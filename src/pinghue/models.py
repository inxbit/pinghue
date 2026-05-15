"""Core data types and state classification."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import cast


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


@dataclass
class TargetRun:
    target: str
    resolved_address: str | None = None
    resolved_family: AddressFamily | None = None
    resolved_addresses: tuple[str, ...] = ()
    status: TargetStatus = TargetStatus.ERROR
    error: str | None = None
    samples: list[ProbeSample] = field(default_factory=list)

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


def summarize_samples(samples: list[ProbeSample]) -> SummaryStats:
    """Return packet and latency statistics for a target."""
    sent = len(samples)
    successful_latencies = [
        sample.latency_ms
        for sample in samples
        if sample.status == SampleStatus.OK and sample.latency_ms is not None
    ]
    received = len(successful_latencies)
    loss_pct = round(((sent - received) / sent * 100), 2) if sent else 0.0

    if not successful_latencies:
        return SummaryStats(
            sent=sent,
            received=received,
            loss_pct=loss_pct,
            min_ms=None,
            avg_ms=None,
            max_ms=None,
            jitter_ms=None,
        )

    jitter_ms = (
        round(statistics.stdev(successful_latencies), 2)
        if len(successful_latencies) >= 2
        else None
    )

    return SummaryStats(
        sent=sent,
        received=received,
        loss_pct=loss_pct,
        min_ms=round(min(successful_latencies), 2),
        avg_ms=round(sum(successful_latencies) / received, 2),
        max_ms=round(max(successful_latencies), 2),
        jitter_ms=jitter_ms,
    )


def classify_samples(
    samples: list[ProbeSample],
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
