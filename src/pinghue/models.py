"""Core data types and state classification."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import cast, overload

MAX_TARGET_SAMPLES = 1_000


class StringEnum(str, Enum):
    """Enum that serializes as its value."""

    def __str__(self) -> str:
        return cast(str, self.value)


class ProbeMode(StringEnum):
    ICMP = "icmp"
    TCP = "tcp"


class AddressFamily(StringEnum):
    AUTO = "auto"
    IPV4 = "ipv4"
    IPV6 = "ipv6"


class SampleStatus(StringEnum):
    OK = "ok"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    REFUSED = "refused"
    ERROR = "error"


class TargetStatus(StringEnum):
    RESOLVING = "resolving"
    HEALTHY = "healthy"
    INTERMITTENT = "intermittent"
    DOWN = "down"
    DNS_FAILURE = "dns_failure"
    PERMISSION_DENIED = "permission_denied"
    ERROR = "error"


@dataclass(frozen=True)
class ProbeConfig:
    mode: ProbeMode
    port: int | None
    interval_s: float
    timeout_s: float
    address_family: AddressFamily


@dataclass(frozen=True)
class ProbeSample:
    timestamp: datetime
    latency_ms: float | None
    status: SampleStatus
    error: str | None = None


@dataclass(frozen=True)
class SummaryStats:
    sent: int
    received: int
    loss_pct: float
    min_ms: float | None
    avg_ms: float | None
    max_ms: float | None
    jitter_ms: float | None


class _RunningSampleStats:
    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.sent = 0
        self.received = 0
        self.latency_mean = 0.0
        self.latency_m2 = 0.0
        self.latency_min: float | None = None
        self.latency_max: float | None = None

    def add(self, sample: ProbeSample) -> None:
        self.sent += 1
        if sample.status != SampleStatus.OK or sample.latency_ms is None:
            return

        latency = sample.latency_ms
        self.received += 1
        delta = latency - self.latency_mean
        self.latency_mean += delta / self.received
        self.latency_m2 += delta * (latency - self.latency_mean)
        self.latency_min = (
            latency if self.latency_min is None else min(self.latency_min, latency)
        )
        self.latency_max = (
            latency if self.latency_max is None else max(self.latency_max, latency)
        )

    def summary(self) -> SummaryStats:
        sent = self.sent
        received = self.received
        loss_pct = round(((sent - received) / sent * 100), 2) if sent else 0.0

        if not received:
            return SummaryStats(
                sent=sent,
                received=received,
                loss_pct=loss_pct,
                min_ms=None,
                avg_ms=None,
                max_ms=None,
                jitter_ms=None,
            )

        if self.latency_min is None or self.latency_max is None:
            return SummaryStats(
                sent=sent,
                received=received,
                loss_pct=loss_pct,
                min_ms=None,
                avg_ms=None,
                max_ms=None,
                jitter_ms=None,
            )

        jitter_ms = None
        if received >= 2:
            variance = self.latency_m2 / (received - 1)
            jitter_ms = round(math.sqrt(max(0.0, variance)), 2)

        return SummaryStats(
            sent=sent,
            received=received,
            loss_pct=loss_pct,
            min_ms=round(self.latency_min, 2),
            avg_ms=round(self.latency_mean, 2),
            max_ms=round(self.latency_max, 2),
            jitter_ms=jitter_ms,
        )


class SampleWindow(Sequence[ProbeSample]):
    def __init__(
        self,
        samples: Iterable[ProbeSample] = (),
        *,
        maxlen: int = MAX_TARGET_SAMPLES,
    ) -> None:
        self.maxlen = maxlen
        self._items: deque[ProbeSample] = deque()
        self._stats = _RunningSampleStats()
        for sample in samples:
            self.append(sample)

    def append(self, sample: ProbeSample) -> None:
        if len(self._items) >= self.maxlen:
            self._items.popleft()
        self._items.append(sample)
        self._stats.add(sample)

    def clear(self) -> None:
        self._items.clear()
        self._stats.clear()

    def summary(self) -> SummaryStats:
        return self._stats.summary()

    def __iter__(self) -> Iterator[ProbeSample]:
        return iter(self._items)

    def __reversed__(self) -> Iterator[ProbeSample]:
        return reversed(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @overload
    def __getitem__(self, index: int) -> ProbeSample: ...

    @overload
    def __getitem__(self, index: slice) -> list[ProbeSample]: ...

    def __getitem__(self, index: int | slice) -> ProbeSample | list[ProbeSample]:
        if isinstance(index, slice):
            return list(self._items)[index]
        return self._items[index]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SampleWindow):
            return list(self) == list(other)
        if isinstance(other, Sequence):
            return list(self) == list(other)
        return False


@dataclass
class TargetRun:
    target: str
    resolved_address: str | None = None
    resolved_family: AddressFamily | None = None
    resolved_addresses: tuple[str, ...] = ()
    status: TargetStatus = TargetStatus.ERROR
    error: str | None = None
    samples: SampleWindow = field(default_factory=SampleWindow)

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.samples), SampleWindow):
            self.samples = SampleWindow(cast(Iterable[ProbeSample], self.samples))

    @property
    def stats(self) -> SummaryStats:
        return summarize_samples(self.samples)

    def apply_sample(
        self,
        sample: ProbeSample,
        *,
        fail_threshold: int,
        jitter_threshold_ms: float,
    ) -> None:
        """Append a probe sample and refresh derived target state."""
        self.samples.append(sample)
        if (
            sample.status == SampleStatus.ERROR
            and sample.error
            and "permission" in sample.error.lower()
        ):
            self.status = TargetStatus.PERMISSION_DENIED
            self.error = sample.error
            return

        self.status = classify_samples(
            self.samples,
            fail_threshold=fail_threshold,
            jitter_threshold_ms=jitter_threshold_ms,
        )
        self.error = (
            sample.error if self.status in {TargetStatus.DOWN, TargetStatus.ERROR} else None
        )


def summarize_samples(samples: Sequence[ProbeSample]) -> SummaryStats:
    """Return packet and latency statistics for a target."""
    if isinstance(samples, SampleWindow):
        return samples.summary()

    stats = _RunningSampleStats()
    for sample in samples:
        stats.add(sample)
    return stats.summary()


def classify_samples(
    samples: Sequence[ProbeSample],
    *,
    fail_threshold: int,
    jitter_threshold_ms: float,
) -> TargetStatus:
    """Classify recent probe samples into a final target state."""
    if not samples:
        return TargetStatus.DOWN

    tail = samples[-fail_threshold:] if fail_threshold > 0 else samples[-1:]
    if len(tail) >= fail_threshold and all(sample.status != SampleStatus.OK for sample in tail):
        return TargetStatus.DOWN

    summary = summarize_samples(samples)
    if summary.loss_pct > 0:
        return TargetStatus.INTERMITTENT

    if summary.jitter_ms is not None and summary.jitter_ms > jitter_threshold_ms:
        return TargetStatus.INTERMITTENT

    return TargetStatus.HEALTHY
