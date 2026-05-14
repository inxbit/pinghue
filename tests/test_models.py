from datetime import datetime, timezone

from pinghue.models import (
    ProbeSample,
    SampleStatus,
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
    assert summary.jitter_ms == 7.07


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
