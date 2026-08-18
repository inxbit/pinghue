"""History-cell rendering."""

from collections.abc import Sequence

from pinghue.models import ProbeSample, SampleStatus

BAR_BUCKETS: tuple[tuple[float, str], ...] = (
    (1.0, "▁"),
    (3.0, "▂"),
    (10.0, "▃"),
    (30.0, "▄"),
    (100.0, "▅"),
    (300.0, "▆"),
    (1000.0, "▇"),
)


def history_symbol(sample: ProbeSample) -> str:
    """Return the single-character history glyph for a sample."""
    if sample.status == SampleStatus.REFUSED:
        return "!"

    if sample.status != SampleStatus.OK or sample.latency_ms is None:
        return "·"

    for limit, symbol in BAR_BUCKETS:
        if sample.latency_ms <= limit:
            return symbol

    return "█"


def visible_history_samples(samples: Sequence[ProbeSample], *, width: int) -> list[ProbeSample]:
    """Return the visible tail of probe samples."""
    if width <= 0:
        return []
    return list(samples)[-width:]


def render_history(samples: Sequence[ProbeSample], *, width: int, style: str) -> str:
    """Render the visible tail of a target's probe history."""
    if style == "none" or width <= 0:
        return ""

    visible = visible_history_samples(samples, width=width)

    if style == "dots":
        return "".join(
            "•" if sample.status == SampleStatus.OK else history_symbol(sample)
            for sample in visible
        )

    # "bar" and its alias "sparkline" share the fixed-scale block glyphs.
    return "".join(history_symbol(sample) for sample in visible)
