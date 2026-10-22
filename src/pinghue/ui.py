"""UI formatting helpers."""

from __future__ import annotations

import re
import time
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import pairwise
from typing import Any

from rich.text import Span, Text
from textual.geometry import Region, Size
from textual.widgets import DataTable
from textual.widgets.data_table import ColumnKey

from pinghue.display import sanitize_display
from pinghue.history import (
    HISTORY_FAILED,
    HISTORY_OK,
    HISTORY_SLOW,
    render_history_parts,
)
from pinghue.models import (
    ProbeSample,
    SampleStatus,
    SampleWindow,
    SummaryStats,
    TargetRun,
    TargetStatus,
)

GREEN = "#7ee787"
AMBER = "#f2cc60"
RED = "#ff7b72"
TEXT = "#e6edf3"
MUTED = "#8ea0b8"

COLUMN_KEYS = (
    "host",
    "address",
    "state",
    "last",
    "min",
    "avg",
    "max",
    "jitter",
    "loss",
    "mode",
    "history",
)

ColumnWidths = dict[str, int]
# A cell before it becomes a Rich Text: (plain text, style). The history cell
# carries one style code per glyph instead of a single style.
CellValue = tuple[str, str]
RowValues = tuple[CellValue, ...]

MIN_HOST_WIDTH = 12
MAX_HOST_WIDTH = 24
HIDDEN_ADDRESS_WIDTH = len("address")
IPV4_ADDRESS_WIDTH = 15
IPV6_ADDRESS_WIDTH = 39
MIN_HISTORY_WIDTH = 6
# The DataTable's solid border, left and right.
TABLE_CHROME_WIDTH = 2
# Reserved even before the table needs a vertical scrollbar: reserving it only
# when rows overflow would let the column widths flip between refreshes.
SCROLLBAR_WIDTH = 2
# Textual DataTable's default: each column renders this many blank cells on
# each side of its content.
CELL_PADDING = 1
FIXED_COLUMN_WIDTHS = {
    "state": len("intermittent"),
    "last": len("1200.00"),
    "min": len("1200.00"),
    "avg": len("1200.00"),
    "max": len("1200.00"),
    "jitter": len("1200.00"),
    "loss": len("100.00%"),
    "mode": 4,
}
# On narrow terminals these columns collapse to zero width, in this order, so
# the host stays readable and the newest history glyphs stay on screen. The
# address column only collapses while it is the hidden placeholder.
NARROW_COLLAPSE_ORDER = ("address", "mode", "min", "max")
# The hidden address placeholder carries no data and mode is the same on every
# row, so they collapse before host names are shortened at all; min and max
# collapse only to keep host names MIN_HOST_WIDTH wide.
SPARE_COLUMNS = frozenset({"address", "mode"})

ELLIPSIS = "…"
# Host-name labels are kept whole when a long name is shortened.
HOST_NAME_SEPARATORS = ".-_:"
PENDING_LABEL = "pending"
HOST_INDEX = COLUMN_KEYS.index("host")
STATE_INDEX = COLUMN_KEYS.index("state")
HISTORY_INDEX = COLUMN_KEYS.index("history")
_COLUMN_KEY_OBJECTS = tuple(ColumnKey(key) for key in COLUMN_KEYS)
_HISTORY_CODE_STYLES = {HISTORY_OK: GREEN, HISTORY_SLOW: AMBER, HISTORY_FAILED: RED}
_STYLE_RUNS = re.compile(r"(.)\1*", re.DOTALL)

_STATE_LABELS: dict[TargetStatus, CellValue] = {
    TargetStatus.RESOLVING: ("resolving", MUTED),
    TargetStatus.HEALTHY: ("healthy", GREEN),
    TargetStatus.INTERMITTENT: ("intermittent", AMBER),
    TargetStatus.DOWN: ("down", RED),
    TargetStatus.DNS_FAILURE: ("dns", RED),
    TargetStatus.PERMISSION_DENIED: ("denied", RED),
    TargetStatus.ERROR: ("error", RED),
}

# Fleet-summary categories in display order, with the color of their count:
# problems first and healthy, the bulk of most fleets, last, so a header too
# narrow for the whole summary cuts the least useful part.
SUMMARY_CATEGORIES: tuple[CellValue, ...] = (
    ("down", RED),
    ("dns", RED),
    ("denied", RED),
    ("error", RED),
    ("intermittent", AMBER),
    (PENDING_LABEL, MUTED),
    ("healthy", GREEN),
)

# Row refresh scheduling: rows near the viewport are refreshed on every pass;
# the rest are refreshed round-robin, each call within a time budget. Every
# full pass owes at least MIN_ROWS_PER_PASS and len(targets) /
# FULL_SWEEP_PASSES rows; what its budget leaves over is refreshed in
# catch-up chunks of the same budget ROW_CATCH_UP_DELAY_S apart, so the event
# loop never blocks for much longer than the budget, every changed row is
# refreshed within FULL_SWEEP_PASSES + 1 full passes however large the table,
# and small tables are refreshed whole every pass.
ROW_REFRESH_BUDGET_S = 0.02
ROW_CATCH_UP_DELAY_S = 0.02
FULL_SWEEP_PASSES = 8
MIN_ROWS_PER_PASS = 256
VIEWPORT_MARGIN_ROWS = 4

_NO_SAMPLE = datetime.max.replace(tzinfo=timezone.utc)


def issue_styles(
    target: TargetRun,
    *,
    jitter_threshold_ms: float,
    slow_latency_ms: float,
    stats: SummaryStats | None = None,
) -> dict[str, str]:
    """Return per-column styles for values that need attention."""
    if stats is None:
        stats = target.stats
    styles = {
        "state": GREEN,
        "last": TEXT,
        "min": TEXT,
        "avg": TEXT,
        "max": TEXT,
        "jitter": TEXT,
        "loss": TEXT,
    }

    if target.status == TargetStatus.INTERMITTENT:
        styles["state"] = AMBER
    elif target.status != TargetStatus.HEALTHY:
        styles["state"] = RED

    if stats.received < stats.sent:
        styles["loss"] = RED

    if stats.jitter_ms is not None and stats.jitter_ms > jitter_threshold_ms:
        styles["jitter"] = AMBER

    latest = target.samples[-1] if target.samples else None
    if latest and latest.status != SampleStatus.OK:
        styles["last"] = RED
    elif latest and latest.latency_ms is not None and latest.latency_ms > slow_latency_ms:
        styles["last"] = AMBER

    if stats.avg_ms is not None and stats.avg_ms > slow_latency_ms:
        styles["avg"] = AMBER

    if stats.max_ms is not None and stats.max_ms > slow_latency_ms:
        styles["max"] = AMBER

    return styles


def compact_ms(value: float | None) -> str:
    """Return a latency value sized for dense terminal columns."""
    if value is None:
        return "-"
    return f"{value:.2f}"


def compact_loss(value: float) -> str:
    """Return a loss percentage sized for dense terminal columns."""
    return f"{value:.2f}%"


def truncate_middle(value: str, width: int) -> str:
    """Shorten text to ``width`` with a middle ellipsis that keeps most of the tail.

    Without the rest of the fleet to compare against, the end of a host name
    is the likeliest part to tell it apart, so the tail gets three quarters
    of the space.
    """
    if len(value) <= width:
        return value
    if width <= 1:
        return ELLIPSIS[: max(width, 0)]
    head = (width - 1) // 4
    tail = width - 1 - head
    return f"{value[:head]}{ELLIPSIS}{value[-tail:]}"


def _shared_prefix_length(left: str, right: str) -> int:
    length = min(len(left), len(right))
    for index in range(length):
        if left[index] != right[index]:
            return index
    return length


def host_name_spans(names: Sequence[str]) -> list[tuple[int, int]]:
    """Return the ``(start, end)`` span of each name that tells it apart.

    The span runs from the first character a name does not share with the
    most similar name from the start to the last one it does not share with
    the most similar name from the end (its neighbors in sorted order, so
    this is O(N log N)), widened to whole labels between HOST_NAME_SEPARATORS:
    ``001`` in ``db-primary-001.dc1.example.net``. A fleet usually shares a
    domain suffix, so the end of a name alone does not tell hosts apart.
    """
    count = len(names)
    shared_head = [0] * count
    shared_tail = [0] * count
    for shared, keys in ((shared_head, names), (shared_tail, [name[::-1] for name in names])):
        order = sorted(range(count), key=keys.__getitem__)
        for left, right in pairwise(order):
            length = _shared_prefix_length(keys[left], keys[right])
            shared[left] = max(shared[left], length)
            shared[right] = max(shared[right], length)

    spans: list[tuple[int, int]] = []
    for name, head, tail in zip(names, shared_head, shared_tail, strict=True):
        size = len(name)
        start = max(0, min(head, size - tail - 1))
        end = min(size, max(head + 1, size - tail))
        while start > 0 and name[start - 1] not in HOST_NAME_SEPARATORS:
            start -= 1
        while end < size and name[end] not in HOST_NAME_SEPARATORS:
            end += 1
        spans.append((start, end))
    return spans


def fit_host_name(name: str, width: int, span: tuple[int, int]) -> str:
    """Shorten a display-safe host name to ``width`` and keep ``span`` whole.

    The rest of the room goes to the start of the name, then to what follows
    the span, with ``…`` at each cut: ``db-primary-001.dc1.exam…``. When the
    span itself does not fit, this falls back to truncate_middle.
    """
    size = len(name)
    if size <= width:
        return name
    start, end = span
    core = name[start:end]
    if len(core) + (start > 0) + (end < size) > width:
        return truncate_middle(name, width)

    room = width - len(core)
    # One cell stays for whatever follows the span, or the cut there.
    tail_reserve = min(size - end, 1)
    if start <= room - tail_reserve:
        head = name[:start]
    else:
        head = name[: room - tail_reserve - 1] + ELLIPSIS
    room -= len(head)
    tail = name[end:]
    if len(tail) > room:
        tail = name[end : end + room - 1] + ELLIPSIS
    return head + core + tail


def display_host_names(names: Sequence[str]) -> tuple[list[str], list[tuple[int, int]]]:
    """Return each host name escaped for display, and the span of the escaped
    name that tells it apart from the rest of the fleet (see host_name_spans).

    Every host label the TUI draws comes from here: names are escaped first,
    so shortening one with fit_host_labels can never bring back a control
    character.
    """
    safe_names = [sanitize_display(name) for name in names]
    return safe_names, host_name_spans(safe_names)


def fit_host_labels(
    safe_names: Sequence[str], spans: Sequence[tuple[int, int]], width: int | None
) -> list[str]:
    """Return display_host_names output as labels at most ``width`` wide that
    keep the part of each name that tells it apart from the rest of the fleet."""
    if width is None:
        return list(safe_names)
    return [fit_host_name(name, width, span) for name, span in zip(safe_names, spans, strict=True)]


def target_state(target: TargetRun) -> CellValue:
    """Return the TUI state label and style for a target.

    A target that has an address but is still ``resolving`` (just resolved,
    or reset, with no probe result yet) is ``pending``: there is no evidence
    about it yet. JSON output keeps the underlying status.
    """
    status = target.status
    if status == TargetStatus.RESOLVING and target.resolved_address:
        return PENDING_LABEL, MUTED
    return _STATE_LABELS.get(status, (status.value, RED))


def history_text(glyphs: str, codes: str) -> Text:
    """Return a history cell with one span per run of equally styled glyphs."""
    return Text(
        glyphs,
        spans=[
            Span(run.start(), run.end(), _HISTORY_CODE_STYLES[run.group(1)])
            for run in _STYLE_RUNS.finditer(codes)
        ],
    )


def format_history_legend(style: str = "bar") -> Text:
    """Return the bottom legend for history glyphs."""
    if style == "none":
        return Text("")

    text = Text("history: ", style=MUTED)
    if style == "dots":
        text.append("• ", style=GREEN)
    else:
        text.append("▁▂▃ ", style=GREEN)
    text.append("ok  ", style=MUTED)
    if style == "dots":
        text.append("• ", style=AMBER)
    else:
        text.append("▆▇█ ", style=AMBER)
    text.append("slow  ", style=MUTED)
    text.append("·", style=RED)
    text.append(" loss/down  ", style=MUTED)
    text.append("!", style=AMBER)
    text.append(" tcp refused", style=MUTED)
    return text


def focus_table(table: Any) -> Any:
    """Restore focus to the target table and return it.

    Widget.focus() repaints the whole widget even when it already has focus,
    and the app calls this on every arrow key, so a focused table is left alone.
    """
    if not getattr(table, "has_focus", False):
        table.focus()
    return table


def target_row_key(index: int) -> str:
    """Return the stable table row key for a target index."""
    return f"target-{index}"


def _last_latency(samples: Sequence[ProbeSample]) -> float | None:
    for sample in reversed(samples):
        if sample.latency_ms is not None:
            return sample.latency_ms
    return None


def _address_value(target: TargetRun, show_address: bool) -> CellValue:
    address = (target.resolved_address or "") if show_address else ""
    return sanitize_display(address), GREEN if address else MUTED


def _metric_values(
    target: TargetRun,
    *,
    jitter_threshold_ms: float,
    slow_latency_ms: float,
) -> tuple[CellValue, ...]:
    """Return the state through loss cells, computing the stats once."""
    samples = target.samples
    stats = target.stats
    styles = issue_styles(
        target,
        jitter_threshold_ms=jitter_threshold_ms,
        slow_latency_ms=slow_latency_ms,
        stats=stats,
    )
    return (
        target_state(target),
        (compact_ms(_last_latency(samples)), styles["last"]),
        (compact_ms(stats.min_ms), styles["min"]),
        (compact_ms(stats.avg_ms), styles["avg"]),
        (compact_ms(stats.max_ms), styles["max"]),
        (compact_ms(stats.jitter_ms), styles["jitter"]),
        (compact_loss(stats.loss_pct), styles["loss"]),
    )


def _cell_text(column_index: int, value: CellValue) -> Text:
    if column_index == HISTORY_INDEX:
        return history_text(*value)
    return Text(value[0], style=value[1])


def _uses_ipv6(targets: Sequence[TargetRun]) -> bool:
    return any((target.resolved_address and ":" in target.resolved_address) for target in targets)


def _target_width(targets: Sequence[TargetRun]) -> int:
    longest = max((len(target.target) for target in targets), default=len("host"))
    return min(max(longest, MIN_HOST_WIDTH), MAX_HOST_WIDTH)


def compute_table_layout(
    *,
    width: int,
    targets: Sequence[TargetRun],
    show_address: bool,
    cell_padding: int = CELL_PADDING,
    scrollbar_width: int = SCROLLBAR_WIDTH,
) -> ColumnWidths:
    """Return DataTable column widths for a table ``width`` cells wide.

    Every column also renders ``2 * cell_padding`` blank cells, and the border
    and the vertical scrollbar are always reserved, so the widths fit without
    horizontal scrolling. As the terminal narrows, history shrinks to
    MIN_HISTORY_WIDTH first, then the SPARE_COLUMNS collapse, then the host
    column shrinks to MIN_HOST_WIDTH and min and max collapse; below that
    history goes and the table scrolls horizontally instead of squeezing host
    names unreadable.
    """
    layout = dict(FIXED_COLUMN_WIDTHS)
    if not show_address:
        layout["address"] = HIDDEN_ADDRESS_WIDTH
    elif _uses_ipv6(targets):
        layout["address"] = IPV6_ADDRESS_WIDTH
    else:
        layout["address"] = IPV4_ADDRESS_WIDTH

    padding = len(COLUMN_KEYS) * 2 * max(cell_padding, 0)
    available = width - TABLE_CHROME_WIDTH - scrollbar_width - padding
    preferred_host_width = _target_width(targets)
    flexible = available - sum(layout.values())
    for key in NARROW_COLLAPSE_ORDER:
        if key == "address" and show_address:
            continue
        kept_host_width = preferred_host_width if key in SPARE_COLUMNS else MIN_HOST_WIDTH
        if flexible < kept_host_width + MIN_HISTORY_WIDTH:
            layout[key] = 0
            flexible = available - sum(layout.values())

    host_width = max(MIN_HOST_WIDTH, min(preferred_host_width, flexible - MIN_HISTORY_WIDTH))
    layout["host"] = host_width
    layout["history"] = max(0, flexible - host_width)
    return {key: layout[key] for key in COLUMN_KEYS}


def refresh_table_geometry(table: Any) -> None:
    """Recompute a DataTable's scroll extent and drop its width-dependent caches.

    Textual's DataTable (8.2) recomputes ``virtual_size`` only when rows or
    columns are added or a cell is re-measured, and its cell, row and line
    render caches are not keyed by column width. After ``Column.width``
    changes, the table would keep its startup scroll extent (so the newest
    history glyphs become unreachable when the terminal grows) and could keep
    painting the old widths. tests/test_ui_rendering.py pins this behavior so
    a Textual upgrade that changes it fails loudly.
    """
    columns = getattr(table, "columns", None)
    virtual_size = getattr(table, "virtual_size", None)
    if columns is None or not isinstance(virtual_size, Size):
        return

    content_width = sum(column.get_render_width(table) for column in columns.values())
    table.virtual_size = Size(content_width, virtual_size.height)
    # DataTable.notify_style_update clears the render caches and repaints.
    table.notify_style_update()
    if table.scroll_x > table.max_scroll_x:
        table.scroll_to(x=table.max_scroll_x, animate=False)


def apply_column_widths(table: Any, widths: ColumnWidths) -> bool:
    """Apply column widths to an existing Textual DataTable.

    Returns whether any width changed.
    """
    changed = False
    for column_key, width in widths.items():
        # DataTable keys compare equal to their string value, so a plain
        # string lookup works against the real widget and simple fakes alike.
        column = table.columns.get(column_key)
        if column is None or column.width == width:
            continue

        column.width = width
        changed = True

    if changed:
        refresh_table_geometry(table)
    return changed


@dataclass(frozen=True)
class FleetSummary:
    """Host counts per displayed state, and how stale the stalest result is."""

    counts: dict[str, int]
    # Among targets still being probed (at least one sample, and with
    # --count, probes left), the age of the oldest "newest sample". Well above
    # the probe interval means probes are falling behind.
    oldest_result_age_s: float | None


def _summary_category(label: str) -> str:
    return PENDING_LABEL if label == "resolving" else label


def format_age(seconds: float) -> str:
    """Return a short age such as 0.8s, 12s, 3m or 5h."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 120:
        return f"{seconds:.0f}s"
    if seconds < 7200:
        return f"{seconds / 60:.0f}m"
    return f"{seconds / 3600:.0f}h"


def fleet_summary_parts(summary: FleetSummary, *, stale_after_s: float) -> list[CellValue]:
    """Return (text, style) parts of the always-visible fleet summary.

    The result age comes first, so a narrow header never cuts it, and turns
    amber once it exceeds ``stale_after_s``; states with no hosts are left out.
    """
    parts: list[CellValue] = []
    age = summary.oldest_result_age_s
    if age is not None:
        parts.append(("oldest ", MUTED))
        parts.append((format_age(age), AMBER if age > stale_after_s else MUTED))

    for category, style in SUMMARY_CATEGORIES:
        count = summary.counts.get(category, 0)
        if not count:
            continue
        if parts:
            parts.append(("  ", MUTED))
        parts.append((str(count), style))
        parts.append((f" {category}", MUTED))
    return parts


class TargetTable(DataTable[Any]):
    """The targets DataTable, repainting only the rows a refresh changed.

    ``DataTable.update_cell`` asks for a whole-table repaint on every call:
    the request costs several times more than the update, and repainting
    every visible cell (about 100 us each) dominates the cost of a refresh.
    Inside ``batch_updates()`` those requests are dropped and the caller
    repaints the rows it changed with ``refresh_rows()``.
    """

    _batch_depth = 0

    @contextmanager
    def batch_updates(self) -> Iterator[None]:
        """Drop update_cell's whole-table repaint requests made in the block."""
        self._batch_depth += 1
        try:
            yield
        finally:
            self._batch_depth -= 1

    def refresh(
        self,
        *regions: Region,
        repaint: bool = True,
        layout: bool = False,
        recompose: bool = False,
    ) -> TargetTable:
        if self._batch_depth and not regions and repaint and not layout and not recompose:
            return self
        super().refresh(*regions, repaint=repaint, layout=layout, recompose=recompose)
        return self

    def refresh_rows(self, indices: Iterable[int]) -> None:
        """Repaint the given rows where they are on screen.

        Rows are one line high (rows are added with the default height) and
        there are no fixed rows, so row ``index`` is virtual line
        ``header + index`` (see DataTable.render_line).
        tests/test_ui_rendering.py pins this against DataTable's own row
        geometry.
        """
        if self.fixed_rows:
            self.refresh()
            return
        header = self.header_height if self.show_header else 0
        top = self.scroll_offset.y
        bottom = top + self.size.height - header
        start = count = 0
        for index in sorted({index for index in indices if top <= index < bottom}):
            if count and index == start + count:
                count += 1
                continue
            if count:
                self.refresh_lines(header + start, count)
            start, count = index, 1
        if count:
            self.refresh_lines(header + start, count)


class _RowState:
    """What a table row currently shows, and the target state it came from."""

    __slots__ = (
        "address",
        "generation",
        "revision",
        "samples",
        "status",
        "target",
        "values",
    )

    def __init__(self) -> None:
        self.target: TargetRun | None = None
        self.samples: SampleWindow | None = None
        self.revision = -1
        self.status: TargetStatus | None = None
        self.address: str | None = None
        self.generation = -1
        self.values: list[CellValue | None] = [None] * len(COLUMN_KEYS)


class TargetTableRenderer:
    """Keep the targets DataTable in sync without rebuilding every row.

    A row is rebuilt only when its target object, sample window or window
    revision, status or address changed (every way the runner and the app
    change a target), or when the layout or the host labels changed, and only
    cells whose text or style changed are sent to the table. Rows near the
    viewport are refreshed first and the rest round-robin within a time
    budget, continued by catch_up(); a changed state label is written at once
    on every full pass, so no state change is held back.
    """

    def __init__(
        self,
        *,
        mode: str,
        history_style: str,
        jitter_threshold_ms: float,
        slow_latency_ms: float,
        probe_count: int | None = None,
        budget_s: float = ROW_REFRESH_BUDGET_S,
        full_sweep_passes: int = FULL_SWEEP_PASSES,
        min_rows_per_pass: int = MIN_ROWS_PER_PASS,
    ) -> None:
        self.mode = mode
        self.history_style = history_style
        self.jitter_threshold_ms = jitter_threshold_ms
        self.slow_latency_ms = slow_latency_ms
        # --count: a target that has sent this many probes is not probed again.
        self.probe_count = probe_count
        self.budget_s = budget_s
        self.full_sweep_passes = max(1, full_sweep_passes)
        self.min_rows_per_pass = min_rows_per_pass
        self._rows: list[_RowState] = []
        self._row_keys: list[Any] = []
        self._categories: list[str] = []
        self._newest: list[datetime] = []
        self._names: list[str] = []
        self._safe_names: list[str] = []
        self._name_spans: list[tuple[int, int]] = []
        self._host_labels: list[str] = []
        self._labels_width: int | None = None
        self._show_address = False
        self._host_width: int | None = None
        self._history_width = 0
        self._generation = 0
        self._cursor = 0
        self._owed = 0

    @property
    def behind(self) -> bool:
        """Whether rows owed by the last full pass are waiting for catch_up()."""
        return self._owed > 0

    def set_layout(self, *, show_address: bool, widths: ColumnWidths) -> None:
        """Record the layout the cells depend on; a change re-renders every row."""
        layout = (show_address, widths["host"], widths["history"])
        if layout != (self._show_address, self._host_width, self._history_width):
            self._show_address, self._host_width, self._history_width = layout
            self._generation += 1

    def sync(
        self,
        table: Any,
        targets: Sequence[TargetRun],
        *,
        priority: range = range(0),
        full: bool = True,
        now: datetime | None = None,
    ) -> FleetSummary | None:
        """Bring the table up to date with ``targets``.

        ``priority`` rows (the viewport) are always refreshed. A ``full`` pass
        also writes changed state labels everywhere, refreshes the other
        changed rows round-robin within the time budget, and returns the
        fleet summary; while ``behind``, catch_up() refreshes the rest of the
        rows the pass owes.
        """
        deadline = time.perf_counter() + self.budget_s
        self._update_host_labels(targets)
        # Rows are added outside the batch: add_row's own repaint request
        # must not be dropped.
        self._add_missing_rows(table, targets)
        count = min(len(targets), len(self._rows))
        changed: list[int] = []
        batch = getattr(table, "batch_updates", nullcontext)
        with batch():
            for index in priority:
                if (
                    0 <= index < count
                    and self._is_stale(index, targets[index])
                    and self._render_row(table, index, targets[index])
                ):
                    changed.append(index)
            if full:
                self._refresh_changed_rows(table, targets, count, changed, deadline=deadline)
        self._repaint(table, changed)
        return self._summary(count, now) if full else None

    def catch_up(self, table: Any, targets: Sequence[TargetRun]) -> bool:
        """Refresh more of the rows the last full pass owes, within the time
        budget, and return whether rows are still owed."""
        if not self.behind:
            return False
        deadline = time.perf_counter() + self.budget_s
        count = min(len(targets), len(self._rows))
        changed: list[int] = []
        batch = getattr(table, "batch_updates", nullcontext)
        with batch():
            self._render_round_robin(
                table,
                targets,
                self._stale_rows(targets, count),
                changed,
                deadline=deadline,
                owed_only=True,
            )
        self._repaint(table, changed)
        return self.behind

    @staticmethod
    def _repaint(table: Any, changed: list[int]) -> None:
        refresh_rows = getattr(table, "refresh_rows", None)
        if changed and refresh_rows is not None:
            refresh_rows(changed)

    def _update_host_labels(self, targets: Sequence[TargetRun]) -> None:
        """Recompute the host labels when a name or the host width changed.

        Comparing the names is cheap (they are the same string objects from
        one call to the next); telling the names apart is O(N log N) and only
        redone when one changed.
        """
        names = [target.target for target in targets]
        names_changed = names != self._names
        if names_changed:
            self._names = names
            self._safe_names, self._name_spans = display_host_names(names)
        width = self._host_width
        if names_changed or width != self._labels_width:
            self._labels_width = width
            self._host_labels = fit_host_labels(self._safe_names, self._name_spans, width)
            self._generation += 1

    def _refresh_changed_rows(
        self,
        table: Any,
        targets: Sequence[TargetRun],
        count: int,
        changed: list[int],
        *,
        deadline: float,
    ) -> None:
        stale = self._stale_rows(targets, count)
        for index in stale:
            if self._refresh_state(table, index, targets[index]):
                changed.append(index)
        minimum = max(self.min_rows_per_pass, -(-count // self.full_sweep_passes))
        self._owed = min(len(stale), self._owed + minimum)
        self._render_round_robin(table, targets, stale, changed, deadline=deadline)

    def _stale_rows(self, targets: Sequence[TargetRun], count: int) -> list[int]:
        return [index for index in range(count) if self._is_stale(index, targets[index])]

    def _render_round_robin(
        self,
        table: Any,
        targets: Sequence[TargetRun],
        stale: list[int],
        changed: list[int],
        *,
        deadline: float,
        owed_only: bool = False,
    ) -> None:
        """Render ``stale`` rows from the round-robin cursor on until the
        deadline (at least one row, so every call makes progress), or with
        ``owed_only`` also until no rows are owed."""
        start = bisect_left(stale, self._cursor)
        for done, index in enumerate(stale[start:] + stale[:start]):
            if (done and time.perf_counter() >= deadline) or (owed_only and not self.behind):
                return
            if self._render_row(table, index, targets[index]):
                changed.append(index)
            self._owed = max(0, self._owed - 1)
            self._cursor = index + 1
        # Every changed row is current.
        self._owed = 0

    def _is_stale(self, index: int, target: TargetRun) -> bool:
        row = self._rows[index]
        samples = target.samples
        return not (
            row.target is target
            and row.samples is samples
            and row.revision == samples.revision
            and row.status is target.status
            and row.address == target.resolved_address
            and row.generation == self._generation
        )

    def _history_value(self, row: _RowState, samples: SampleWindow) -> CellValue:
        """Return the history cell, rendering only the samples added since the
        row was last rendered when the window and the layout are unchanged."""
        previous = row.values[HISTORY_INDEX]
        added = samples.revision - row.revision
        visible = min(self._history_width, len(samples))
        if (
            previous is not None
            and row.samples is samples
            and row.generation == self._generation
            # A clear bumps the revision too, so after one the window is
            # always shorter than ``added`` and this falls through.
            and 0 < added <= visible
            and added < len(samples)
        ):
            glyphs, codes = render_history_parts(
                samples,
                width=added,
                style=self.history_style,
                slow_latency_ms=self.slow_latency_ms,
            )
            return (previous[0] + glyphs)[-visible:], (previous[1] + codes)[-visible:]
        return render_history_parts(
            samples,
            width=self._history_width,
            style=self.history_style,
            slow_latency_ms=self.slow_latency_ms,
        )

    def _values(self, index: int, row: _RowState, target: TargetRun) -> RowValues:
        return (
            (self._host_labels[index], GREEN),
            _address_value(target, self._show_address),
            *_metric_values(
                target,
                jitter_threshold_ms=self.jitter_threshold_ms,
                slow_latency_ms=self.slow_latency_ms,
            ),
            (self.mode, TEXT),
            self._history_value(row, target.samples),
        )

    def _add_missing_rows(self, table: Any, targets: Sequence[TargetRun]) -> None:
        for index in range(len(self._rows), len(targets)):
            target = targets[index]
            row = _RowState()
            values = self._values(index, row, target)
            row.values[:] = values
            key = target_row_key(index)
            added = table.add_row(
                *(_cell_text(column, value) for column, value in enumerate(values)),
                key=key,
            )
            self._rows.append(row)
            # DataTable.add_row returns the RowKey; reusing it skips a key
            # conversion on every later update_cell.
            self._row_keys.append(key if added is None else added)
            self._categories.append(PENDING_LABEL)
            self._newest.append(_NO_SAMPLE)
            self._mark_rendered(index, target)

    def _mark_rendered(self, index: int, target: TargetRun) -> None:
        row = self._rows[index]
        samples = target.samples
        row.target = target
        row.samples = samples
        row.revision = samples.revision
        row.status = target.status
        row.address = target.resolved_address
        row.generation = self._generation
        self._remember_state(index, row.values[STATE_INDEX] or target_state(target), samples)

    def _remember_state(self, index: int, state: CellValue, samples: SampleWindow) -> None:
        self._categories[index] = _summary_category(state[0])
        self._newest[index] = (
            samples[-1].timestamp if samples and not self._probes_done(samples) else _NO_SAMPLE
        )

    def _probes_done(self, samples: SampleWindow) -> bool:
        """Whether a target has sent its --count probes: its newest result
        then says nothing about the probe cadence."""
        count = self.probe_count
        # The window revision counts every sample ever added, so it bounds
        # the sent count without building the summary for every row.
        return count is not None and samples.revision >= count and samples.summary().sent >= count

    def _refresh_state(self, table: Any, index: int, target: TargetRun) -> bool:
        """Write a changed state label ahead of the rest of the row."""
        row = self._rows[index]
        state = target_state(target)
        changed = row.values[STATE_INDEX] != state
        if changed:
            table.update_cell(
                self._row_keys[index],
                _COLUMN_KEY_OBJECTS[STATE_INDEX],
                _cell_text(STATE_INDEX, state),
                update_width=False,
            )
            row.values[STATE_INDEX] = state
        self._remember_state(index, state, target.samples)
        return changed

    def _render_row(self, table: Any, index: int, target: TargetRun) -> bool:
        """Send a row's changed cells to the table; return whether any changed."""
        row = self._rows[index]
        current = row.values
        row_key = self._row_keys[index]
        changed = False
        for column, value in enumerate(self._values(index, row, target)):
            if current[column] == value:
                continue
            table.update_cell(
                row_key,
                _COLUMN_KEY_OBJECTS[column],
                _cell_text(column, value),
                update_width=False,
            )
            current[column] = value
            changed = True
        self._mark_rendered(index, target)
        return changed

    def _summary(self, count: int, now: datetime | None) -> FleetSummary:
        counts = Counter(self._categories[:count])
        oldest = min(self._newest[:count], default=_NO_SAMPLE)
        age = None
        if oldest is not _NO_SAMPLE:
            current = datetime.now(timezone.utc) if now is None else now
            age = max(0.0, (current - oldest).total_seconds())
        return FleetSummary(counts=dict(counts), oldest_result_age_s=age)


def restore_cursor_row(table: Any, *, preferred_row: int, row_count: int) -> None:
    """Restore the selected row after a full table refresh."""
    if row_count <= 0:
        return

    row = min(max(preferred_row, 0), row_count - 1)
    table.move_cursor(row=row, column=0, animate=False)
