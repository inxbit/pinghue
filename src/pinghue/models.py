"""Core data types and state classification."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import cast, overload

MAX_TARGET_SAMPLES = 1_000
MAX_TOTAL_RETAINED_SAMPLES = 100_000


def retained_samples_per_target(target_count: int) -> int:
    """Return a uniform per-target window within the aggregate sample budget."""
    if target_count <= 0:
        return MAX_TARGET_SAMPLES
    return min(MAX_TARGET_SAMPLES, max(1, MAX_TOTAL_RETAINED_SAMPLES // target_count))


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
        self.consecutive_failures = 0
        self.latency_mean = 0.0
        self.latency_jitter = 0.0
        self.latency_jitter_max = 0.0
        self.latency_previous: float | None = None
        self.latency_min: float | None = None
        self.latency_max: float | None = None

    def add(self, sample: ProbeSample) -> None:
        self.sent += 1
        if sample.status == SampleStatus.OK:
            self.consecutive_failures = 0
        else:
            self.consecutive_failures += 1
        if sample.status != SampleStatus.OK or sample.latency_ms is None:
            return

        latency = sample.latency_ms
        self.received += 1
        delta = latency - self.latency_mean
        self.latency_mean += delta / self.received
        # RFC 3550 interarrival jitter: smoothed mean absolute difference between
        # consecutive received latencies, J += (|D| - J) / 16.
        if self.latency_previous is not None:
            difference = abs(latency - self.latency_previous)
            self.latency_jitter += (difference - self.latency_jitter) / 16.0
            self.latency_jitter_max = max(
                self.latency_jitter_max,
                self.latency_jitter,
            )
        self.latency_previous = latency
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

        jitter = self.jitter_ms()
        jitter_ms = round(jitter, 2) if jitter is not None else None

        return SummaryStats(
            sent=sent,
            received=received,
            loss_pct=loss_pct,
            min_ms=round(self.latency_min, 2),
            avg_ms=round(self.latency_mean, 2),
            max_ms=round(self.latency_max, 2),
            jitter_ms=jitter_ms,
        )

    def jitter_ms(self) -> float | None:
        """Return the current unrounded RFC 3550 jitter estimate."""
        return self.latency_jitter if self.received >= 2 else None

    def maximum_jitter_ms(self) -> float | None:
        """Return the peak unrounded jitter observed during this run."""
        return self.latency_jitter_max if self.received >= 2 else None


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

    @property
    def consecutive_failures(self) -> int:
        return self._stats.consecutive_failures

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
    _last_resolve_time: float | None = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

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

    if isinstance(samples, SampleWindow):
        running_stats = samples._stats
    else:
        running_stats = _RunningSampleStats()
        for sample in samples:
            running_stats.add(sample)

    effective_fail_threshold = max(1, fail_threshold)
    failure_threshold_reached = (
        running_stats.consecutive_failures >= effective_fail_threshold
    )
    if failure_threshold_reached:
        return TargetStatus.DOWN

    summary = running_stats.summary()
    # A window with no successful samples is down, even when the run is shorter
    # than fail_threshold (so the consecutive-failure check above cannot fire).
    # INTERMITTENT requires at least one successful response.
    if summary.received == 0:
        return TargetStatus.DOWN

    if summary.received < summary.sent:
        return TargetStatus.INTERMITTENT

    jitter_ms = running_stats.maximum_jitter_ms()
    if jitter_ms is not None and jitter_ms > jitter_threshold_ms:
        return TargetStatus.INTERMITTENT

    return TargetStatus.HEALTHY
