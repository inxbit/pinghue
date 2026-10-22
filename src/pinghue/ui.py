"""UI formatting helpers."""

from __future__ import annotations

import re
import time
from bisect import bisect_left
from collections.abc import Iterable, Iterator, MutableMapping, Sequence
from contextlib import contextmanager, nullcontext
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

# Row refresh scheduling: rows near the viewport are refreshed on every pass;
# the rest are refreshed round-robin within a per-pass time budget, but at
# least MIN_ROWS_PER_PASS and len(targets) / FULL_SWEEP_PASSES rows per pass,
# so every changed row is refreshed within FULL_SWEEP_PASSES + 1 full passes
# however large the table, and small tables are refreshed whole every pass.
ROW_REFRESH_BUDGET_S = 0.02
FULL_SWEEP_PASSES = 8
MIN_ROWS_PER_PASS = 256
VIEWPORT_MARGIN_ROWS = 4


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


def format_text_cell(value: str, style: str = TEXT) -> Text:
    """Return a styled table cell."""
    return Text(value, style=style)


def compact_ms(value: float | None) -> str:
    """Return a latency value sized for dense terminal columns."""
    if value is None:
        return "-"
    return f"{value:.2f}"


def compact_loss(value: float) -> str:
    """Return a loss percentage sized for dense terminal columns."""
    return f"{value:.2f}%"


def format_state_cell(status: TargetStatus) -> Text:
    """Return a styled target-state cell."""
    label, style = _STATE_LABELS.get(status, (status.value, RED))
    return Text(label, style=style)


def target_state(target: TargetRun) -> CellValue:
    """Return the TUI state label and style for a target."""
    return _STATE_LABELS.get(target.status, (target.status.value, RED))


def history_text(glyphs: str, codes: str) -> Text:
    """Return a history cell with one span per run of equally styled glyphs."""
    return Text(
        glyphs,
        spans=[
            Span(run.start(), run.end(), _HISTORY_CODE_STYLES[run.group(1)])
            for run in _STYLE_RUNS.finditer(codes)
        ],
    )


def format_history_cell(
    samples: Sequence[ProbeSample],
    *,
    width: int,
    style: str,
    slow_latency_ms: float,
) -> Text:
    """Return a colored history cell."""
    glyphs, codes = render_history_parts(
        samples,
        width=width,
        style=style,
        slow_latency_ms=slow_latency_ms,
    )
    return history_text(glyphs, codes)


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


def _host_value(target: TargetRun) -> CellValue:
    return sanitize_display(target.target), GREEN


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


def target_row_values(
    target: TargetRun,
    *,
    show_address: bool,
    mode: str,
    history_width: int,
    history_style: str,
    jitter_threshold_ms: float,
    slow_latency_ms: float,
) -> RowValues:
    """Return the plain text and style of every cell in one target row."""
    return (
        _host_value(target),
        _address_value(target, show_address),
        *_metric_values(
            target,
            jitter_threshold_ms=jitter_threshold_ms,
            slow_latency_ms=slow_latency_ms,
        ),
        (mode, TEXT),
        render_history_parts(
            target.samples,
            width=history_width,
            style=history_style,
            slow_latency_ms=slow_latency_ms,
        ),
    )


def _cell_text(column_index: int, value: CellValue) -> Text:
    if column_index == HISTORY_INDEX:
        return history_text(*value)
    return Text(value[0], style=value[1])


def target_row_cells(
    target: TargetRun,
    *,
    show_address: bool,
    mode: str,
    history_width: int,
    history_style: str,
    jitter_threshold_ms: float,
    slow_latency_ms: float,
) -> tuple[Text, ...]:
    """Return rendered cells for one target row."""
    values = target_row_values(
        target,
        show_address=show_address,
        mode=mode,
        history_width=history_width,
        history_style=history_style,
        jitter_threshold_ms=jitter_threshold_ms,
        slow_latency_ms=slow_latency_ms,
    )
    return tuple(_cell_text(index, value) for index, value in enumerate(values))


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
    MIN_HISTORY_WIDTH first, then the host column to MIN_HOST_WIDTH, then the
    NARROW_COLLAPSE_ORDER columns collapse; below that history goes and the
    table scrolls horizontally instead of squeezing host names unreadable.
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
    collapsible = [key for key in NARROW_COLLAPSE_ORDER if key != "address" or not show_address]
    flexible = available - sum(layout.values())
    while flexible < MIN_HOST_WIDTH + MIN_HISTORY_WIDTH and collapsible:
        layout[collapsible.pop(0)] = 0
        flexible = available - sum(layout.values())

    host_width = max(MIN_HOST_WIDTH, min(_target_width(targets), flexible - MIN_HISTORY_WIDTH))
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


def sync_target_table(
    table: Any,
    targets: Sequence[TargetRun],
    *,
    initialized: bool,
    show_address: bool,
    mode: str,
    history_width: int,
    history_style: str,
    jitter_threshold_ms: float,
    slow_latency_ms: float,
    cell_cache: MutableMapping[tuple[str, str], object] | None = None,
) -> bool:
    """Add rows once, then update every row's changed cells in place.

    This is the simple path without change tracking or a time budget; the TUI
    uses TargetTableRenderer.
    """
    if not targets:
        return initialized

    for index, target in enumerate(targets):
        row_key = target_row_key(index)
        values = target_row_values(
            target,
            show_address=show_address,
            mode=mode,
            history_width=history_width,
            history_style=history_style,
            jitter_threshold_ms=jitter_threshold_ms,
            slow_latency_ms=slow_latency_ms,
        )
        if not initialized:
            table.add_row(
                *(_cell_text(column, value) for column, value in enumerate(values)),
                key=row_key,
            )
            if cell_cache is not None:
                for column_key, value in zip(COLUMN_KEYS, values, strict=True):
                    cell_cache[(row_key, column_key)] = value
            continue

        for column, (column_key, value) in enumerate(zip(COLUMN_KEYS, values, strict=True)):
            cache_key = (row_key, column_key)
            if cell_cache is not None and cell_cache.get(cache_key) == value:
                continue

            table.update_cell(row_key, column_key, _cell_text(column, value), update_width=False)
            if cell_cache is not None:
                cell_cache[cache_key] = value

    return True


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
    change a target), or when the layout changed, and only cells whose text or
    style changed are sent to the table. Rows near the viewport are refreshed
    first and the rest round-robin within a time budget; a changed state label
    is written at once on every full pass, so no state change is held back.
    """

    def __init__(
        self,
        *,
        mode: str,
        history_style: str,
        jitter_threshold_ms: float,
        slow_latency_ms: float,
        budget_s: float = ROW_REFRESH_BUDGET_S,
        full_sweep_passes: int = FULL_SWEEP_PASSES,
        min_rows_per_pass: int = MIN_ROWS_PER_PASS,
    ) -> None:
        self.mode = mode
        self.history_style = history_style
        self.jitter_threshold_ms = jitter_threshold_ms
        self.slow_latency_ms = slow_latency_ms
        self.budget_s = budget_s
        self.full_sweep_passes = max(1, full_sweep_passes)
        self.min_rows_per_pass = min_rows_per_pass
        self._rows: list[_RowState] = []
        self._row_keys: list[Any] = []
        self._show_address = False
        self._history_width = 0
        self._generation = 0
        self._cursor = 0

    def set_layout(self, *, show_address: bool, widths: ColumnWidths) -> None:
        """Record the layout the cells depend on; a change re-renders every row."""
        layout = (show_address, widths["history"])
        if layout != (self._show_address, self._history_width):
            self._show_address, self._history_width = layout
            self._generation += 1

    def sync(
        self,
        table: Any,
        targets: Sequence[TargetRun],
        *,
        priority: range = range(0),
        full: bool = True,
    ) -> None:
        """Bring the table up to date with ``targets``.

        ``priority`` rows (the viewport) are always refreshed. A ``full`` pass
        also writes changed state labels everywhere and refreshes the other
        changed rows round-robin.
        """
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
                self._refresh_changed_rows(table, targets, count, changed)
        refresh_rows = getattr(table, "refresh_rows", None)
        if changed and refresh_rows is not None:
            refresh_rows(changed)

    def _refresh_changed_rows(
        self,
        table: Any,
        targets: Sequence[TargetRun],
        count: int,
        changed: list[int],
    ) -> None:
        started = time.perf_counter()
        stale = [index for index in range(count) if self._is_stale(index, targets[index])]
        for index in stale:
            if self._refresh_state(table, index, targets[index]):
                changed.append(index)
        if not stale:
            return

        minimum = max(self.min_rows_per_pass, -(-count // self.full_sweep_passes))
        deadline = started + self.budget_s
        start = bisect_left(stale, self._cursor)
        for done, index in enumerate(stale[start:] + stale[:start]):
            if done >= minimum and time.perf_counter() >= deadline:
                break
            if self._render_row(table, index, targets[index]):
                changed.append(index)
            self._cursor = index + 1

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

    def _values(self, row: _RowState, target: TargetRun) -> RowValues:
        host = row.values[HOST_INDEX]
        if host is None or row.target is not target:
            host = _host_value(target)
        return (
            host,
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
            values = self._values(row, target)
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
        return changed

    def _render_row(self, table: Any, index: int, target: TargetRun) -> bool:
        """Send a row's changed cells to the table; return whether any changed."""
        row = self._rows[index]
        current = row.values
        row_key = self._row_keys[index]
        changed = False
        for column, value in enumerate(self._values(row, target)):
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


def restore_cursor_row(table: Any, *, preferred_row: int, row_count: int) -> None:
    """Restore the selected row after a full table refresh."""
    if row_count <= 0:
        return

    row = min(max(preferred_row, 0), row_count - 1)
    table.move_cursor(row=row, column=0, animate=False)
