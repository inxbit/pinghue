"""Target table rendering and the DataTable geometry the layout relies on.

The Pilot tests pin Textual behavior that pinghue works around (scroll extent
and render caches after column width changes), so a Textual upgrade that
changes it fails here instead of on screen.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import DataTable

import pinghue.app as app_module
import pinghue.runner as runner_module
from pinghue.app import PinghueTextualApp
from pinghue.models import (
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
)
from pinghue.ui import apply_column_widths, target_row_key

T0 = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)


def ok(latency_ms: float, *, at: float = 0.0) -> ProbeSample:
    return ProbeSample(T0 + timedelta(seconds=at), latency_ms, SampleStatus.OK)


def add(target: TargetRun, sample: ProbeSample) -> None:
    target.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)


class TableApp(App[None]):
    def compose(self) -> ComposeResult:
        yield DataTable(id="targets")


def _render_widths(table: DataTable[Any]) -> int:
    return sum(column.get_render_width(table) for column in table.columns.values())


async def test_column_width_changes_drop_cached_renders() -> None:
    # DataTable's render caches are not keyed by column width.
    app = TableApp()
    async with app.run_test(size=(60, 10)) as pilot:
        table = app.query_one(DataTable)
        table.add_column("host", key="host", width=4)
        table.add_column("state", key="state", width=6)
        table.add_row(Text("aaaa"), Text("bbbb"), key="row-0")
        await pilot.pause()
        before = table.render_line(table.header_height).text.index("bbbb")

        assert apply_column_widths(table, {"host": 10, "state": 6})
        await pilot.pause()

        assert table.render_line(table.header_height).text.index("bbbb") == before + 6
        assert table.virtual_size.width == _render_widths(table)


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
