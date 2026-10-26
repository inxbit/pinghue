"""Integration smoke tests that drive the real Textual app via its Pilot harness.

Unlike test_app_structure.py (which fakes the table and monkeypatches query_one),
these run the app end-to-end: real widgets, real key dispatch, real task lifecycle.
Network is the only thing stubbed, so the tests stay hermetic and fast.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from textual.widgets import DataTable, Header, Input, Static

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


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 4,
        "count": None,
        "duration": None,
        "fail_threshold": 3,
        "history_style": "bar",
        # A long interval means each probe loop fires exactly one immediate
        # probe and then idles, so sample counts stay deterministic in tests.
        "interval": 60.0,
        "jitter_threshold": 50.0,
        "port": None,
        "targets": ["a.example", "b.example"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ok_sample() -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=5.0,
        status=SampleStatus.OK,
    )


@pytest.fixture
def stub_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub DNS resolution and probing; return the list of probed target names."""
    probed: list[str] = []

    async def fake_resolve(target: str, _args: object) -> TargetRun:
        return TargetRun(
            target=target,
            resolved_address="1.1.1.1",
            resolved_family=AddressFamily.IPV4,
            resolved_addresses=("1.1.1.1",),
            # Resolved but not yet probed, as target_run_from_resolution reports.
            status=TargetStatus.RESOLVING,
        )

    async def fake_probe_once(target: TargetRun, **_kwargs: Any) -> ProbeSample:
        sample = _ok_sample()
        target.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)
        probed.append(target.target)
        return sample

    # resolve_run_target is looked up in the app module; probe_once runs
    # inside the runner's per-target probe loop.
    monkeypatch.setattr(app_module, "resolve_run_target", fake_resolve)
    monkeypatch.setattr(runner_module, "probe_once", fake_probe_once)
    return probed


async def _wait_until(pilot: Any, predicate: Any, *, ticks: int = 60) -> bool:
    for _ in range(ticks):
        if predicate():
            return True
        await pilot.pause()
    return False


@pytest.mark.usefixtures("stub_network")
async def test_pilot_count_exits_after_requested_probe_count() -> None:
    app = PinghueTextualApp(
        args=build_args(count=1, targets=["a.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test() as pilot:
        completed = await _wait_until(pilot, lambda: app.exit_reason == "completed")

    assert completed
    assert app.exit_reason == "completed"
    assert len(app.targets[0].samples) == 1


@pytest.mark.usefixtures("stub_network")
async def test_pilot_duration_exits_at_deadline() -> None:
    app = PinghueTextualApp(
        args=build_args(duration=0.01, targets=["a.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test() as pilot:
        deadline = await _wait_until(pilot, lambda: app.exit_reason == "deadline")

    assert deadline
    assert app.exit_reason == "deadline"


@pytest.mark.usefixtures("stub_network")
async def test_pilot_mounts_and_renders_a_row_per_target() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        assert table.row_count == 2
        assert len(app.targets) == 2


@pytest.mark.usefixtures("stub_network")
async def test_pilot_quit_binding_exits_with_user_quit_reason() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app.exit_reason == "user_quit"


@pytest.mark.usefixtures("stub_network")
async def test_pilot_reset_selected_clears_the_selected_targets_samples() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test() as pilot:
        got_sample = await _wait_until(pilot, lambda: len(app.targets[0].samples) > 0)
        assert got_sample, "background probe loop never recorded a sample"

        await pilot.press("r")
        await pilot.pause()

        assert len(app.targets[0].samples) == 0
        # L6: a cleared target shows a pending state, not DOWN (red), until the
        # next probe arrives.
        assert app.targets[0].status == TargetStatus.RESOLVING


async def test_pilot_burst_selected_triggers_an_immediate_probe(
    stub_network: list[str],
) -> None:
    probed = stub_network
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test() as pilot:
        await _wait_until(pilot, lambda: len(probed) >= len(app.targets))
        baseline = len(probed)

        await pilot.press("b")
        await _wait_until(pilot, lambda: len(probed) > baseline)

        assert len(probed) > baseline


@pytest.mark.usefixtures("stub_network")
async def test_pilot_arrow_navigation_recovers_after_header_click() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["a.example", "b.example", "c.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        assert table.cursor_row == 0

        await pilot.click(Header, offset=(0, 0))
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()

        assert table.cursor_row == 1


@pytest.mark.usefixtures("stub_network")
async def test_pilot_arrow_navigation_recovers_after_bottom_corner_click() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["a.example", "b.example", "c.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        assert table.cursor_row == 0

        await pilot.click(None, offset=(99, 23))
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()

        assert table.cursor_row == 1


def _hosts(table: DataTable[Any]) -> list[str]:
    return [table.get_row_at(row)[0].plain for row in range(table.row_count)]


async def _probe_every_target(pilot: Any) -> None:
    """First probes are spread over the interval; probe every target now."""
    app = pilot.app
    assert await _wait_until(pilot, lambda: all(t.resolved_address for t in app.targets))
    await pilot.press("B")
    assert await _wait_until(pilot, lambda: all(len(t.samples) for t in app.targets))


async def _click_header(pilot: Any, column: str) -> None:
    """Click a column header: the header row sits just inside the top border."""
    table = pilot.app.query_one("#targets", DataTable)
    x = 1
    for key, header in table.columns.items():
        if key.value == column:
            break
        x += header.get_render_width(table)
    await pilot.click("#targets", offset=(x + 1, 1))
    await pilot.pause()


@pytest.mark.usefixtures("stub_network")
async def test_pilot_header_clicks_sort_by_ip_reverse_then_restore_the_input_order() -> None:
    targets = ["10.1.10.10", "10.1.9.9", "10.1.1.2"]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        assert _hosts(table) == targets

        await _click_header(pilot, "host")
        assert _hosts(table) == ["10.1.1.2", "10.1.9.9", "10.1.10.10"]
        assert table.ordered_columns[0].label.plain == "host ▲"

        await _click_header(pilot, "host")
        assert _hosts(table) == ["10.1.10.10", "10.1.9.9", "10.1.1.2"]
        assert table.ordered_columns[0].label.plain == "host ▼"

        await _click_header(pilot, "host")
        assert _hosts(table) == targets
        assert table.ordered_columns[0].label.plain == "host"


@pytest.mark.usefixtures("stub_network")
async def test_pilot_reset_after_sorting_clears_the_highlighted_host() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["b.example", "c.example", "a.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await _probe_every_target(pilot)
        table = app.query_one("#targets", DataTable)

        await _click_header(pilot, "host")
        assert _hosts(table)[0] == "a.example"
        await pilot.press("r")
        await pilot.pause()

        # a.example is the third target but the first row once sorted.
        assert [len(target.samples) > 0 for target in app.targets] == [True, True, False]


@pytest.mark.usefixtures("stub_network")
async def test_pilot_search_filters_rows_without_triggering_bindings() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["db-1.example", "web.example", "db-2.example", "queue.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await _probe_every_target(pilot)
        table = app.query_one("#targets", DataTable)

        await pilot.press("slash", "d", "b")
        await pilot.pause()
        assert _hosts(table) == ["db-1.example", "db-2.example"]

        # Typed into the search, not the q (quit) and a (address) bindings.
        await pilot.press("backspace", "backspace", "a")
        await pilot.pause()
        assert app.show_address is False
        await pilot.press("backspace", "q", "u", "e")
        await pilot.pause()
        assert app.is_running
        assert _hosts(table) == ["queue.example"]

        await pilot.press("enter")
        await pilot.pause()
        assert table.has_focus
        assert _hosts(table) == ["queue.example"]
        # The header keeps counting the whole fleet.
        assert app.sub_title.endswith("  4 healthy")

        await pilot.press("escape")
        await pilot.pause()
        assert _hosts(table) == ["db-1.example", "web.example", "db-2.example", "queue.example"]


@pytest.mark.usefixtures("stub_network")
async def test_pilot_escape_in_the_search_box_clears_it_and_returns_to_the_table() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["db-1.example", "web.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        search = app.query_one("#search", Input)

        await pilot.press("slash", "w", "e", "b")
        await pilot.pause()
        assert _hosts(table) == ["web.example"]
        assert search.has_focus

        await pilot.press("escape")
        await pilot.pause()
        assert _hosts(table) == ["db-1.example", "web.example"]
        assert search.value == ""
        assert table.has_focus
        assert not search.is_on_screen


@pytest.mark.usefixtures("stub_network")
async def test_pilot_the_number_pads_symbol_keys_type_into_the_search() -> None:
    # iTerm2 sends the number pad's keys in the kitty keyboard protocol
    # without their text. Textual turns its digits into characters but its
    # ., -, +, *, /, = and , into named keys with none, so the search box
    # ignored them.
    app = PinghueTextualApp(
        args=build_args(targets=["10.0.0.5", "10.0.0.6", "db-1.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        search = app.query_one("#search", Input)

        await pilot.press("slash", "1", "0", "decimal", "0", "decimal", "0", "decimal", "5")
        await pilot.pause()
        assert search.value == "10.0.0.5"
        assert _hosts(table) == ["10.0.0.5"]

        await pilot.press("escape", "slash", "d", "b", "subtract", "1")
        await pilot.pause()
        assert search.value == "db-1"

        await pilot.press("escape", "slash", "add", "multiply", "divide", "equal", "separator")
        await pilot.pause()
        assert search.value == "+*/=,"


@pytest.mark.usefixtures("stub_network")
async def test_pilot_clicking_the_search_box_keeps_it_focused() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["db-1.example", "web.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        search = app.query_one("#search", Input)
        await pilot.press("slash", "d", "b", "enter")
        await pilot.pause()
        assert table.has_focus

        await pilot.click("#search")
        await pilot.press("end", "backspace", "backspace", "w")
        await pilot.pause()

        assert search.has_focus
        assert _hosts(table) == ["web.example"]


@pytest.mark.usefixtures("stub_network")
async def test_pilot_sorting_by_address_shows_the_address_column() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        assert app.show_address is False

        await _click_header(pilot, "address")

        assert app.show_address is True


@pytest.mark.usefixtures("stub_network")
async def test_pilot_a_sorted_table_follows_the_values_as_they_change() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["a.example", "b.example", "c.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await _probe_every_target(pilot)
        table = app.query_one("#targets", DataTable)
        await _click_header(pilot, "loss")
        assert _hosts(table) == ["a.example", "b.example", "c.example"]

        lost = ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 12, tzinfo=timezone.utc),
            latency_ms=None,
            status=SampleStatus.TIMEOUT,
        )
        app.targets[2].apply_sample(lost, fail_threshold=3, jitter_threshold_ms=50.0)

        assert await _wait_until(pilot, lambda: _hosts(table)[0] == "c.example", ticks=200), (
            "the sort did not follow the new loss"
        )


@pytest.mark.usefixtures("stub_network")
async def test_pilot_escape_without_a_search_leaves_the_selection_alone() -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["a.example", "b.example", "c.example"]),
        mode=ProbeMode.ICMP,
    )
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        await pilot.press("down", "down", "escape")
        await pilot.pause()

        assert table.cursor_row == 2


def _footer_actions(app: PinghueTextualApp) -> set[str]:
    return {
        active.binding.action
        for active in app.active_bindings.values()
        if active.enabled and active.binding.show
    }


@pytest.mark.usefixtures("stub_network")
async def test_pilot_the_footer_offers_clearing_only_while_a_search_is_open() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        assert "clear_search" not in _footer_actions(app)

        await pilot.press("slash", "a", "enter")
        await pilot.pause()
        assert "clear_search" in _footer_actions(app)

        await pilot.press("escape")
        await pilot.pause()
        assert "clear_search" not in _footer_actions(app)


@pytest.mark.usefixtures("stub_network")
async def test_pilot_searching_a_scrolled_table_rebuilds_it_cleanly() -> None:
    # Every stubbed host resolves to 1.1.1.1, so the search term must not
    # match that address: the first key has to shrink the scrolled table.
    targets = [f"{'web' if number % 2 else 'db'}-{number:03}.example" for number in range(120)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)
        table.move_cursor(row=len(targets) - 1, animate=False)
        await pilot.pause()
        assert table.scroll_y > 0

        await pilot.press("slash", "w")
        await pilot.pause()

        assert app.is_running
        assert _hosts(table) == [name for name in targets if "w" in name]


@pytest.mark.usefixtures("stub_network")
async def test_pilot_a_search_with_no_match_empties_the_table_until_cleared() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        table = app.query_one("#targets", DataTable)

        await pilot.press("slash", "z", "z", "z")
        await pilot.pause()
        assert table.row_count == 0
        assert str(app.query_one("#search-count", Static).render()) == "0 of 2"

        await pilot.press("escape")
        await pilot.pause()
        assert _hosts(table) == ["a.example", "b.example"]
