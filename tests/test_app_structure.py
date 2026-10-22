import asyncio
import os
import signal
from collections.abc import Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import pinghue.app as app_module
from pinghue.app import PinghueApp, PinghueTextualApp
from pinghue.models import (
    MAX_TOTAL_RETAINED_SAMPLES,
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
)
from pinghue.probes import ResolvedTarget
from pinghue.runner import target_run_from_resolution


def test_tui_app_class_is_module_scoped() -> None:
    assert PinghueTextualApp.__module__ == "pinghue.app"


def test_tui_omits_history_legend_when_history_is_disabled() -> None:
    app = PinghueTextualApp(
        args=build_args(history_style="none"),
        mode=ProbeMode.ICMP,
    )

    assert all(getattr(widget, "id", None) != "history-legend" for widget in app.compose())


def test_tui_slow_latency_threshold_matches_the_fixed_scale() -> None:
    assert app_module.SLOW_LATENCY_MS == 100.0


async def test_tui_duration_includes_time_elapsed_before_deadline_task(
    monkeypatch,
) -> None:
    now = 100.0
    waits: list[float] = []
    reasons: list[str] = []

    monkeypatch.setattr(app_module, "_monotonic_time", lambda: now, raising=False)
    app = PinghueTextualApp(
        args=build_args(duration=10.0),
        mode=ProbeMode.ICMP,
    )
    now = 103.0

    async def fake_wait_for(awaitable: Any, *, timeout: float) -> None:
        awaitable.close()
        waits.append(timeout)
        raise asyncio.TimeoutError

    async def fake_finish(reason: str) -> None:
        reasons.append(reason)

    monkeypatch.setattr(app_module.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(app, "_finish", fake_finish)

    await app._wait_for_deadline()

    assert waits == [7.0]
    assert reasons == ["deadline"]


async def test_tui_resolution_preserves_aggregate_sample_budget(monkeypatch) -> None:
    target_count = 200
    app = PinghueTextualApp(
        args=build_args(targets=[f"192.0.2.{index}" for index in range(target_count)]),
        mode=ProbeMode.ICMP,
    )
    app.targets = [
        TargetRun(target, status=TargetStatus.RESOLVING) for target in app.args_config.targets
    ]
    app_module.apply_retained_sample_budget(app.targets)

    started: list[int] = []

    async def fake_resolve_one(index: int, target: str) -> tuple[int, TargetRun]:
        return index, TargetRun(target, resolved_address=target)

    def record_start(index: int, _target: TargetRun, **_kwargs: object) -> None:
        started.append(index)

    monkeypatch.setattr(app, "_resolve_one_target", fake_resolve_one)
    monkeypatch.setattr(app, "_start_probe_task", record_start)

    await app._resolve_targets()

    assert {target.samples.maxlen for target in app.targets} == {
        MAX_TOTAL_RETAINED_SAMPLES // target_count
    }
    assert sorted(started) == list(range(target_count))


async def test_tui_preserves_aggregate_budget_during_partial_resolution(
    monkeypatch,
) -> None:
    target_count = 200
    app = PinghueTextualApp(
        args=build_args(targets=[f"host-{index}" for index in range(target_count)]),
        mode=ProbeMode.ICMP,
    )
    table = FakeTable()

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        if target == "host-0":
            return TargetRun(target, resolved_address="192.0.2.1")
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(app, "set_interval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "resolve_run_target", fake_resolve_run_target)

    await app.on_mount()
    for _ in range(100):
        if app.targets[0].resolved_address is not None:
            break
        await asyncio.sleep(0)

    expected_window = MAX_TOTAL_RETAINED_SAMPLES // target_count
    assert app.targets[0].resolved_address == "192.0.2.1"
    assert {target.samples.maxlen for target in app.targets} == {expected_window}
    await app.on_unmount()


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 1,
        "count": None,
        "duration": None,
        "fail_threshold": 3,
        "history_style": "bar",
        "interval": 1.0,
        "jitter_threshold": 50.0,
        "numeric": False,
        "port": None,
        "targets": ["example.com"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class FakeTable:
    def __init__(self, *, cursor_row: int | None = None) -> None:
        self.cursor_row = cursor_row
        self.cursor_type = ""
        self.added_columns: list[str] = []
        self.added_rows: list[tuple[str, tuple[object, ...]]] = []
        self.updated_cells: list[tuple[str, str, object, bool]] = []
        self.focused = False
        self.columns: dict[str, Any] = {}

    def add_column(self, _label: str, *, key: str, width: int) -> None:
        _ = width
        self.added_columns.append(key)

    def add_row(self, *cells: object, key: str) -> None:
        self.added_rows.append((key, cells))

    def update_cell(
        self,
        row_key: str,
        column_key: str,
        cell: object,
        *,
        update_width: bool,
    ) -> None:
        self.updated_cells.append((row_key, column_key, cell, update_width))

    def focus(self) -> None:
        self.focused = True

    @property
    def row_count(self) -> int:
        return len(self.added_rows)

    def action_cursor_up(self) -> None:
        self.cursor_row = 0 if self.cursor_row is None else max(self.cursor_row - 1, 0)

    def action_cursor_down(self) -> None:
        self.cursor_row = 0 if self.cursor_row is None else self.cursor_row + 1


def test_tui_uses_datatable_native_navigation() -> None:
    bound_actions = {binding.action for binding in PinghueTextualApp.BINDINGS}

    assert "table_cursor_up" not in bound_actions
    assert "table_cursor_down" not in bound_actions
    assert "table_page_up" not in bound_actions
    assert "table_page_down" not in bound_actions


def test_reset_selected_ignores_missing_cursor_row(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    app.targets = [TargetRun("1.1.1.1")]
    table = FakeTable(cursor_row=None)
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(app, "_refresh_table", lambda: None)

    app.action_reset_selected()

    assert app.targets[0].samples == []


def test_reset_selected_recomputes_target_state(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    target = TargetRun(
        "1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    target.apply_sample(
        ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=10.0,
            status=SampleStatus.OK,
        ),
        fail_threshold=3,
        jitter_threshold_ms=50.0,
    )
    target.apply_sample(
        ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 12, tzinfo=timezone.utc),
            latency_ms=None,
            status=SampleStatus.TIMEOUT,
            error="timeout",
        ),
        fail_threshold=3,
        jitter_threshold_ms=50.0,
    )
    assert target.status == TargetStatus.INTERMITTENT
    app.targets = [target]
    table = FakeTable(cursor_row=0)
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(app, "_refresh_table", lambda: None)

    app.action_reset_selected()

    assert app.targets[0].samples == []
    assert app.targets[0].status == TargetStatus.RESOLVING
    assert app.targets[0].error is None


def test_reset_all_recomputes_all_target_states(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
    targets = [
        TargetRun(
            "1.1.1.1",
            resolved_address="1.1.1.1",
            resolved_family=AddressFamily.IPV4,
        ),
        TargetRun(
            "8.8.8.8",
            resolved_address="8.8.8.8",
            resolved_family=AddressFamily.IPV4,
        ),
    ]
    for target in targets:
        target.apply_sample(
            ProbeSample(
                timestamp=timestamp,
                latency_ms=5.0,
                status=SampleStatus.OK,
            ),
            fail_threshold=3,
            jitter_threshold_ms=50.0,
        )
    app.targets = targets
    monkeypatch.setattr(app, "_refresh_table", lambda: None)

    app.action_reset_all()

    assert [list(target.samples) for target in app.targets] == [[], []]
    assert [target.status for target in app.targets] == [
        TargetStatus.RESOLVING,
        TargetStatus.RESOLVING,
    ]


def test_burst_selected_wakes_only_the_selected_target_loop(monkeypatch) -> None:
    # L1: bursting must wake the existing per-target probe loop (via its
    # immediate event) rather than spawn a second concurrent probe on the same
    # TargetRun, which double-counts samples.
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    app.targets = [
        TargetRun("1.1.1.1", resolved_address="1.1.1.1"),
        TargetRun("8.8.8.8", resolved_address="8.8.8.8"),
    ]
    app._immediate_events = [asyncio.Event(), asyncio.Event()]
    table = FakeTable(cursor_row=1)
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)

    app.action_burst_selected()

    assert app._immediate_events[1].is_set()
    assert not app._immediate_events[0].is_set()


def test_burst_all_wakes_every_target_loop() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    app.targets = [
        TargetRun("1.1.1.1", resolved_address="1.1.1.1"),
        TargetRun("8.8.8.8", resolved_address="8.8.8.8"),
    ]
    app._immediate_events = [asyncio.Event(), asyncio.Event()]

    app.action_burst_all()

    assert all(event.is_set() for event in app._immediate_events)


class _TestEvent:
    def __init__(self, key: str) -> None:
        self.key = key
        self.prevented = False

    def prevent_default(self) -> None:
        self.prevented = True


def test_on_click_focuses_target_table(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    table = FakeTable()
    app.targets = [TargetRun("example.com", resolved_address="example.com")]
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)

    app.on_click(_TestEvent("click"))  # type: ignore[arg-type]

    assert table.focused is True


def test_on_key_moves_cursor_when_up_down(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    table = FakeTable(cursor_row=0)
    table.added_rows.append(("r0", ()))
    table.added_rows.append(("r1", ()))
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)

    app.on_key(_TestEvent("up"))  # type: ignore[arg-type]
    app.on_key(_TestEvent("down"))  # type: ignore[arg-type]

    assert table.focused is True
    assert table.cursor_row == 1


def test_on_key_non_navigation_is_ignored() -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    event = _TestEvent("x")

    app.on_key(event)  # type: ignore[arg-type]

    assert event.prevented is False


async def test_on_mount_renders_targets_before_dns_resolution(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(targets=["slow.example"]), mode=ProbeMode.ICMP)
    table = FakeTable()
    resolve_started = asyncio.Event()

    async def fake_resolve_run_target(*_: object, **__: object) -> TargetRun:
        resolve_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(app, "set_interval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "resolve_run_target", fake_resolve_run_target)

    await app.on_mount()

    assert app.targets[0].status == TargetStatus.RESOLVING
    assert table.added_rows
    await asyncio.wait_for(resolve_started.wait(), timeout=1.0)
    await app.on_unmount()


async def _eventually(predicate: Callable[[], object], *, turns: int = 200) -> bool:
    for _ in range(turns):
        if predicate():
            return True
        await asyncio.sleep(0)
    return bool(predicate())


def _resolved(target: str, address: str) -> TargetRun:
    return target_run_from_resolution(ResolvedTarget(target, address, AddressFamily.IPV4))


def _mount_with_fakes(
    monkeypatch: pytest.MonkeyPatch,
    app: PinghueTextualApp,
    *,
    resolve: Callable[..., Any],
    probe_loop: Callable[..., Any],
) -> FakeTable:
    table = FakeTable()
    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(app, "set_interval", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "exit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app_module, "resolve_run_target", resolve)
    monkeypatch.setattr(app_module, "probe_target_loop", probe_loop)
    return table


async def test_tui_resolution_does_not_redraw_the_table_per_target(monkeypatch) -> None:
    # A full-table refresh per resolved target made startup O(N^2) and
    # stalled the event loop for seconds before any probe started.
    targets = [f"127.0.{index // 250}.{index % 250 + 1}" for index in range(1000)]
    app = PinghueTextualApp(args=build_args(targets=targets, interval=60.0), mode=ProbeMode.ICMP)
    started: list[str] = []
    loops_created_before_the_first_ran: list[int] = []

    async def fake_probe_target_loop(target: TargetRun, *, stop_event: Any, **_: Any) -> None:
        if not started:
            loops_created_before_the_first_ran.append(len(app._probe_tasks))
        started.append(target.target)
        await stop_event.wait()

    _mount_with_fakes(
        monkeypatch,
        app,
        resolve=app_module.resolve_run_target,
        probe_loop=fake_probe_target_loop,
    )
    original_refresh = app._refresh_table
    refreshes = 0

    def counting_refresh() -> None:
        nonlocal refreshes
        refreshes += 1
        original_refresh()

    monkeypatch.setattr(app, "_refresh_table", counting_refresh)

    await app.on_mount()
    assert app._resolution_task is not None
    await asyncio.wait_for(app._resolution_task, timeout=10.0)
    assert await _eventually(lambda: len(started) == len(targets))

    # Only the initial render on mount; the interval timer redraws after that.
    assert refreshes <= 1
    assert sorted(started) == sorted(targets)
    # Lookups of IP literals finish without suspending; draining them must
    # still give other work (input, rendering, started loops) a turn.
    assert loops_created_before_the_first_ran[0] < len(targets)
    await app.on_unmount()


async def test_tui_keeps_starting_rows_after_one_lookup_raises(monkeypatch) -> None:
    # An unexpected error from one lookup used to end the whole drain: every
    # row not drained yet stayed "resolving" and a -c run never completed.
    app = PinghueTextualApp(
        args=build_args(targets=["192.0.2.1", "bad.example", "192.0.2.3"], count=1),
        mode=ProbeMode.ICMP,
    )
    started: list[str] = []

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        if target == "bad.example":
            raise RuntimeError("resolver bug")
        return _resolved(target, target)

    async def fake_probe_target_loop(
        target: TargetRun, *, stop_event: asyncio.Event, **_: Any
    ) -> None:
        started.append(target.target)
        await stop_event.wait()

    _mount_with_fakes(
        monkeypatch,
        app,
        resolve=fake_resolve_run_target,
        probe_loop=fake_probe_target_loop,
    )

    await app.on_mount()
    assert app._resolution_task is not None
    await asyncio.wait_for(app._resolution_task, timeout=2.0)
    assert await _eventually(lambda: len(started) == 2)

    assert started == ["192.0.2.1", "192.0.2.3"]
    assert [target.target for target in app.targets] == ["192.0.2.1", "bad.example", "192.0.2.3"]
    assert app.targets[1].status == TargetStatus.ERROR
    assert app.targets[1].error == "Unexpected probe loop error: resolver bug"
    # --count completion is armed for the loops that did start.
    assert app._completion_task is not None
    await app.on_unmount()


async def test_tui_starts_each_probe_loop_as_its_target_resolves(monkeypatch) -> None:
    # IP literals are probed while a slow lookup is still pending, and
    # --count completion still waits for the loop of the target resolved last.
    app = PinghueTextualApp(
        args=build_args(targets=["slow.example", "192.0.2.1", "192.0.2.2"], count=1),
        mode=ProbeMode.ICMP,
    )
    release_slow = asyncio.Event()
    started: dict[str, tuple[asyncio.Event, float]] = {}

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        if target == "slow.example":
            await release_slow.wait()
            return _resolved(target, "192.0.2.9")
        return _resolved(target, target)

    async def fake_probe_target_loop(
        target: TargetRun,
        *,
        stop_event: asyncio.Event,
        immediate_event: asyncio.Event,
        initial_delay: float,
        **_: Any,
    ) -> None:
        started[target.target] = (immediate_event, initial_delay)
        await stop_event.wait()

    _mount_with_fakes(
        monkeypatch,
        app,
        resolve=fake_resolve_run_target,
        probe_loop=fake_probe_target_loop,
    )

    await app.on_mount()
    # One "probe now" event per row exists before any target resolves.
    assert len(app._immediate_events) == 3
    assert await _eventually(lambda: set(started) == {"192.0.2.1", "192.0.2.2"})

    assert app._completion_task is None
    assert app.targets[0].resolved_address is None
    # Resolved but not yet probed is pending, not DOWN.
    assert [target.status for target in app.targets] == [TargetStatus.RESOLVING] * 3
    assert started["192.0.2.1"][0] is app._immediate_events[1]
    assert started["192.0.2.2"][0] is app._immediate_events[2]
    # First probes keep their slots across one interval from a fixed start.
    assert 0.2 < started["192.0.2.1"][1] <= 1 / 3
    assert 0.5 < started["192.0.2.2"][1] <= 2 / 3

    release_slow.set()
    assert await _eventually(lambda: "slow.example" in started)
    assert started["slow.example"][0] is app._immediate_events[0]
    assert app.targets[0].resolved_address == "192.0.2.9"
    assert app._completion_task is not None
    assert len(app._probe_tasks) == 3
    await app.on_unmount()


async def test_tui_finish_cancels_pending_lookups_and_started_loops(monkeypatch) -> None:
    app = PinghueTextualApp(
        args=build_args(targets=["slow.example", "192.0.2.1"]),
        mode=ProbeMode.ICMP,
    )
    lookup_cancelled = asyncio.Event()
    loop_cancelled = asyncio.Event()

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        if target == "slow.example":
            try:
                await asyncio.Event().wait()
            finally:
                lookup_cancelled.set()
        return _resolved(target, target)

    async def fake_probe_target_loop(*_: Any, **__: Any) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            loop_cancelled.set()

    _mount_with_fakes(
        monkeypatch,
        app,
        resolve=fake_resolve_run_target,
        probe_loop=fake_probe_target_loop,
    )

    await app.on_mount()
    assert await _eventually(lambda: app._probe_tasks)
    await asyncio.wait_for(app._finish("deadline"), timeout=2.0)

    assert app.exit_reason == "deadline"
    assert lookup_cancelled.is_set()
    assert loop_cancelled.is_set()
    # The completed lookup is kept in place and requested order is preserved.
    assert [target.target for target in app.targets] == ["slow.example", "192.0.2.1"]
    assert [target.resolved_address for target in app.targets] == [None, "192.0.2.1"]
    await app.on_unmount()


async def test_tui_marks_a_row_whose_probe_loop_dies(monkeypatch) -> None:
    # A loop that ends on an unexpected error must not leave its row frozen on
    # its last state while the other rows keep updating.
    app = PinghueTextualApp(
        args=build_args(targets=["192.0.2.1", "192.0.2.2"]),
        mode=ProbeMode.ICMP,
    )

    async def fake_probe_target_loop(
        target: TargetRun, *, stop_event: asyncio.Event, **_: Any
    ) -> None:
        if target.target == "192.0.2.1":
            raise RuntimeError("loop bug")
        await stop_event.wait()

    _mount_with_fakes(
        monkeypatch,
        app,
        resolve=app_module.resolve_run_target,
        probe_loop=fake_probe_target_loop,
    )

    await app.on_mount()
    assert await _eventually(lambda: app.targets[0].status == TargetStatus.ERROR)

    assert app.targets[0].error == "Unexpected probe loop error: loop bug"
    assert app.targets[1].status == TargetStatus.RESOLVING
    await app.on_unmount()


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP", "SIGINT"])
async def test_tui_finishes_as_interrupted_on_termination_signals(
    monkeypatch, signal_name: str
) -> None:
    # Textual handles only job-control signals, so SIGTERM/SIGHUP used to
    # kill the TUI without restoring the terminal or writing --output.
    signal_number = getattr(signal, signal_name, None)
    if signal_number is None:
        pytest.skip(f"{signal_name} is not available on this platform")
    finished = asyncio.Event()
    original_finish = PinghueTextualApp._finish

    async def fake_run_async(_app: PinghueTextualApp) -> None:
        os.kill(os.getpid(), signal_number)
        await asyncio.wait({asyncio.ensure_future(finished.wait())}, timeout=2.0)

    async def recording_finish(self: PinghueTextualApp, exit_reason: str) -> None:
        await original_finish(self, exit_reason)
        finished.set()

    monkeypatch.setattr(PinghueTextualApp, "run_async", fake_run_async)
    monkeypatch.setattr(PinghueTextualApp, "_finish", recording_finish)
    monkeypatch.setattr(PinghueTextualApp, "exit", lambda *_args, **_kwargs: None)
    # Safety net: a missing handler fails the assertions below instead of
    # letting the signal's default action end the test process.
    previous = signal.signal(signal_number, lambda *_: None)
    try:
        targets, exit_reason, started_at, ended_at = await PinghueApp(
            args=build_args(targets=["a.example", "b.example"]),
            mode=ProbeMode.ICMP,
        ).run_async()
        handler_after_run = signal.getsignal(signal_number)
    finally:
        signal.signal(signal_number, previous)

    assert exit_reason == "interrupted"
    assert ended_at >= started_at
    # The app never mounted, so the report still lists every requested target.
    assert [target.target for target in targets] == ["a.example", "b.example"]
    assert [target.status for target in targets] == [TargetStatus.RESOLVING] * 2
    # The handlers are removed once the app has exited.
    assert handler_after_run in (signal.SIG_DFL, signal.default_int_handler)
