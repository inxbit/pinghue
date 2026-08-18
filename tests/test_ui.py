from datetime import datetime, timezone
from types import SimpleNamespace

from rich.text import Text

from pinghue.models import (
    ProbeSample,
    SampleStatus,
    SummaryStats,
    TargetRun,
    TargetStatus,
)
from pinghue.ui import (
    AMBER,
    COLUMN_KEYS,
    GREEN,
    RED,
    apply_column_widths,
    compact_loss,
    compact_ms,
    compute_table_layout,
    focus_table,
    format_history_cell,
    format_history_legend,
    format_state_cell,
    format_text_cell,
    issue_styles,
    restore_cursor_row,
    sync_target_table,
    target_row_key,
)


def sample(status: SampleStatus, latency_ms: float | None = None) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=latency_ms,
        status=status,
        error=None,
    )


def plain_and_style(value: Text) -> tuple[str, str]:
    spans = value.spans
    return value.plain, spans[0].style if spans else str(value.style or "")


def test_issue_styles_marks_latency_jitter_and_loss_independently() -> None:
    target = TargetRun(
        target="slow.example",
        status=TargetStatus.INTERMITTENT,
        samples=[
            sample(SampleStatus.OK, 10.0),
            sample(SampleStatus.OK, 720.0),
            sample(SampleStatus.TIMEOUT),
        ],
    )

    styles = issue_styles(target, jitter_threshold_ms=50.0, slow_latency_ms=300.0)

    assert styles["last"] == RED
    assert styles["max"] == AMBER
    assert styles["loss"] == RED
    assert styles["state"] == AMBER


def test_issue_styles_marks_loss_when_percentage_rounds_to_zero() -> None:
    target = SimpleNamespace(
        status=TargetStatus.INTERMITTENT,
        samples=[sample(SampleStatus.OK, 10.0)],
        stats=SummaryStats(
            sent=20_001,
            received=20_000,
            loss_pct=0.0,
            min_ms=10.0,
            avg_ms=10.0,
            max_ms=10.0,
            jitter_ms=0.0,
        ),
    )

    styles = issue_styles(target, jitter_threshold_ms=50.0, slow_latency_ms=300.0)

    assert styles["loss"] == RED


def test_format_text_cell_colors_only_affected_values() -> None:
    assert plain_and_style(format_text_cell("12.30", GREEN)) == ("12.30", GREEN)
    assert plain_and_style(format_text_cell("450.00", AMBER)) == ("450.00", AMBER)
    assert plain_and_style(format_text_cell("5.00%", RED)) == ("5.00%", RED)


def test_compact_metric_formats_keep_columns_readable() -> None:
    assert compact_ms(None) == "-"
    assert compact_ms(4.3) == "4.30"
    assert compact_ms(46.04) == "46.04"
    assert compact_ms(255.1) == "255.10"
    assert compact_ms(1200.0) == "1200.00"
    assert compact_loss(0.0) == "0.00%"
    assert compact_loss(38.89) == "38.89%"
    assert compact_loss(100.0) == "100.00%"


def test_format_state_cell_colors_health_state() -> None:
    assert plain_and_style(format_state_cell(TargetStatus.HEALTHY)) == ("healthy", GREEN)
    assert plain_and_style(format_state_cell(TargetStatus.INTERMITTENT)) == (
        "intermittent",
        AMBER,
    )
    assert plain_and_style(format_state_cell(TargetStatus.DOWN)) == ("down", RED)


def test_format_history_cell_colors_each_probe_segment() -> None:
    history = format_history_cell(
        [
            sample(SampleStatus.OK, 5.0),
            sample(SampleStatus.OK, 350.0),
            sample(SampleStatus.TIMEOUT),
        ],
        width=10,
        style="bar",
        slow_latency_ms=300.0,
    )

    assert history.plain == "▃▇·"
    assert [span.style for span in history.spans] == [GREEN, AMBER, RED]


def test_slow_latency_boundary_matches_the_fixed_glyph_scale() -> None:
    history = format_history_cell(
        [
            sample(SampleStatus.OK, 100.0),
            sample(SampleStatus.OK, 100.01),
        ],
        width=10,
        style="bar",
        slow_latency_ms=100.0,
    )

    assert history.plain == "▅▆"
    assert [span.style for span in history.spans] == [GREEN, AMBER]


def test_format_history_cell_marks_refused_amber() -> None:
    # A refusal is distinct from timeout/loss and uses the documented warning
    # color even when repeated refusals eventually classify the target DOWN.
    history = format_history_cell(
        [sample(SampleStatus.REFUSED)],
        width=10,
        style="bar",
        slow_latency_ms=300.0,
    )

    assert history.plain == "!"
    assert [span.style for span in history.spans] == [AMBER]


def test_format_history_legend_explains_probe_glyphs() -> None:
    legend = format_history_legend()

    assert "history:" in legend.plain
    assert "ok" in legend.plain
    assert "slow" in legend.plain
    assert "· loss/down" in legend.plain
    assert "! tcp refused" in legend.plain
    assert {span.style for span in legend.spans} >= {GREEN, AMBER, RED}


def test_dot_history_legend_matches_dot_glyphs() -> None:
    legend = format_history_legend("dots")

    assert "•" in legend.plain
    assert "▁▂▃" not in legend.plain
    assert "!" in legend.plain


def test_none_history_legend_is_empty() -> None:
    assert format_history_legend("none").plain == ""


def test_focus_table_restores_keyboard_navigation_target() -> None:
    class FakeTable:
        def __init__(self) -> None:
            self.focused = False

        def focus(self) -> None:
            self.focused = True

    table = FakeTable()

    assert focus_table(table) is table
    assert table.focused is True


def test_restore_cursor_row_preserves_selected_host_after_refresh() -> None:
    class FakeTable:
        def __init__(self) -> None:
            self.row: int | None = None
            self.column: int | None = None
            self.animate = False
            self.scroll = True

        def move_cursor(
            self,
            *,
            row: int | None = None,
            column: int | None = None,
            animate: bool = False,
            scroll: bool = True,
        ) -> None:
            self.row = row
            self.column = column
            self.animate = animate
            self.scroll = scroll

    table = FakeTable()

    restore_cursor_row(table, preferred_row=2, row_count=3)

    assert table.row == 2
    assert table.column == 0


def test_compute_table_layout_keeps_metrics_fixed_and_resizes_history() -> None:
    targets = [
        TargetRun("au-ml-vltr.metercdn.net", resolved_address="67.219.1.10"),
        TargetRun("test.sin2.servers.com", resolved_address="94.242.10.1"),
    ]

    narrow = compute_table_layout(width=96, targets=targets, show_address=True)
    wide = compute_table_layout(width=140, targets=targets, show_address=True)
    hidden_address = compute_table_layout(
        width=96,
        targets=targets,
        show_address=False,
    )

    assert narrow["address"] == 15
    assert hidden_address["address"] == len("address")
    assert narrow["state"] >= len("intermittent")
    assert narrow["last"] >= len("1200.00")
    assert narrow["loss"] >= len("100.00%")
    assert wide["history"] > narrow["history"]
    assert hidden_address["history"] > narrow["history"]


def test_compute_table_layout_uses_extra_wide_terminal_width() -> None:
    targets = [
        TargetRun("edge-gateway", resolved_address="67.219.1.10"),
        TargetRun("core-router", resolved_address="94.242.10.1"),
    ]

    width = 180
    layout = compute_table_layout(width=width, targets=targets, show_address=True)
    used_width = sum(layout.values()) + len(COLUMN_KEYS) + 2

    assert used_width == width
    assert layout["history"] > 48


def test_apply_column_widths_only_updates_changed_widths() -> None:
    class FakeColumn:
        def __init__(self, width: int) -> None:
            self.width = width

    class FakeTable:
        def __init__(self) -> None:
            self.columns = {"host": FakeColumn(8), "history": FakeColumn(10)}
            self.refreshed: list[int] = []

        def get_column_index(self, key: str) -> int:
            return 0 if key == "host" else 1

        def refresh_column(self, index: int) -> None:
            self.refreshed.append(index)

    table = FakeTable()

    apply_column_widths(table, {"host": 12, "history": 10})

    assert table.columns["host"].width == 12
    assert table.columns["history"].width == 10
    assert table.refreshed == [0]


def test_sync_target_table_updates_existing_rows_without_clearing() -> None:
    class FakeTable:
        def __init__(self) -> None:
            self.added_rows: list[tuple[str, tuple[object, ...]]] = []
            self.updated_cells: list[tuple[str, str, object, bool]] = []

        @property
        def row_count(self) -> int:
            return len(self.added_rows)

        def add_row(self, *cells: object, key: str) -> None:
            self.added_rows.append((key, cells))

        def update_cell(
            self,
            row_key: str,
            column_key: str,
            value: object,
            *,
            update_width: bool = False,
        ) -> None:
            self.updated_cells.append((row_key, column_key, value, update_width))

        def clear(self) -> None:
            raise AssertionError("refresh must not clear and rebuild rows")

    targets = [
        TargetRun("1.1.1.1", samples=[sample(SampleStatus.OK, 5.0)]),
        TargetRun("8.8.8.8", samples=[sample(SampleStatus.OK, 6.0)]),
    ]
    table = FakeTable()

    assert (
        sync_target_table(
            table,
            targets,
            initialized=False,
            show_address=False,
            mode="icmp",
            history_width=24,
            history_style="bar",
            jitter_threshold_ms=50.0,
            slow_latency_ms=300.0,
        )
        is True
    )
    assert [row[0] for row in table.added_rows] == [target_row_key(0), target_row_key(1)]

    sync_target_table(
        table,
        targets,
        initialized=True,
        show_address=False,
        mode="icmp",
        history_width=24,
        history_style="bar",
        jitter_threshold_ms=50.0,
        slow_latency_ms=300.0,
    )

    assert len(table.updated_cells) == len(targets) * len(COLUMN_KEYS)
    assert table.updated_cells[0][0] == target_row_key(0)
    assert table.updated_cells[0][1] == "host"


def test_sync_target_table_skips_unchanged_cached_cells() -> None:
    class FakeTable:
        def __init__(self) -> None:
            self.added_rows: list[tuple[str, tuple[object, ...]]] = []
            self.updated_cells: list[tuple[str, str, object, bool]] = []

        def add_row(self, *cells: object, key: str) -> None:
            self.added_rows.append((key, cells))

        def update_cell(
            self,
            row_key: str,
            column_key: str,
            value: object,
            *,
            update_width: bool = False,
        ) -> None:
            self.updated_cells.append((row_key, column_key, value, update_width))

    targets = [TargetRun("1.1.1.1", samples=[sample(SampleStatus.OK, 5.0)])]
    cache: dict[tuple[str, str], object] = {}
    table = FakeTable()

    sync_target_table(
        table,
        targets,
        initialized=False,
        show_address=False,
        mode="icmp",
        history_width=24,
        history_style="bar",
        jitter_threshold_ms=50.0,
        slow_latency_ms=300.0,
        cell_cache=cache,
    )
    sync_target_table(
        table,
        targets,
        initialized=True,
        show_address=False,
        mode="icmp",
        history_width=24,
        history_style="bar",
        jitter_threshold_ms=50.0,
        slow_latency_ms=300.0,
        cell_cache=cache,
    )

    assert table.updated_cells == []

    targets[0].samples.append(sample(SampleStatus.OK, 6.0))
    sync_target_table(
        table,
        targets,
        initialized=True,
        show_address=False,
        mode="icmp",
        history_width=24,
        history_style="bar",
        jitter_threshold_ms=50.0,
        slow_latency_ms=300.0,
        cell_cache=cache,
    )

    assert 0 < len(table.updated_cells) < len(COLUMN_KEYS)
