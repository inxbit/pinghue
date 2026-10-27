"""Target table rendering: change tracking, the per-pass budget, the fleet
summary, and the DataTable geometry the renderer and the layout rely on.

The Pilot tests pin Textual behavior that pinghue works around (scroll extent
after column width changes, row-to-line mapping for partial repaints), so a
Textual upgrade that changes it fails here instead of on screen.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.widget import Widget
from textual.widgets import DataTable, Header
from textual.widgets._header import HeaderTitle

import pinghue.app as app_module
import pinghue.runner as runner_module
import pinghue.ui as ui
from pinghue.app import PinghueTextualApp
from pinghue.history import render_history_parts
from pinghue.models import (
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    SampleWindow,
    TargetRun,
    TargetStatus,
)
from pinghue.probes import ResolvedTarget
from pinghue.runner import record_probe_loop_failure, target_run_from_resolution
from pinghue.ui import (
    AMBER,
    COLUMN_KEYS,
    ELLIPSIS,
    MUTED,
    PENDING_LABEL,
    RED,
    FleetSummary,
    TargetTable,
    TargetTableRenderer,
    apply_column_widths,
    compute_table_layout,
    display_host_names,
    fit_host_labels,
    fleet_summary_parts,
    format_age,
    history_text,
    target_row_key,
    target_state,
    truncate_middle,
)

T0 = datetime(2026, 5, 14, 18, 32, 11, tzinfo=UTC)
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

    def clear(self) -> None:
        self.rows.clear()

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


def shown_cells(table: RecordingTable, index: int) -> list[str]:
    return [table.cell(index, column).plain for column in COLUMN_KEYS]


def fresh_cells(targets: list[TargetRun], index: int) -> list[str]:
    """Row ``index`` as a renderer that has never seen ``targets`` draws it."""
    table = RecordingTable()
    make_renderer().sync(table, targets)
    return shown_cells(table, index)


def host_labels(names: list[str], width: int | None) -> list[str]:
    """The host column the renderer draws for ``names`` at ``width``."""
    return fit_host_labels(*display_host_names(names), width)


def history_cell(samples: Any, *, width: int) -> Text:
    return history_text(
        *render_history_parts(samples, width=width, style="bar", slow_latency_ms=SLOW_MS)
    )


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
    assert shown_cells(table, 5) == fresh_cells(targets, 5)

    table.updates.clear()
    renderer.sync(table, targets)
    assert table.updates == []


def test_layout_change_rerenders_every_row() -> None:
    names = [f"db-primary-{index:02d}.dc1.example.net" for index in range(3)]
    targets = [healthy(name, *(ok(float(n)) for n in range(1, 40))) for name in names]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.sync(table, targets)

    narrow = dict(WIDTHS, host=14, history=9)
    renderer.set_layout(show_address=False, widths=narrow)
    renderer.sync(table, targets)

    for index, label in enumerate(host_labels(names, 14)):
        assert len(table.cell(index, "history").plain) == 9
        assert table.cell(index, "host").plain == label
        assert table.cell(index, "address").plain == ""


def _refresh_interval(
    renderer: TargetTableRenderer, table: RecordingTable, targets: list[TargetRun]
) -> None:
    """One refresh interval: a full pass, then the catch-up chunks it leaves."""
    renderer.sync(table, targets)
    while renderer.catch_up(table, targets):
        pass


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
            _refresh_interval(renderer, table, targets)
            passes += 1
            assert passes <= 4
        assert passes == 4


def test_a_pass_stays_within_its_budget_and_catch_up_refreshes_the_rest() -> None:
    targets = [healthy(f"host-{index}") for index in range(20)]
    table = RecordingTable()
    # With a zero budget every call refreshes exactly one row.
    renderer = make_renderer(budget_s=0.0, full_sweep_passes=4, min_rows_per_pass=1)
    renderer.sync(table, targets)
    assert not renderer.behind
    for target in targets:
        add(target, ok(44.0))
    table.updates.clear()

    renderer.sync(table, targets)

    assert table.updated_rows() == {target_row_key(0)}
    assert renderer.behind
    # The pass owes 20 / 4 rows; catch_up() refreshes the other four one by one.
    for done in range(1, 5):
        assert renderer.catch_up(table, targets) is (done < 4)
        assert table.updated_rows() == {target_row_key(index) for index in range(done + 1)}
    assert not renderer.behind
    assert renderer.catch_up(table, targets) is False
    assert table.updated_rows() == {target_row_key(index) for index in range(5)}


def test_state_changes_are_written_on_the_next_full_pass() -> None:
    targets = [healthy(f"host-{index}") for index in range(50)]
    table = RecordingTable()
    renderer = make_renderer(budget_s=0.0, full_sweep_passes=100, min_rows_per_pass=1)
    renderer.sync(table, targets)

    for target in targets:
        add(target, ok(41.0))
    targets[40].status = TargetStatus.DOWN
    summary = renderer.sync(table, targets)

    # Only one row was rebuilt this pass, but every state label is current.
    assert table.cell(40, "state").plain == "down"
    assert table.cell(40, "last").plain != "41.00"
    assert summary is not None
    assert summary.counts == {"healthy": 49, "down": 1}


def test_viewport_rows_are_refreshed_without_a_full_pass() -> None:
    targets = [healthy(f"host-{index}") for index in range(40)]
    table = RecordingTable()
    renderer = make_renderer(budget_s=0.0, full_sweep_passes=100, min_rows_per_pass=1)
    renderer.sync(table, targets)
    table.updates.clear()

    for target in targets:
        add(target, ok(43.0))
    summary = renderer.sync(table, targets, priority=range(10, 20), full=False)

    assert summary is None
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
            expected = history_cell(target.samples, width=widths["history"])
            shown = table.cell(index, "history")
            assert (shown.plain, shown.spans) == (expected.plain, expected.spans), step


def test_history_cell_uses_one_span_per_run_of_equal_styles() -> None:
    samples = [ok(5.0), ok(6.0), ok(150.0), lost(), lost(), ok(7.0)]

    cell = history_cell(samples, width=10)

    assert [(span.start, span.end, span.style) for span in cell.spans] == [
        (0, 2, ui.GREEN),
        (2, 3, AMBER),
        (3, 5, RED),
        (5, 6, ui.GREEN),
    ]


def test_resolved_target_without_results_is_pending() -> None:
    resolving = TargetRun("a.example", status=TargetStatus.RESOLVING)
    # Just resolved, or reset with "r": the runner and the app both leave the
    # target resolving with an address and no samples.
    resolved = TargetRun("a.example", resolved_address="127.0.253.1", status=TargetStatus.RESOLVING)
    probed_down = TargetRun(
        "a.example",
        resolved_address="127.0.253.1",
        status=TargetStatus.DOWN,
        samples=[lost()],
    )

    assert target_state(resolving) == ("resolving", MUTED)
    assert target_state(resolved) == (PENDING_LABEL, MUTED)
    assert target_state(probed_down) == ("down", RED)
    # Only the TUI label changes; JSON output keeps the model status.
    assert resolved.status.value == "resolving"


def test_truncate_middle_keeps_the_distinguishing_tail() -> None:
    first = truncate_middle("db-primary-01.dc1.example.net", 24)
    second = truncate_middle("db-primary-02.dc1.example.net", 24)

    assert first == f"db-pr{ELLIPSIS}01.dc1.example.net"
    assert second == f"db-pr{ELLIPSIS}02.dc1.example.net"
    assert truncate_middle("short.example", 24) == "short.example"
    assert truncate_middle("abcdef", 1) == ELLIPSIS
    assert truncate_middle("abcdef", 0) == ""


NUMBERED_FLEETS = {
    "db-primary": [f"db-primary-{index:03d}.dc1.example.net" for index in range(500)],
    "web": [f"web-{index:04d}.prod.us-east-1.example.com" for index in range(2000)],
    "two-sites": [
        f"db-primary-{index:03d}.dc{site}.example.net" for site in (1, 2) for index in range(250)
    ],
    "two-digit": [f"db-primary-{index:02d}.dc1.example.net" for index in range(40)],
}


@pytest.mark.parametrize("fleet", NUMBERED_FLEETS)
@pytest.mark.parametrize("width", [100, 120, 160])
def test_numbered_fleets_keep_every_host_label_distinct(fleet: str, width: int) -> None:
    # A fleet shares a domain suffix: the end of a name alone is not enough.
    names = NUMBERED_FLEETS[fleet]
    targets = [TargetRun(name) for name in names]
    for show_address in (False, True):
        layout = compute_table_layout(width=width, targets=targets, show_address=show_address)

        labels = host_labels(names, layout["host"])

        assert len(set(labels)) == len(names), (fleet, width, show_address)
        assert max(len(label) for label in labels) <= layout["host"]


def test_host_labels_keep_the_start_and_the_label_that_tells_hosts_apart() -> None:
    names = NUMBERED_FLEETS["two-digit"]

    assert host_labels(names, 24)[1] == f"db-primary-01.dc1.examp{ELLIPSIS}"
    assert host_labels(names, 14)[11] == f"db-primary-11{ELLIPSIS}"
    assert host_labels(NUMBERED_FLEETS["two-sites"], 14)[1] == (f"db-pr{ELLIPSIS}001.dc1{ELLIPSIS}")
    sites = [f"server-in-datacenter-frankfurt-{index:02d}" for index in range(3)]
    assert host_labels(sites, 24)[1] == f"server-in-datacenter-{ELLIPSIS}01"
    # Names that fit stay whole; with nothing shared, the end is kept.
    assert host_labels(["short.example", "x" * 30], 24) == [
        "short.example",
        truncate_middle("x" * 30, 24),
    ]
    assert host_labels(names, None) == names


def test_renderer_host_cells_tell_a_numbered_fleet_apart() -> None:
    names = NUMBERED_FLEETS["db-primary"]
    targets = [healthy(name) for name in names]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.set_layout(show_address=False, widths=dict(WIDTHS, host=14))

    renderer.sync(table, targets)

    hosts = [table.cell(index, "host").plain for index in range(len(names))]
    assert len(set(hosts)) == len(names)
    assert hosts[1] == f"db-primar{ELLIPSIS}001{ELLIPSIS}"


def test_host_labels_escape_control_characters_first() -> None:
    names = ["evil\x1b[31m-01.example.net", "evil\x1b[31m-02.example.net"]

    labels = host_labels(names, 14)

    assert all("\x1b" not in label and len(label) <= 14 for label in labels)
    assert len(set(labels)) == 2


def test_host_cell_escapes_control_characters_before_truncating() -> None:
    target = TargetRun("evil\x1b[31m" + "x" * 40 + ".example", resolved_address="127.0.253.1")
    table = RecordingTable()
    renderer = make_renderer()
    renderer.set_layout(show_address=False, widths=dict(WIDTHS, host=16))

    renderer.sync(table, [target])

    host = table.cell(0, "host").plain
    assert len(host) == 16
    assert "\x1b" not in host
    assert host.startswith("evi")
    assert host.endswith("xxxx.example")


def test_reordered_rows_show_each_target_as_it_was_drawn_before() -> None:
    fleet = [
        healthy("db-primary-001.dc1.example.net", ok(5.0)),
        TargetRun("db-primary-002.dc1.example.net", status=TargetStatus.DNS_FAILURE),
        healthy("db-primary-003.dc1.example.net", ok(250.0), lost()),
    ]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.sync(table, fleet)
    table.updates.clear()

    renderer.sync(table, fleet[::-1])

    assert [shown_cells(table, row) for row in range(3)] == [
        fresh_cells(fleet, index) for index in (2, 1, 0)
    ]
    # The middle row shows the same target as before, so it is left alone.
    assert target_row_key(1) not in table.updated_rows()


def test_fewer_rows_than_before_rebuild_the_table_with_only_those_rows() -> None:
    fleet = [healthy(f"host-{number}.example", ok(5.0 + number)) for number in range(4)]
    table = RecordingTable()
    renderer = make_renderer()
    renderer.sync(table, fleet)

    renderer.sync(table, [fleet[3], fleet[1]], fleet=fleet)

    assert list(table.rows) == [target_row_key(0), target_row_key(1)]
    assert [shown_cells(table, row) for row in range(2)] == [
        fresh_cells(fleet, index) for index in (3, 1)
    ]

    renderer.sync(table, fleet)

    assert [shown_cells(table, row) for row in range(4)] == [
        fresh_cells(fleet, index) for index in range(4)
    ]


def test_a_sync_started_while_the_table_is_cleared_leaves_one_row_per_target() -> None:
    """DataTable.clear() resets scroll_y, and Textual runs the app's scroll
    watcher, which syncs the visible rows, inside that call."""
    fleet = [healthy(f"host-{number}.example", ok(5.0)) for number in range(4)]
    renderer = make_renderer()

    class ScrollWatchingTable(RecordingTable):
        def add_row(self, *cells: Any, key: str) -> str:
            assert key not in self.rows, f"duplicate row {key}"
            return super().add_row(*cells, key=key)

        def clear(self) -> None:
            super().clear()
            renderer.sync(self, [fleet[0]], fleet=fleet, full=False)

    table = ScrollWatchingTable()
    renderer.sync(table, fleet)

    renderer.sync(table, [fleet[0]], fleet=fleet)

    assert list(table.rows) == [target_row_key(0)]
    assert shown_cells(table, 0) == fresh_cells(fleet, 0)


def test_fleet_summary_counts_the_whole_fleet_while_rows_show_part_of_it() -> None:
    fleet = [
        healthy("a.example", ok(5.0, at=9.0)),
        TargetRun(
            "b.example",
            resolved_address="127.0.253.3",
            status=TargetStatus.DOWN,
            samples=[lost(at=4.0)],
        ),
        TargetRun("c.example", status=TargetStatus.DNS_FAILURE),
    ]

    summary = make_renderer().sync(
        RecordingTable(), [fleet[0]], fleet=fleet, now=T0 + timedelta(seconds=10)
    )

    assert summary == FleetSummary(
        counts={"healthy": 1, "down": 1, "dns": 1},
        oldest_result_age_s=6.0,
    )


def test_fleet_summary_counts_states_and_the_oldest_result() -> None:
    targets = [
        healthy("healthy.example", ok(5.0, at=9.0)),
        TargetRun(
            "flappy.example",
            resolved_address="127.0.253.2",
            status=TargetStatus.INTERMITTENT,
            samples=[ok(5.0, at=4.0), lost(at=5.0)],
        ),
        TargetRun(
            "down.example",
            resolved_address="127.0.253.3",
            status=TargetStatus.DOWN,
            samples=[lost(at=8.0)],
        ),
        TargetRun("nxdomain.example", status=TargetStatus.DNS_FAILURE),
        TargetRun(
            "denied.example",
            resolved_address="127.0.253.4",
            status=TargetStatus.PERMISSION_DENIED,
        ),
        TargetRun("slow-dns.example", status=TargetStatus.RESOLVING),
        TargetRun("new.example", resolved_address="127.0.253.5", status=TargetStatus.RESOLVING),
        TargetRun("reset.example", resolved_address="127.0.253.6", status=TargetStatus.RESOLVING),
    ]
    table = RecordingTable()

    summary = make_renderer().sync(table, targets, now=T0 + timedelta(seconds=10))

    assert summary == FleetSummary(
        counts={
            "healthy": 1,
            "intermittent": 1,
            "down": 1,
            "dns": 1,
            "denied": 1,
            PENDING_LABEL: 3,
        },
        oldest_result_age_s=5.0,
    )
    parts = fleet_summary_parts(summary, stale_after_s=4.0)
    # The age first and healthy last: a narrow header cuts the end.
    assert "".join(text for text, _style in parts) == (
        "oldest 5.0s  1 down  1 dns  1 denied  1 intermittent  3 pending  1 healthy"
    )
    assert ("5.0s", AMBER) in parts
    assert ("5.0s", MUTED) in fleet_summary_parts(summary, stale_after_s=6.0)


def test_fleet_summary_without_results_has_no_age() -> None:
    summary = make_renderer().sync(
        RecordingTable(),
        [TargetRun("a.example", status=TargetStatus.RESOLVING)],
    )

    assert summary == FleetSummary(counts={PENDING_LABEL: 1}, oldest_result_age_s=None)
    assert fleet_summary_parts(summary, stale_after_s=1.0) == [("1", MUTED), (" pending", MUTED)]


def test_result_age_leaves_out_targets_that_sent_their_count() -> None:
    finished = healthy("finished.example", ok(5.0, at=1.0), ok(5.0, at=2.0))
    probing = healthy("probing.example", ok(5.0, at=8.0))
    table = RecordingTable()
    renderer = make_renderer(probe_count=2)
    now = T0 + timedelta(seconds=10)

    # With --count 2, the first target is done and no longer ages.
    summary = renderer.sync(table, [finished, probing], now=now)
    assert summary is not None
    assert summary.oldest_result_age_s == 2.0
    unlimited = make_renderer().sync(RecordingTable(), [finished, probing], now=now)
    assert unlimited is not None
    assert unlimited.oldest_result_age_s == 8.0

    add(probing, ok(5.0, at=9.0))
    summary = renderer.sync(table, [finished, probing], now=now)
    assert summary is not None
    assert summary.oldest_result_age_s is None
    assert summary.counts == {"healthy": 2}


def test_result_age_leaves_out_a_target_whose_probe_loop_died() -> None:
    live = healthy("live.example", ok(5.0, at=119.5))
    stuck = healthy("stuck.example", ok(5.0, at=0.0))
    table = RecordingTable()
    renderer = make_renderer()
    now = T0 + timedelta(seconds=120)

    # Still probed, the stale target is what the age is for.
    summary = renderer.sync(table, [live, stuck], now=now)
    assert summary == FleetSummary(counts={"healthy": 2}, oldest_result_age_s=120.0)

    # Once its probe loop is dead it shows as an error, and its last result no
    # longer says anything about whether probes keep up.
    record_probe_loop_failure(stuck, RuntimeError("boom"))
    summary = renderer.sync(table, [live, stuck], now=now)
    assert summary == FleetSummary(counts={"healthy": 1, "error": 1}, oldest_result_age_s=0.5)
    assert make_renderer().sync(RecordingTable(), [live, stuck], now=now) == summary


def test_format_age_stays_short() -> None:
    assert format_age(0.84) == "0.8s"
    assert format_age(12.4) == "12s"
    assert format_age(300.0) == "5m"
    assert format_age(5 * 3600.0) == "5h"


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


def _resolved(target: str, address: str = "127.0.253.1") -> TargetRun:
    # Resolved but not probed yet, exactly as the runner reports it.
    return target_run_from_resolution(
        ResolvedTarget(target, address, AddressFamily.IPV4, addresses=(address,))
    )


@pytest.fixture
def stub_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every target to a loopback address; probes answer at once."""

    async def fake_resolve(target: str, _args: object) -> TargetRun:
        return _resolved(target)

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
async def test_header_shows_the_fleet_summary() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 20)) as pilot:
        await _start(pilot, app)

        assert app.sub_title.startswith("oldest ")
        assert app.sub_title.endswith("  2 healthy")
        title = app.query_one(Header).format_title().plain
        assert title.startswith(f"{app.title}  oldest ")


@pytest.mark.usefixtures("stub_network")
async def test_reset_selected_shows_pending_at_once() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 20)) as pilot:
        table = await _start(pilot, app)
        assert table.get_cell(target_row_key(0), "state").plain == "healthy"

        await pilot.press("r")

        assert table.get_cell(target_row_key(0), "state").plain == PENDING_LABEL
        assert app.targets[0].status == TargetStatus.RESOLVING


def _states(table: DataTable[Any], count: int) -> list[str]:
    return [table.get_cell(target_row_key(index), "state").plain for index in range(count)]


def _full_refresh(app: PinghueTextualApp) -> None:
    app._last_full_refresh = float("-inf")
    app._refresh_table()


async def test_rows_and_fleet_summary_follow_every_way_a_run_changes_a_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The app replaces each placeholder TargetRun as its lookup completes and
    # starts that target's probe loop at once; a resolved target stays
    # RESOLVING until its first sample; a lookup that raises or a probe loop
    # that dies marks its row error; and the probe loop re-resolves a name
    # whose first lookup failed on the same TargetRun. Every change must reach
    # the row and the header counts on the next refresh.
    release_slow = asyncio.Event()
    retry_dns = asyncio.Event()
    probe_up = asyncio.Event()
    probe_late = asyncio.Event()
    real_probe_target_loop = runner_module.probe_target_loop

    async def fake_resolve(target: str, _args: object) -> TargetRun:
        if target == "bad.example":
            raise RuntimeError("resolver bug")
        if target == "late.example":
            return target_run_from_resolution(
                ResolvedTarget(target, None, None, error="Name or service not known")
            )
        if target == "slow.example":
            await release_slow.wait()
        return _resolved(target)

    async def fake_probe_target_loop(
        target: TargetRun, *, stop_event: asyncio.Event, **kwargs: Any
    ) -> None:
        if target.target == "dies.example":
            raise RuntimeError("loop bug")
        if target.target == "late.example":
            await retry_dns.wait()
            kwargs["initial_delay"] = 0.0
            await real_probe_target_loop(target, stop_event=stop_event, **kwargs)
            return
        if target.target == "up.example":
            await probe_up.wait()
            add(target, ProbeSample(datetime.now(UTC), 5.0, SampleStatus.OK))
        await stop_event.wait()

    async def fake_resolve_target_bounded(target: str, *_: Any, **__: Any) -> ResolvedTarget:
        return ResolvedTarget(target, "127.0.253.7", AddressFamily.IPV4, addresses=("127.0.253.7",))

    async def fake_probe_address(*_: Any, **__: Any) -> ProbeSample:
        await probe_late.wait()
        return ProbeSample(datetime.now(UTC), 5.0, SampleStatus.OK)

    monkeypatch.setattr(app_module, "resolve_run_target", fake_resolve)
    monkeypatch.setattr(app_module, "probe_target_loop", fake_probe_target_loop)
    monkeypatch.setattr(runner_module, "_resolve_target_bounded", fake_resolve_target_bounded)
    monkeypatch.setattr(runner_module, "_probe_address", fake_probe_address)
    names = ["up", "slow", "bad", "dies", "late", "idle"]
    targets = [f"{name}.example" for name in names]
    app = PinghueTextualApp(args=build_args(targets=targets, numeric=False), mode=ProbeMode.ICMP)
    async with app.run_test(size=(160, 20)) as pilot:
        table = app.query_one("#targets", DataTable)
        assert await _wait_until(
            pilot,
            lambda: (
                len(app._probe_tasks) == 4
                and app.targets[2].status == TargetStatus.ERROR
                and app.targets[3].status == TargetStatus.ERROR
            ),
        )
        placeholder = app.targets[1]
        _full_refresh(app)

        # Resolved but not probed shows "pending" while JSON keeps
        # "resolving"; a lookup still running shows "resolving".
        assert _states(table, 6) == ["pending", "resolving", "error", "error", "dns", "pending"]
        assert app.targets[0].status == TargetStatus.RESOLVING
        # No results yet, so no age; "resolving" counts as pending.
        assert app.sub_title == "1 dns  2 error  3 pending"

        release_slow.set()
        retry_dns.set()
        assert await _wait_until(
            pilot,
            lambda: app.targets[1] is not placeholder and app.targets[4].resolved_address,
        )
        _full_refresh(app)

        assert _states(table, 6) == ["pending"] * 2 + ["error"] * 2 + ["pending"] * 2
        assert app.targets[4].status == TargetStatus.RESOLVING
        assert app.sub_title == "2 error  4 pending"

        probe_up.set()
        probe_late.set()
        assert await _wait_until(pilot, lambda: app.targets[0].samples and app.targets[4].samples)
        _full_refresh(app)

        assert _states(table, 6) == ["healthy", "pending", "error", "error", "healthy", "pending"]
        assert table.get_cell(target_row_key(4), "last").plain == "5.00"
        assert app.sub_title.startswith("oldest ")
        assert app.sub_title.endswith("  2 error  2 pending  2 healthy")

        await pilot.press("a")
        assert table.get_cell(target_row_key(4), "address").plain == "127.0.253.7"
        assert table.get_cell(target_row_key(1), "address").plain == "127.0.253.1"


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


def _show_every_state(app: PinghueTextualApp) -> None:
    """Spread the targets over every summary category, ten hosts each."""
    states = [
        TargetStatus.HEALTHY,
        TargetStatus.INTERMITTENT,
        TargetStatus.DOWN,
        TargetStatus.DNS_FAILURE,
        TargetStatus.PERMISSION_DENIED,
        TargetStatus.ERROR,
        TargetStatus.RESOLVING,
    ]
    for index, target in enumerate(app.targets):
        target.status = states[index % len(states)]
    app._last_full_refresh = float("-inf")
    app._refresh_table()


@pytest.mark.usefixtures("stub_network")
@pytest.mark.parametrize("width", [100, 120])
async def test_header_keeps_the_result_age_on_screen_with_every_state(width: int) -> None:
    targets = [f"host-{index:02d}.example" for index in range(70)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(width, 20)) as pilot:
        await _start(pilot, app)
        _show_every_state(app)
        await pilot.pause()

        assert app.sub_title.startswith("oldest ")
        assert "10 down  10 dns  10 denied  10 error" in app.sub_title
        title = app.query_one(HeaderTitle)
        shown = title.render_lines(Region(0, 0, title.size.width, 1))[0].text
        # The header is too narrow for the whole summary, but the age and the
        # problem states come before the cut.
        assert ELLIPSIS in shown
        assert f"{app.title}  oldest " in shown
        assert "10 down" in shown


@pytest.mark.usefixtures("stub_network")
async def test_scroll_position_is_clamped_when_the_table_gets_narrower_or_fits() -> None:
    targets = [f"db-primary-{index:02d}.dc1.example.net" for index in range(40)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(70, 30)) as pilot:
        table = await _start(pilot, app)
        await pilot.press("a")
        await pilot.pause()
        table.scroll_to(x=10_000, animate=False, immediate=True)
        await pilot.pause()
        wide_scroll = table.scroll_x
        assert wide_scroll == table.max_scroll_x > 0

        # Hiding the address column again shrinks the extent under the
        # scroll position.
        await pilot.press("a")
        await pilot.pause()
        assert 0 < table.max_scroll_x < wide_scroll
        assert table.scroll_x == table.max_scroll_x

        table.scroll_to(x=10_000, animate=False, immediate=True)
        await pilot.pause()
        await pilot.resize_terminal(160, 30)
        await pilot.pause()
        assert table.max_scroll_x == 0
        assert table.scroll_x == 0


async def test_refresh_rows_repaints_the_whole_table_with_fixed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = TableApp()
    async with app.run_test(size=(60, 20)) as pilot:
        table = app.query_one(TargetTable)
        table.add_column("host", key="host", width=12)
        for index in range(30):
            table.add_row(Text(f"row {index}"), key=f"row-{index}")
        table.fixed_rows = 1
        await pilot.pause()

        calls: list[tuple[Region, ...]] = []
        original_refresh = Widget.refresh

        def recording_refresh(widget: Widget, *regions: Region, **kwargs: Any) -> Any:
            if widget is table:
                calls.append(regions)
            return original_refresh(widget, *regions, **kwargs)

        monkeypatch.setattr(Widget, "refresh", recording_refresh)
        # Fixed rows break the row-to-line mapping, so the whole table is
        # repainted instead.
        table.refresh_rows([3, 4])

        assert calls == [()]


@pytest.mark.usefixtures("stub_network")
async def test_rows_a_full_pass_leaves_are_refreshed_by_catch_up_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module, "ROW_CATCH_UP_DELAY_S", 0.001)
    targets = [f"host-{index:02d}.example" for index in range(60)]
    app = PinghueTextualApp(args=build_args(targets=targets), mode=ProbeMode.ICMP)
    async with app.run_test(size=(140, 20)) as pilot:
        table = await _start(pilot, app)
        renderer = app._table_renderer
        catch_ups: list[bool] = []
        catch_up = renderer.catch_up

        def counting_catch_up(*args: Any) -> bool:
            catch_ups.append(catch_up(*args))
            return catch_ups[-1]

        monkeypatch.setattr(renderer, "catch_up", counting_catch_up)
        # With a zero budget a full pass refreshes the viewport and one more
        # row; the rest of the table is left to the catch-up chunks.
        renderer.budget_s = 0.0
        for target in app.targets:
            add(target, ok(321.0))
        app._last_full_refresh = float("-inf")
        app._refresh_table()
        assert renderer.behind
        assert table.get_cell(target_row_key(59), "last").plain != "321.00"

        assert await _wait_until(pilot, lambda: not renderer.behind, ticks=200)
        assert len(catch_ups) > 1
        assert catch_ups[-1] is False
        assert all(
            table.get_cell(target_row_key(index), "last").plain == "321.00" for index in range(60)
        )
