"""Target table rendering: change tracking, the per-pass budget, and the
DataTable geometry the renderer and the layout rely on.

The Pilot tests pin Textual behavior that pinghue works around (scroll extent
after column width changes, row-to-line mapping for partial repaints), so a
Textual upgrade that changes it fails here instead of on screen.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.widget import Widget
from textual.widgets import DataTable

import pinghue.app as app_module
import pinghue.runner as runner_module
import pinghue.ui as ui
from pinghue.app import PinghueTextualApp
from pinghue.models import (
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    SampleWindow,
    TargetRun,
    TargetStatus,
)
from pinghue.ui import (
    AMBER,
    COLUMN_KEYS,
    RED,
    TargetTable,
    TargetTableRenderer,
    apply_column_widths,
    compute_table_layout,
    format_history_cell,
    target_row_cells,
    target_row_key,
)

T0 = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
SLOW_MS = 100.0


def ok(latency_ms: float, *, at: float = 0.0) -> ProbeSample:
    return ProbeSample(T0 + timedelta(seconds=at), latency_ms, SampleStatus.OK)


def lost(*, at: float = 0.0) -> ProbeSample:
    return ProbeSample(T0 + timedelta(seconds=at), None, SampleStatus.TIMEOUT)


def refused(*, at: float = 0.0) -> ProbeSample:
    return ProbeSample(T0 + timedelta(seconds=at), 1.0, SampleStatus.REFUSED)


def healthy(name: str, *samples: ProbeSample) -> TargetRun:
    return TargetRun(
        name,
        resolved_address="127.0.253.1",
        status=TargetStatus.HEALTHY,
        samples=list(samples or (ok(5.0),)),
    )


def add(target: TargetRun, sample: ProbeSample) -> None:
    target.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)


class RecordingTable:
    """Stands in for the DataTable: keeps the latest cell of every row."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.updates: list[tuple[str, str]] = []

    def add_row(self, *cells: Any, key: str) -> str:
        self.rows[key] = dict(zip(COLUMN_KEYS, cells, strict=True))
        return key

    def update_cell(self, row_key: Any, column_key: Any, value: Any, *, update_width: bool) -> None:
        assert update_width is False
        row = str(getattr(row_key, "value", row_key))
        column = str(getattr(column_key, "value", column_key))
        self.rows[row][column] = value
        self.updates.append((row, column))

    def cell(self, index: int, column: str) -> Any:
        return self.rows[target_row_key(index)][column]

    def updated_rows(self) -> set[str]:
        return {row for row, _column in self.updates}


WIDTHS = compute_table_layout(width=160, targets=[healthy("db-primary-01")], show_address=True)


def make_renderer(**overrides: Any) -> TargetTableRenderer:
    options: dict[str, Any] = {
        "mode": "icmp",
        "history_style": "bar",
        "jitter_threshold_ms": 50.0,
        "slow_latency_ms": SLOW_MS,
        # By default every changed row is refreshed on every full pass.
        "budget_s": 60.0,
        "full_sweep_passes": 1,
    }
    options.update(overrides)
    renderer = TargetTableRenderer(**options)
    renderer.set_layout(show_address=True, widths=WIDTHS)
    return renderer


def fresh_cells(target: TargetRun, widths: dict[str, int] = WIDTHS) -> list[str]:
    return [
        cell.plain
        for cell in target_row_cells(
            target,
            show_address=True,
            mode="icmp",
            history_width=widths["history"],
            history_style="bar",
            jitter_threshold_ms=50.0,
            slow_latency_ms=SLOW_MS,
        )
    ]


def shown_cells(table: RecordingTable, index: int) -> list[str]:
    return [table.cell(index, column).plain for column in COLUMN_KEYS]


def test_unchanged_rows_are_skipped_without_rebuilding_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [healthy(f"host-{index:04d}") for index in range(5000)]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.sync(table, targets)
    assert len(table.rows) == 5000

    rebuilt: list[str] = []
    metric_values = ui._metric_values

    def counting_metric_values(target: TargetRun, **kwargs: Any) -> Any:
        rebuilt.append(target.target)
        return metric_values(target, **kwargs)

    monkeypatch.setattr(ui, "_metric_values", counting_metric_values)
    renderer.sync(table, targets, priority=range(0, 60))

    assert table.updates == []
    assert rebuilt == []

    add(targets[1234], ok(42.0))
    renderer.sync(table, targets, priority=range(0, 60))

    assert rebuilt == ["host-1234"]
    assert table.updated_rows() == {target_row_key(1234)}
    assert table.cell(1234, "last").plain == "42.00"


def _apply_sample(targets: list[TargetRun], index: int) -> None:
    add(targets[index], ok(420.0))


def _reset(targets: list[TargetRun], index: int) -> None:
    targets[index].samples.clear()
    targets[index].status = TargetStatus.RESOLVING


def _dns_failure(targets: list[TargetRun], index: int) -> None:
    # The runner's DNS paths change the status without adding a sample.
    targets[index].status = TargetStatus.DNS_FAILURE


def _failover(targets: list[TargetRun], index: int) -> None:
    targets[index].resolved_address = "127.0.253.99"


def _replace_target(targets: list[TargetRun], index: int) -> None:
    targets[index] = TargetRun(
        targets[index].target,
        resolved_address="127.0.253.77",
        status=TargetStatus.INTERMITTENT,
        samples=[ok(7.0), lost()],
    )


def _replace_window(targets: list[TargetRun], index: int) -> None:
    targets[index].samples = SampleWindow([ok(9.0), ok(11.0)])


@pytest.mark.parametrize(
    "mutate",
    [_apply_sample, _reset, _dns_failure, _failover, _replace_target, _replace_window],
)
def test_every_way_a_target_changes_rerenders_its_row(mutate: Any) -> None:
    targets = [healthy(f"host-{index}", ok(5.0), ok(6.0)) for index in range(8)]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.sync(table, targets)
    table.updates.clear()

    mutate(targets, 5)
    renderer.sync(table, targets)

    assert table.updated_rows() == {target_row_key(5)}
    assert shown_cells(table, 5) == fresh_cells(targets[5])

    table.updates.clear()
    renderer.sync(table, targets)
    assert table.updates == []


def test_layout_change_rerenders_every_row() -> None:
    long_name = "db-primary-01.dc1.example.net"
    targets = [healthy(long_name, *(ok(float(n)) for n in range(1, 40))) for _ in range(3)]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.sync(table, targets)

    narrow = dict(WIDTHS, host=14, history=9)
    renderer.set_layout(show_address=False, widths=narrow)
    renderer.sync(table, targets)

    for index in range(3):
        assert len(table.cell(index, "history").plain) == 9
        assert table.cell(index, "address").plain == ""


def test_round_robin_refreshes_every_changed_row_within_the_sweep() -> None:
    targets = [healthy(f"host-{index}") for index in range(20)]
    table = RecordingTable()
    # A zero budget leaves only the guaranteed minimum: 20 rows / 4 passes.
    renderer = make_renderer(budget_s=0.0, full_sweep_passes=4, min_rows_per_pass=1)
    renderer.sync(table, targets)

    for round_number in range(3):
        latency = f"{40.0 + round_number:.2f}"
        for target in targets:
            add(target, ok(40.0 + round_number))
        passes = 0
        while any(table.cell(index, "last").plain != latency for index in range(20)):
            renderer.sync(table, targets)
            passes += 1
            assert passes <= 4
        assert passes == 4


def test_state_changes_are_written_on_the_next_full_pass() -> None:
    targets = [healthy(f"host-{index}") for index in range(50)]
    table = RecordingTable()
    renderer = make_renderer(budget_s=0.0, full_sweep_passes=100, min_rows_per_pass=1)
    renderer.sync(table, targets)

    for target in targets:
        add(target, ok(41.0))
    targets[40].status = TargetStatus.DOWN
    renderer.sync(table, targets)

    # Only one row was rebuilt this pass, but every state label is current.
    assert table.cell(40, "state").plain == "down"
    assert table.cell(40, "last").plain != "41.00"


def test_viewport_rows_are_refreshed_without_a_full_pass() -> None:
    targets = [healthy(f"host-{index}") for index in range(40)]
    table = RecordingTable()
    renderer = make_renderer(budget_s=0.0, full_sweep_passes=100, min_rows_per_pass=1)
    renderer.sync(table, targets)
    table.updates.clear()

    for target in targets:
        add(target, ok(43.0))
    renderer.sync(table, targets, priority=range(10, 20), full=False)

    assert table.updated_rows() == {target_row_key(index) for index in range(10, 20)}


def test_incremental_history_matches_a_full_render() -> None:
    rng = random.Random(20260514)
    targets = [
        TargetRun(
            f"host-{index}",
            resolved_address="127.0.253.1",
            status=TargetStatus.HEALTHY,
            samples=SampleWindow(maxlen=12),
        )
        for index in range(6)
    ]
    table = RecordingTable()
    widths = dict(WIDTHS, history=8)
    renderer = make_renderer()
    renderer.set_layout(show_address=True, widths=widths)

    for step in range(400):
        for target in targets:
            roll = rng.random()
            if roll < 0.04:
                target.samples.clear()
            elif roll < 0.06:
                target.samples = SampleWindow(maxlen=12)
            else:
                for _ in range(rng.randint(0, 5)):
                    kind = rng.random()
                    if kind < 0.15:
                        target.samples.append(lost(at=step))
                    elif kind < 0.2:
                        target.samples.append(refused(at=step))
                    else:
                        target.samples.append(ok(rng.uniform(0.5, 1500.0), at=step))
        if rng.random() < 0.05:
            widths["history"] = rng.randint(1, 20)
            renderer.set_layout(show_address=True, widths=widths)
        renderer.sync(table, targets)

        for index, target in enumerate(targets):
            expected = format_history_cell(
                target.samples,
                width=widths["history"],
                style="bar",
                slow_latency_ms=SLOW_MS,
            )
            shown = table.cell(index, "history")
            assert (shown.plain, shown.spans) == (expected.plain, expected.spans), step


def test_history_cell_uses_one_span_per_run_of_equal_styles() -> None:
    samples = [ok(5.0), ok(6.0), ok(150.0), lost(), lost(), ok(7.0)]

    cell = format_history_cell(samples, width=10, style="bar", slow_latency_ms=SLOW_MS)

    assert [(span.start, span.end, span.style) for span in cell.spans] == [
        (0, 2, ui.GREEN),
        (2, 3, AMBER),
        (3, 5, RED),
        (5, 6, ui.GREEN),
    ]


class TableApp(App[None]):
    def compose(self) -> ComposeResult:
        yield TargetTable(id="targets")


def _render_widths(table: DataTable[Any]) -> int:
    return sum(column.get_render_width(table) for column in table.columns.values())


async def test_refresh_rows_repaints_the_lines_datatable_draws_the_rows_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = TableApp()
    async with app.run_test(size=(60, 20)) as pilot:
        table = app.query_one(TargetTable)
        table.add_column("host", key="host", width=12)
        for index in range(100):
            table.add_row(Text(f"row {index}"), key=f"row-{index}")
        await pilot.pause()
        table.scroll_to(y=30, animate=False, immediate=True)
        await pilot.pause()
        assert table.scroll_offset.y == 30

        refreshed: list[Region] = []
        original_refresh = Widget.refresh

        def recording_refresh(widget: Widget, *regions: Region, **kwargs: Any) -> Any:
            if widget is table:
                refreshed.extend(regions)
            return original_refresh(widget, *regions, **kwargs)

        monkeypatch.setattr(Widget, "refresh", recording_refresh)
        visible_rows = table.size.height - table.header_height
        on_screen = [30, 31, 32, 40, 30 + visible_rows - 1]
        table.refresh_rows([29, *on_screen, 30 + visible_rows, 99])

        lines = {region.y + offset for region in refreshed for offset in range(region.height)}
        # DataTable's own geometry for those rows, in screen lines.
        scroll = table.scroll_offset
        expected = {table._get_row_region(index).translate(-scroll).y for index in on_screen}
        assert lines == expected
        assert all(region.x == 0 and region.width >= table.size.width for region in refreshed)


async def test_column_width_changes_drop_cached_renders() -> None:
    # DataTable's render caches are not keyed by column width.
    app = TableApp()
    async with app.run_test(size=(60, 10)) as pilot:
        table = app.query_one(TargetTable)
        table.add_column("host", key="host", width=4)
        table.add_column("state", key="state", width=6)
        table.add_row(Text("aaaa"), Text("bbbb"), key="row-0")
        await pilot.pause()
        before = table.render_line(table.header_height).text.index("bbbb")

        assert apply_column_widths(table, {"host": 10, "state": 6})
        await pilot.pause()

        assert table.render_line(table.header_height).text.index("bbbb") == before + 6
        assert table.virtual_size.width == _render_widths(table)


async def test_batched_updates_repaint_changed_rows_on_screen() -> None:
    app = TableApp()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one(TargetTable)
        targets = [healthy(f"host-{index}") for index in range(40)]
        widths = compute_table_layout(width=120, targets=targets, show_address=True)
        for key in COLUMN_KEYS:
            table.add_column(key, key=key, width=widths[key])
        renderer = make_renderer()
        renderer.set_layout(show_address=True, widths=widths)
        renderer.sync(table, targets, priority=range(0, 20))
        await pilot.pause()

        def screen_text() -> str:
            region = Region(0, 0, *table.outer_size)
            return "\n".join(strip.text for strip in table.render_lines(region))

        assert "777.00" not in screen_text()
        add(targets[3], ok(777.0))
        add(targets[35], ok(888.0))
        renderer.sync(table, targets, priority=range(0, 20))
        await pilot.pause()

        # Row 3 is on screen: its line was repainted from the cached render.
        assert "777.00" in screen_text()
        table.scroll_to(y=30, animate=False, immediate=True)
        await pilot.pause()
        assert "888.00" in screen_text()


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 64,
        "count": None,
        "duration": None,
        "fail_threshold": 3,
        "history_style": "bar",
        "interval": 60.0,
        "jitter_threshold": 50.0,
        "port": None,
        "targets": ["a.example", "b.example"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
def stub_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every target to a loopback address; probes answer at once."""

    async def fake_resolve(target: str, _args: object) -> TargetRun:
        return TargetRun(
            target=target,
            resolved_address="127.0.253.1",
            resolved_family=AddressFamily.IPV4,
            resolved_addresses=("127.0.253.1",),
            status=TargetStatus.DOWN,
        )

    async def fake_probe_once(target: TargetRun, **_kwargs: Any) -> ProbeSample:
        sample = ok(5.0)
        add(target, sample)
        return sample

    monkeypatch.setattr(app_module, "resolve_run_target", fake_resolve)
    monkeypatch.setattr(runner_module, "probe_once", fake_probe_once)


async def _wait_until(pilot: Any, predicate: Any, *, ticks: int = 100) -> bool:
    for _ in range(ticks):
        if predicate():
            return True
        await pilot.pause()
    return False


async def _start(pilot: Any, app: PinghueTextualApp) -> DataTable[Any]:
    """Wait for resolution, give every target a result and refresh the table.

    Probe loops are staggered across the (long) interval, so the samples are
    added here instead of waiting for them.
    """
    resolved = await _wait_until(
        pilot,
        lambda: bool(app.targets) and all(target.resolved_address for target in app.targets),
    )
    assert resolved
    for target in app.targets:
        if not target.samples:
            add(target, ok(5.0))
    # Force a full pass: calls right after another one only cover the viewport.
    app._last_full_refresh = float("-inf")
    app._refresh_table()
    await pilot.pause()
    return app.query_one("#targets", DataTable)


def _assert_geometry(table: DataTable[Any]) -> None:
    # The scroll extent follows the current column widths.
    assert table.virtual_size.width == _render_widths(table)


@pytest.mark.usefixtures("stub_network")
@pytest.mark.parametrize("width", [100, 120, 160, 220])
@pytest.mark.parametrize("target_count", [2, 60])
async def test_table_fits_common_terminal_widths_without_horizontal_scroll(
    width: int,
    target_count: int,
) -> None:
    targets = [f"db-primary-{index:02d}.dc1.example.net" for index in range(target_count)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(width, 30)) as pilot:
        table = await _start(pilot, app)

        _assert_geometry(table)
        assert table.max_scroll_x == 0
        # The newest history glyph is the last one in the last column, which
        # is inside the viewport.
        assert table.get_cell(target_row_key(0), "history").plain.endswith("▃")


@pytest.mark.usefixtures("stub_network")
async def test_resize_and_address_toggle_recompute_the_scroll_extent() -> None:
    targets = [f"db-primary-{index:02d}.dc1.example.net" for index in range(40)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(120, 30)) as pilot:
        table = await _start(pilot, app)

        for width in (200, 90, 160):
            await pilot.resize_terminal(width, 30)
            await pilot.pause()
            _assert_geometry(table)
            assert table.max_scroll_x == 0

        await pilot.press("a")
        await pilot.pause()
        assert table.columns["address"].width == 15
        _assert_geometry(table)
        assert table.max_scroll_x == 0

        await pilot.resize_terminal(200, 30)
        await pilot.pause()
        _assert_geometry(table)
        assert table.max_scroll_x == 0


@pytest.mark.usefixtures("stub_network")
async def test_rows_scrolled_into_view_are_refreshed_before_they_are_painted() -> None:
    targets = [f"host-{index:02d}.example" for index in range(60)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 20)) as pilot:
        table = await _start(pilot, app)
        assert table.scroll_offset.y == 0

        # No await from here on: the refresh timer cannot run in between.
        add(app.targets[50], ok(777.0))
        assert table.get_cell(target_row_key(50), "last").plain != "777.00"
        table.scroll_to(y=40, animate=False, immediate=True)

        assert table.get_cell(target_row_key(50), "last").plain == "777.00"
