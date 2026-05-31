"""UI formatting helpers."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from typing import Any

from rich.text import Text

from pinghue.display import sanitize_display
from pinghue.history import render_history, visible_history_samples
from pinghue.models import ProbeSample, SampleStatus, TargetRun, TargetStatus

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

CellSignature = tuple[str, str, tuple[tuple[int, int, str], ...]]
ColumnWidths = dict[str, int]

MIN_HOST_WIDTH = 12
MAX_HOST_WIDTH = 24
HIDDEN_ADDRESS_WIDTH = len("address")
IPV4_ADDRESS_WIDTH = 15
IPV6_ADDRESS_WIDTH = 39
MIN_HISTORY_WIDTH = 6
TABLE_CHROME_WIDTH = 2
COLUMN_GAP_WIDTH = len(COLUMN_KEYS)
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


def _latest_success(target: TargetRun) -> ProbeSample | None:
    return next(
        (
            sample
            for sample in reversed(target.samples)
            if sample.status == SampleStatus.OK and sample.latency_ms is not None
        ),
        None,
    )


def issue_styles(
    target: TargetRun,
    *,
    jitter_threshold_ms: float,
    slow_latency_ms: float,
) -> dict[str, str]:
    """Return per-column styles for values that need attention."""
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

    if stats.loss_pct > 0:
        styles["loss"] = RED

    if stats.jitter_ms is not None and stats.jitter_ms > jitter_threshold_ms:
        styles["jitter"] = AMBER

    latest = target.samples[-1] if target.samples else None
    if latest and latest.status != SampleStatus.OK:
        styles["last"] = RED
    elif latest and latest.latency_ms is not None and latest.latency_ms >= slow_latency_ms:
        styles["last"] = AMBER

    if stats.avg_ms is not None and stats.avg_ms >= slow_latency_ms:
        styles["avg"] = AMBER

    if stats.max_ms is not None and stats.max_ms >= slow_latency_ms:
        styles["max"] = AMBER

    return styles


def format_text_cell(value: str, style: str = TEXT) -> Text:
    """Return a styled table cell."""
    return Text(value, style=style)


def format_numeric_cell(value: str, style: str = TEXT) -> Text:
    """Return a styled numeric table cell."""
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
    if status == TargetStatus.RESOLVING:
        style = MUTED
        label = "resolving"
    elif status == TargetStatus.HEALTHY:
        style = GREEN
        label = "healthy"
    elif status == TargetStatus.INTERMITTENT:
        style = AMBER
        label = "intermittent"
    elif status == TargetStatus.DNS_FAILURE:
        style = RED
        label = "dns"
    elif status == TargetStatus.PERMISSION_DENIED:
        style = RED
        label = "denied"
    else:
        style = RED
        label = status.value

    return Text(label, style=style)


def _history_sample_style(sample: ProbeSample, *, slow_latency_ms: float) -> str:
    if sample.status != SampleStatus.OK or sample.latency_ms is None:
        return RED

    if sample.latency_ms >= slow_latency_ms:
        return AMBER

    return GREEN


def format_history_cell(
    samples: Sequence[ProbeSample],
    *,
    width: int,
    style: str,
    slow_latency_ms: float,
) -> Text:
    """Return a colored history cell."""
    if style == "none" or width <= 0:
        return Text("")

    text = Text()
    visible = visible_history_samples(samples, width=width)
    rendered = render_history(samples, width=width, style=style)
    for glyph, sample in zip(rendered, visible, strict=True):
        text.append(glyph, style=_history_sample_style(sample, slow_latency_ms=slow_latency_ms))

    return text


def format_history_legend() -> Text:
    """Return the bottom legend for history glyphs."""
    text = Text("history: ", style=MUTED)
    text.append("▁▂▃ ", style=GREEN)
    text.append("ok  ", style=MUTED)
    text.append("▆▇█ ", style=AMBER)
    text.append("slow  ", style=MUTED)
    text.append("·", style=RED)
    text.append(" loss/down  ", style=MUTED)
    text.append("!", style=RED)
    text.append(" tcp refused", style=MUTED)
    return text


def focus_table(table: Any) -> Any:
    """Restore focus to the target table and return it."""
    table.focus()
    return table


def target_row_key(index: int) -> str:
    """Return the stable table row key for a target index."""
    return f"target-{index}"


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
    stats = target.stats
    last = next(
        (
            sample
            for sample in reversed(target.samples)
            if sample.latency_ms is not None
        ),
        None,
    )
    address = target.resolved_address if show_address else ""
    styles = issue_styles(
        target,
        jitter_threshold_ms=jitter_threshold_ms,
        slow_latency_ms=slow_latency_ms,
    )

    return (
        format_text_cell(sanitize_display(target.target), GREEN),
        format_text_cell(sanitize_display(address or ""), GREEN if address else MUTED),
        format_state_cell(target.status),
        format_numeric_cell(
            compact_ms(None if last is None else last.latency_ms),
            styles["last"],
        ),
        format_numeric_cell(
            compact_ms(stats.min_ms),
            styles["min"],
        ),
        format_numeric_cell(
            compact_ms(stats.avg_ms),
            styles["avg"],
        ),
        format_numeric_cell(
            compact_ms(stats.max_ms),
            styles["max"],
        ),
        format_numeric_cell(
            compact_ms(stats.jitter_ms),
            styles["jitter"],
        ),
        format_numeric_cell(compact_loss(stats.loss_pct), styles["loss"]),
        format_text_cell(mode, TEXT),
        format_history_cell(
            target.samples,
            width=history_width,
            style=history_style,
            slow_latency_ms=slow_latency_ms,
        ),
    )


def _uses_ipv6(targets: Sequence[TargetRun]) -> bool:
    return any(
        (target.resolved_address and ":" in target.resolved_address)
        for target in targets
    )


def _target_width(targets: Sequence[TargetRun]) -> int:
    longest = max((len(target.target) for target in targets), default=len("host"))
    return min(max(longest, MIN_HOST_WIDTH), MAX_HOST_WIDTH)


def compute_table_layout(
    *,
    width: int,
    targets: Sequence[TargetRun],
    show_address: bool,
) -> ColumnWidths:
    """Return responsive DataTable column widths."""
    layout = dict(FIXED_COLUMN_WIDTHS)
    if not show_address:
        layout["address"] = HIDDEN_ADDRESS_WIDTH
    elif _uses_ipv6(targets):
        layout["address"] = IPV6_ADDRESS_WIDTH
    else:
        layout["address"] = IPV4_ADDRESS_WIDTH

    fixed_without_host_history = sum(layout.values())
    flexible = width - TABLE_CHROME_WIDTH - COLUMN_GAP_WIDTH - fixed_without_host_history
    preferred_host = _target_width(targets)

    if flexible <= MIN_HOST_WIDTH:
        host_width = max(0, flexible)
        history_width = 0
    else:
        host_width = min(preferred_host, flexible)
        history_width = flexible - host_width
        if history_width < MIN_HISTORY_WIDTH and host_width > MIN_HOST_WIDTH:
            shift = min(MIN_HISTORY_WIDTH - history_width, host_width - MIN_HOST_WIDTH)
            host_width -= shift
            history_width += shift

    layout["host"] = host_width
    layout["history"] = max(0, history_width)
    return {key: layout[key] for key in COLUMN_KEYS}


def apply_column_widths(table: Any, widths: ColumnWidths) -> None:
    """Apply column widths to an existing Textual DataTable."""
    for column_key, width in widths.items():
        try:
            column_index = table.get_column_index(column_key)
            column = table.ordered_columns[column_index]
        except AttributeError:
            column_index = table.get_column_index(column_key)
            column = table.columns.get(column_key)

        if column is None:
            continue
        if column.width == width:
            continue

        column.width = width
        table.refresh_column(column_index)


def _cell_signature(cell: Text) -> CellSignature:
    return (
        cell.plain,
        str(cell.style or ""),
        tuple((span.start, span.end, str(span.style)) for span in cell.spans),
    )


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
    """Add rows once, then update cells in place on subsequent refreshes."""
    if not targets:
        return initialized

    if not initialized:
        for index, target in enumerate(targets):
            row_key = target_row_key(index)
            cells = target_row_cells(
                target,
                show_address=show_address,
                mode=mode,
                history_width=history_width,
                history_style=history_style,
                jitter_threshold_ms=jitter_threshold_ms,
                slow_latency_ms=slow_latency_ms,
            )
            table.add_row(
                *cells,
                key=row_key,
            )
            if cell_cache is not None:
                for column_key, cell in zip(COLUMN_KEYS, cells, strict=True):
                    cell_cache[(row_key, column_key)] = _cell_signature(cell)
        return True

    for index, target in enumerate(targets):
        row_key = target_row_key(index)
        cells = target_row_cells(
            target,
            show_address=show_address,
            mode=mode,
            history_width=history_width,
            history_style=history_style,
            jitter_threshold_ms=jitter_threshold_ms,
            slow_latency_ms=slow_latency_ms,
        )
        for column_key, cell in zip(COLUMN_KEYS, cells, strict=True):
            cache_key = (row_key, column_key)
            signature = _cell_signature(cell)
            if cell_cache is not None and cell_cache.get(cache_key) == signature:
                continue

            table.update_cell(row_key, column_key, cell, update_width=False)
            if cell_cache is not None:
                cell_cache[cache_key] = signature

    return True


def restore_cursor_row(table: Any, *, preferred_row: int, row_count: int) -> None:
    """Restore the selected row after a full table refresh."""
    if row_count <= 0:
        return

    row = min(max(preferred_row, 0), row_count - 1)
    table.move_cursor(row=row, column=0, animate=False)
