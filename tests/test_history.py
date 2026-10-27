from datetime import UTC, datetime

from pinghue.history import render_history
from pinghue.models import ProbeSample, SampleStatus


def make_sample(status: SampleStatus, latency_ms: float | None = None) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=UTC),
        latency_ms=latency_ms,
        status=status,
        error=None,
    )


def test_render_history_uses_fixed_latency_buckets() -> None:
    latencies = [0.5, 1.0, 2.0, 9.0, 20.0, 75.0, 200.0, 700.0, 1000.0, 1000.01, 1200.0]
    samples = [make_sample(SampleStatus.OK, latency) for latency in latencies]

    assert render_history(samples, width=len(samples), style="bar") == "▁▁▂▃▄▅▆▇▇██"


def test_render_history_uses_distinct_failure_glyphs() -> None:
    samples = [
        make_sample(SampleStatus.TIMEOUT),
        make_sample(SampleStatus.REFUSED),
        make_sample(SampleStatus.ERROR),
    ]

    assert render_history(samples, width=3, style="bar") == "·!·"


def test_render_history_uses_visible_width_from_tail() -> None:
    samples = [
        make_sample(SampleStatus.OK, 1.0),
        make_sample(SampleStatus.OK, 10.0),
        make_sample(SampleStatus.TIMEOUT),
        make_sample(SampleStatus.OK, 1000.0),
    ]

    assert render_history(samples, width=3, style="bar") == "▃·▇"
    assert render_history(samples, width=3, style="none") == ""


def test_simplified_history_styles_keep_tcp_refused_distinct() -> None:
    samples = [
        make_sample(SampleStatus.OK, 10.0),
        make_sample(SampleStatus.REFUSED),
        make_sample(SampleStatus.TIMEOUT),
    ]

    assert render_history(samples, width=3, style="dots") == "•!·"
    assert render_history(samples, width=3, style="sparkline") == "▃!·"


def test_render_history_accepts_bounded_sample_history() -> None:
    from pinghue.models import SampleWindow

    samples = SampleWindow(maxlen=3)
    for index in range(5):
        samples.append(make_sample(SampleStatus.OK, float(index + 1)))

    assert render_history(samples, width=3, style="bar") == "▂▃▃"
