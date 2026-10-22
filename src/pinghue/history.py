"""History-cell rendering."""

from bisect import bisect_left
from collections.abc import Sequence
from itertools import islice

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
_BAR_LIMITS = tuple(limit for limit, _ in BAR_BUCKETS)
_BAR_GLYPHS = "".join(symbol for _, symbol in BAR_BUCKETS) + "█"

# One style code per history glyph; the UI maps them to palette colors.
HISTORY_OK = "g"
HISTORY_SLOW = "a"
HISTORY_FAILED = "r"


def visible_history_samples(samples: Sequence[ProbeSample], *, width: int) -> list[ProbeSample]:
    """Return the visible tail of probe samples without copying the whole history."""
    count = min(width, len(samples))
    if count <= 0:
        return []
    tail = list(islice(reversed(samples), count))
    tail.reverse()
    return tail


def render_history_parts(
    samples: Sequence[ProbeSample],
    *,
    width: int,
    style: str,
    slow_latency_ms: float,
) -> tuple[str, str]:
    """Return the visible history glyphs and one style code per glyph.

    Codes are HISTORY_OK, HISTORY_SLOW (slow reply or TCP refused) and
    HISTORY_FAILED (timeout, loss or probe error).
    """
    if style == "none" or width <= 0:
        return "", ""

    dots = style == "dots"
    glyphs: list[str] = []
    codes: list[str] = []
    for sample in visible_history_samples(samples, width=width):
        status = sample.status
        latency = sample.latency_ms
        if status == SampleStatus.REFUSED:
            glyphs.append("!")
            codes.append(HISTORY_SLOW)
        elif status != SampleStatus.OK or latency is None:
            glyphs.append("·")
            codes.append(HISTORY_FAILED)
        else:
            # "bar" and its alias "sparkline" share the fixed-scale block glyphs.
            glyphs.append("•" if dots else _BAR_GLYPHS[bisect_left(_BAR_LIMITS, latency)])
            codes.append(HISTORY_SLOW if latency > slow_latency_ms else HISTORY_OK)

    return "".join(glyphs), "".join(codes)


def render_history(samples: Sequence[ProbeSample], *, width: int, style: str) -> str:
    """Render the visible tail of a target's probe history."""
    return render_history_parts(samples, width=width, style=style, slow_latency_ms=0.0)[0]
