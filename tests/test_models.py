from datetime import datetime, timezone

from pinghue.models import (
    MAX_TARGET_SAMPLES,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
    classify_samples,
    summarize_samples,
)


def sample(status: SampleStatus, latency_ms: float | None = None) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=latency_ms,
        status=status,
        error=None,
    )


def test_summarize_samples_calculates_loss_and_jitter() -> None:
    summary = summarize_samples(
        [
            sample(SampleStatus.OK, 10.0),
            sample(SampleStatus.OK, 20.0),
            sample(SampleStatus.TIMEOUT),
        ]
    )

    assert summary.sent == 3
    assert summary.received == 2
    assert summary.loss_pct == 33.33
    assert summary.min_ms == 10.0
    assert summary.avg_ms == 15.0
    assert summary.max_ms == 20.0
    # RFC 3550 inter-packet jitter: J += (|D| - J) / 16; for [10, 20] -> 0.62.
    assert summary.jitter_ms == 0.62


def test_summarize_samples_uses_rfc3550_interpacket_jitter() -> None:
    summary = summarize_samples(
        [
            sample(SampleStatus.OK, 10.0),
            sample(SampleStatus.OK, 20.0),
            sample(SampleStatus.OK, 50.0),
        ]
    )

    assert summary.jitter_ms == 2.46


def test_classify_samples_marks_down_when_all_failed_below_threshold() -> None:
    # H1: a host that never responded is DOWN even when the run is shorter than
    # fail_threshold, so it cannot be misreported as INTERMITTENT (and exit 0).
    status = classify_samples(
        [sample(SampleStatus.TIMEOUT), sample(SampleStatus.TIMEOUT)],
        fail_threshold=3,
        jitter_threshold_ms=50.0,
    )

    assert status == TargetStatus.DOWN


def test_classify_samples_marks_down_after_failure_threshold() -> None:
    status = classify_samples(
        [
            sample(SampleStatus.OK, 10.0),
            sample(SampleStatus.TIMEOUT),
            sample(SampleStatus.UNREACHABLE),
            sample(SampleStatus.TIMEOUT),
        ],
        fail_threshold=3,
        jitter_threshold_ms=50.0,
    )

    assert status == TargetStatus.DOWN


def test_classify_samples_marks_intermitent_when_any_loss_without_down() -> None:
    status = classify_samples(
        [
            sample(SampleStatus.OK, 10.0),
            sample(SampleStatus.TIMEOUT),
            sample(SampleStatus.OK, 12.0),
        ],
        fail_threshold=3,
        jitter_threshold_ms=50.0,
    )

    assert status == TargetStatus.INTERMITTENT


def test_target_run_apply_sample_keeps_permission_denied_invariant() -> None:
    target = TargetRun("1.1.1.1")
    denied = ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=None,
        status=SampleStatus.ERROR,
        error="permission denied: socket",
    )

    target.apply_sample(denied, fail_threshold=3, jitter_threshold_ms=50.0)

    assert target.samples == [denied]
    assert target.status == TargetStatus.PERMISSION_DENIED
    assert target.error == "permission denied: socket"


def test_target_run_apply_sample_classifies_regular_samples() -> None:
    target = TargetRun("1.1.1.1")

    target.apply_sample(sample(SampleStatus.OK, 10.0), fail_threshold=3, jitter_threshold_ms=50.0)

    assert target.status == TargetStatus.HEALTHY
    assert target.error is None


def test_target_run_caps_sample_history_and_keeps_lifetime_stats_current() -> None:
    target = TargetRun("1.1.1.1")

    for index in range(MAX_TARGET_SAMPLES + 5):
        target.apply_sample(
            ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, index % 60, tzinfo=timezone.utc),
                latency_ms=float(index),
                status=SampleStatus.OK,
            ),
            fail_threshold=3,
            jitter_threshold_ms=10_000.0,
        )

    assert len(target.samples) == MAX_TARGET_SAMPLES
    assert target.samples[0].latency_ms == 5.0
    assert target.stats.sent == MAX_TARGET_SAMPLES + 5
    assert target.stats.received == MAX_TARGET_SAMPLES + 5
    assert target.stats.min_ms == 0.0
    assert target.stats.avg_ms == 502.0
    assert target.stats.max_ms == 1004.0


def test_target_sample_clear_resets_cached_statistics() -> None:
    target = TargetRun(
        "1.1.1.1",
        samples=[
            ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
                latency_ms=10.0,
                status=SampleStatus.OK,
            )
        ],
    )

    target.samples.clear()

    assert target.stats.sent == 0
    assert target.stats.received == 0
    assert target.stats.avg_ms is None
