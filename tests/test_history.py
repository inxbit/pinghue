from datetime import datetime, timezone

from pinghue.history import history_symbol, render_history
from pinghue.models import ProbeSample, SampleStatus


def make_sample(status: SampleStatus, latency_ms: float | None = None) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=latency_ms,
        status=status,
        error=None,
    )


def test_history_symbol_uses_fixed_latency_buckets() -> None:
    assert history_symbol(make_sample(SampleStatus.OK, 0.5)) == "▁"
    assert history_symbol(make_sample(SampleStatus.OK, 2.0)) == "▂"
    assert history_symbol(make_sample(SampleStatus.OK, 9.0)) == "▃"
    assert history_symbol(make_sample(SampleStatus.OK, 20.0)) == "▄"
    assert history_symbol(make_sample(SampleStatus.OK, 75.0)) == "▅"
    assert history_symbol(make_sample(SampleStatus.OK, 200.0)) == "▆"
    assert history_symbol(make_sample(SampleStatus.OK, 700.0)) == "▇"
    assert history_symbol(make_sample(SampleStatus.OK, 1200.0)) == "█"


def test_history_symbol_uses_distinct_failure_glyphs() -> None:
    assert history_symbol(make_sample(SampleStatus.TIMEOUT)) == "·"
    assert history_symbol(make_sample(SampleStatus.REFUSED)) == "!"


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
