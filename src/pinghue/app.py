"""Textual application."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.events import Click, Key
from textual.widgets import DataTable, Footer, Header, Static

from pinghue import __version__
from pinghue.config import RunConfig
from pinghue.models import ProbeMode, SampleWindow, TargetRun, TargetStatus
from pinghue.runner import (
    _monotonic_time,
    _pending_target_runs,
    apply_retained_sample_budget,
    first_probe_delay,
    install_stop_signal_callback,
    probe_target_loop,
    record_probe_loop_failure,
    resolve_run_target,
    stagger_window,
)
from pinghue.ui import (
    COLUMN_KEYS,
    apply_column_widths,
    compute_table_layout,
    focus_table,
    format_history_legend,
    sync_target_table,
)

SLOW_LATENCY_MS = 100.0
UI_REFRESH_INTERVAL = 0.5
# Lookups that are already done (IP literals) drain without suspending; give
# input and rendering a turn this often while starting their probe loops.
RESOLUTION_YIELD_EVERY = 256


def _note_probe_loop_failure(target: TargetRun, task: asyncio.Task[None]) -> None:
    # Without this a loop that died on an unexpected error would leave its row
    # frozen on its last state for the rest of the run.
    if not task.cancelled() and (error := task.exception()) is not None:
        record_probe_loop_failure(target, error)


class PinghueTextualApp(App[None]):
    """Textual app that owns TUI state and probe tasks."""

    TITLE = f"PingHUE v{__version__}"
    ENABLE_COMMAND_PALETTE = False
    CSS = """
    Screen {
        background: #101418;
        color: #e6edf3;
    }

    DataTable {
        background: #101418;
        color: #e6edf3;
        border: solid #2a313a;
        height: 1fr;
    }

    DataTable > .datatable--cursor {
        background: #58a6ff 22%;
        color: #e6edf3;
        text-style: none;
    }

    DataTable:focus > .datatable--cursor {
        background: #58a6ff 32%;
        color: #e6edf3;
        text-style: none;
    }

    DataTable > .datatable--hover {
        background: #58a6ff 14%;
    }

    DataTable > .datatable--header {
        color: #8ea0b8;
    }

    #history-legend {
        background: #101418;
        color: #8ea0b8;
        height: 1;
        padding: 0 1;
    }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("a", "toggle_address", "Address"),
        Binding("r", "reset_selected", "Reset"),
        Binding("R", "reset_all", "Reset all"),
        Binding("b", "burst_selected", "Probe now"),
        Binding("B", "burst_all", "Probe all"),
    ]

    def __init__(self, *, args: RunConfig, mode: ProbeMode) -> None:
        super().__init__()
        self.args_config = args
        self.mode = mode
        self.targets: list[TargetRun] = []
        self.started_at = datetime.now(timezone.utc)
        self.ended_at = self.started_at
        self._deadline_at = None if args.duration is None else _monotonic_time() + args.duration
        self.exit_reason = "user_quit"
        self.show_address = False
        self._semaphore = asyncio.Semaphore(args.concurrency)
        self._stop_event = asyncio.Event()
        self._probe_tasks: list[asyncio.Task[None]] = []
        self._resolution_task: asyncio.Task[None] | None = None
        self._resolution_target_tasks: list[asyncio.Task[tuple[int, TargetRun]]] = []
        self._deadline_task: asyncio.Task[None] | None = None
        self._completion_task: asyncio.Task[None] | None = None
        self._immediate_events: list[asyncio.Event] = []
        self._table_initialized = False
        self._cell_cache: dict[tuple[str, str], object] = {}
        self._column_widths: dict[str, int] = {}
        self._finishing = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="targets")
        if self.args_config.history_style != "none":
            yield Static(
                format_history_legend(self.args_config.history_style),
                id="history-legend",
            )
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#targets", DataTable)
        table.cursor_type = "row"
        self.targets = [
            TargetRun(target=target, status=TargetStatus.RESOLVING)
            for target in self.args_config.targets
        ]
        apply_retained_sample_budget(self.targets)
        self._column_widths = compute_table_layout(
            width=max(self.size.width, 1),
            targets=self.targets,
            show_address=self.show_address,
        )
        for key in COLUMN_KEYS:
            table.add_column(key, key=key, width=self._column_widths[key])
        self._focus_target_table()
        self._refresh_table()
        self.set_interval(UI_REFRESH_INTERVAL, self._refresh_table)
        if self.args_config.duration is not None:
            self._deadline_task = asyncio.create_task(self._wait_for_deadline())
        # One "probe now" event per row from the start, so b/B address rows by
        # index even while some targets are still resolving.
        self._immediate_events = [asyncio.Event() for _ in self.targets]
        # A stop signal that arrived during startup may have finished the app.
        if not self._finishing:
            self._resolution_task = asyncio.create_task(self._resolve_targets())

    async def _resolve_one_target(self, index: int, target: str) -> tuple[int, TargetRun]:
        return index, await resolve_run_target(target, self.args_config)

    async def _resolve_targets(self) -> None:
        """Resolve targets and start each probe loop as soon as its target resolves.

        Rows are redrawn by the UI_REFRESH_INTERVAL timer rather than once per
        resolved target, which would rebuild the whole table N times.
        """
        run_start = _monotonic_time()
        window = stagger_window(
            interval=self.args_config.interval,
            remaining=None if self._deadline_at is None else self._deadline_at - run_start,
        )
        self._resolution_target_tasks = [
            asyncio.create_task(self._resolve_one_target(index, target.target))
            for index, target in enumerate(self.targets)
        ]

        try:
            for drained, task in enumerate(
                asyncio.as_completed(self._resolution_target_tasks), start=1
            ):
                index, resolved = await task
                resolved.samples = SampleWindow(
                    resolved.samples,
                    maxlen=self.targets[index].samples.maxlen,
                )
                self.targets[index] = resolved
                if not self._stop_event.is_set():
                    self._start_probe_task(index, resolved, run_start=run_start, window=window)
                if drained % RESOLUTION_YIELD_EVERY == 0:
                    await asyncio.sleep(0)
        except asyncio.CancelledError:
            for task in self._resolution_target_tasks:
                task.cancel()
            await asyncio.gather(*self._resolution_target_tasks, return_exceptions=True)
            raise

        # Every loop exists now, so --count completion waits for all of them,
        # including loops whose targets resolved last.
        if self.args_config.count is not None and not self._stop_event.is_set():
            self._completion_task = asyncio.create_task(self._finish_when_probe_tasks_complete())

    def _focus_target_table(self) -> DataTable[Any]:
        table = self.query_one("#targets", DataTable)
        focus_table(table)
        return table

    def _start_probe_task(
        self,
        index: int,
        target: TargetRun,
        *,
        run_start: float,
        window: float,
    ) -> None:
        task = asyncio.create_task(
            probe_target_loop(
                target,
                args=self.args_config,
                mode=self.mode,
                semaphore=self._semaphore,
                stop_event=self._stop_event,
                immediate_event=self._immediate_events[index],
                initial_delay=first_probe_delay(
                    index=index,
                    count=len(self.targets),
                    window=window,
                    elapsed=_monotonic_time() - run_start,
                ),
            )
        )
        task.add_done_callback(partial(_note_probe_loop_failure, target))
        self._probe_tasks.append(task)

    async def _stop_probe_tasks(self) -> None:
        self._stop_event.set()
        for event in self._immediate_events:
            event.set()

        for task in self._probe_tasks:
            task.cancel()

        if self._probe_tasks:
            await asyncio.gather(*self._probe_tasks, return_exceptions=True)

    async def _stop_resolution_tasks(self) -> None:
        if self._resolution_task is None:
            return

        self._resolution_task.cancel()
        await asyncio.gather(self._resolution_task, return_exceptions=True)
        self._resolution_task = None

    async def _stop_background_task(self, task: asyncio.Task[None] | None) -> None:
        if task is None or task.done() or task is asyncio.current_task():
            return

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _wait_for_deadline(self) -> None:
        deadline_at = self._deadline_at
        if deadline_at is None:
            return

        remaining = max(0.0, deadline_at - _monotonic_time())
        if remaining == 0:
            if not self._stop_event.is_set():
                await self._finish("deadline")
            return

        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            await self._finish("deadline")

    async def _finish_when_probe_tasks_complete(self) -> None:
        if not self._probe_tasks:
            return

        await asyncio.gather(*self._probe_tasks, return_exceptions=True)
        if not self._stop_event.is_set():
            await self._finish("completed")

    async def _finish(self, exit_reason: str) -> None:
        if self._finishing:
            return

        self._finishing = True
        self.ended_at = datetime.now(timezone.utc)
        self.exit_reason = exit_reason
        # Stop first so no lookup that completes meanwhile starts a new loop,
        # then cancel outstanding lookups and every loop already started.
        self._stop_event.set()
        await self._stop_resolution_tasks()
        await self._stop_probe_tasks()
        self.exit()

    async def on_unmount(self) -> None:
        await self._stop_resolution_tasks()
        await self._stop_probe_tasks()
        await self._stop_background_task(self._deadline_task)
        await self._stop_background_task(self._completion_task)

    def _refresh_table(self) -> None:
        try:
            table = self.query_one("#targets", DataTable)
        except NoMatches:
            return
        column_widths = compute_table_layout(
            width=max(self.size.width, 1),
            targets=self.targets,
            show_address=self.show_address,
        )
        if column_widths != self._column_widths:
            apply_column_widths(table, column_widths)
            self._column_widths = column_widths

        self._table_initialized = sync_target_table(
            table,
            self.targets,
            initialized=self._table_initialized,
            show_address=self.show_address,
            mode=self.mode.value,
            history_width=self._column_widths["history"],
            history_style=self.args_config.history_style,
            jitter_threshold_ms=self.args_config.jitter_threshold,
            slow_latency_ms=SLOW_LATENCY_MS,
            cell_cache=self._cell_cache,
        )

    def on_click(self, event: Click) -> None:
        event.prevent_default()
        self._focus_target_table()

    def on_key(self, event: Key) -> None:
        if event.key not in {"up", "down"}:
            return

        table = self._focus_target_table()
        if table.row_count == 0:
            return

        if event.key == "up":
            table.action_cursor_up()
        else:
            table.action_cursor_down()

        event.prevent_default()

    def action_toggle_address(self) -> None:
        self.show_address = not self.show_address
        self._refresh_table()

    def action_reset_selected(self) -> None:
        table = self.query_one("#targets", DataTable)
        row = table.cursor_row
        if row is not None and 0 <= row < len(self.targets):
            self._reset_target_samples(self.targets[row])
        self._refresh_table()

    def action_reset_all(self) -> None:
        for target in self.targets:
            self._reset_target_samples(target)
        self._refresh_table()

    def _reset_target_samples(self, target: TargetRun) -> None:
        had_samples = len(target.samples) > 0
        target.samples.clear()
        if not had_samples:
            return

        # An empty window has no evidence yet; show a pending state rather than
        # DOWN (what classify_samples returns for no samples) until the next probe.
        target.status = TargetStatus.RESOLVING
        target.error = None

    def action_burst_selected(self) -> None:
        # Wake the selected target's existing probe loop instead of starting a
        # second concurrent probe on the same TargetRun (which double-counts).
        table = self.query_one("#targets", DataTable)
        row = table.cursor_row
        if row is not None and 0 <= row < len(self._immediate_events):
            self._immediate_events[row].set()

    def action_burst_all(self) -> None:
        for event in self._immediate_events:
            event.set()

    async def action_quit(self) -> None:
        await self._finish("user_quit")


class PinghueApp:
    """Small wrapper around the Textual app entry point."""

    def __init__(self, *, args: RunConfig, mode: ProbeMode) -> None:
        self.args = args
        self.mode = mode

    async def run_async(self) -> tuple[list[TargetRun], str, datetime, datetime]:
        app = PinghueTextualApp(args=self.args, mode=self.mode)
        finish_tasks: list[asyncio.Task[None]] = []

        def interrupt() -> None:
            # Textual handles only job-control signals itself; finishing through
            # the app restores the terminal and lets run() write --output.
            if not finish_tasks:
                finish_tasks.append(asyncio.create_task(app._finish("interrupted")))

        # Installed around the whole app run, so a signal that arrives during
        # startup is handled too, and removed again once the app has exited.
        cleanup_signal_handlers = install_stop_signal_callback(interrupt)
        try:
            await app.run_async()
            await asyncio.gather(*finish_tasks, return_exceptions=True)
        finally:
            cleanup_signal_handlers()
        # A signal before mount ends the app before it builds its target list.
        targets = app.targets or _pending_target_runs(self.args.targets)
        ended_at = app.ended_at if app.ended_at != app.started_at else datetime.now(timezone.utc)
        return targets, app.exit_reason, app.started_at, ended_at
