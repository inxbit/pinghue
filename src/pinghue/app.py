"""Textual application."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, Static

from pinghue import __version__
from pinghue.config import RunConfig
from pinghue.models import ProbeMode, TargetRun, TargetStatus
from pinghue.runner import (
    probe_once,
    probe_target_loop,
    resolve_run_target,
    stagger_delay,
)
from pinghue.ui import (
    COLUMN_KEYS,
    apply_column_widths,
    compute_table_layout,
    focus_table,
    format_history_legend,
    sync_target_table,
)

SLOW_LATENCY_MS = 300.0
UI_REFRESH_INTERVAL = 0.5


class PinghueTextualApp(App[None]):
    """Textual app that owns TUI state and probe tasks."""

    TITLE = f"PingHUE v{__version__}"
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
        self.exit_reason = "user_quit"
        self.show_address = False
        self._semaphore = asyncio.Semaphore(args.concurrency)
        self._probe_executor = (
            ThreadPoolExecutor(max_workers=args.concurrency)
            if mode == ProbeMode.ICMP
            else None
        )
        self._stop_event = asyncio.Event()
        self._probe_tasks: list[asyncio.Task[None]] = []
        self._resolution_task: asyncio.Task[None] | None = None
        self._resolution_target_tasks: list[asyncio.Task[tuple[int, TargetRun]]] = []
        self._immediate_events: list[asyncio.Event] = []
        self._table_initialized = False
        self._cell_cache: dict[tuple[str, str], object] = {}
        self._column_widths: dict[str, int] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield DataTable(id="targets")
        yield Static(format_history_legend(), id="history-legend")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#targets", DataTable)
        table.cursor_type = "row"
        self.targets = [
            TargetRun(target=target, status=TargetStatus.RESOLVING)
            for target in self.args_config.targets
        ]
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
        self._resolution_task = asyncio.create_task(self._resolve_targets())

    async def _resolve_one_target(self, index: int, target: str) -> tuple[int, TargetRun]:
        return index, await resolve_run_target(target, self.args_config)

    async def _resolve_targets(self) -> None:
        self._resolution_target_tasks = [
            asyncio.create_task(self._resolve_one_target(index, target.target))
            for index, target in enumerate(self.targets)
        ]

        try:
            for task in asyncio.as_completed(self._resolution_target_tasks):
                index, resolved = await task
                self.targets[index] = resolved
                self._refresh_table()
        except asyncio.CancelledError:
            for task in self._resolution_target_tasks:
                task.cancel()
            await asyncio.gather(*self._resolution_target_tasks, return_exceptions=True)
            raise

        if not self._stop_event.is_set():
            self._start_probe_tasks()

    def _focus_target_table(self) -> DataTable[Any]:
        table = self.query_one("#targets", DataTable)
        focus_table(table)
        return table

    def _start_probe_tasks(self) -> None:
        active_targets = [target for target in self.targets if target.resolved_address]
        active_count = len(active_targets)
        self._immediate_events = []

        for index, target in enumerate(active_targets):
            immediate_event = asyncio.Event()
            self._immediate_events.append(immediate_event)
            task = asyncio.create_task(
                probe_target_loop(
                    target,
                    args=self.args_config,
                    mode=self.mode,
                    semaphore=self._semaphore,
                    stop_event=self._stop_event,
                    immediate_event=immediate_event,
                    initial_delay=stagger_delay(
                        index=index,
                        count=active_count,
                        interval=self.args_config.interval,
                    ),
                    executor=self._probe_executor,
                )
            )
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

    async def _probe_selected_now(self, index: int) -> None:
        if 0 <= index < len(self.targets):
            await probe_once(
                self.targets[index],
                args=self.args_config,
                mode=self.mode,
                semaphore=self._semaphore,
                executor=self._probe_executor,
            )

    async def _probe_all_now(self) -> None:
        probes = [
            self._probe_selected_now(index)
            for index, target in enumerate(self.targets)
            if target.resolved_address
        ]
        if probes:
            await asyncio.gather(*probes)

    async def _probe_selected_and_refresh(self, index: int) -> None:
        await self._probe_selected_now(index)
        self._refresh_table()

    async def _probe_all_and_refresh(self) -> None:
        await self._probe_all_now()
        self._refresh_table()

    async def _tick(self) -> None:
        # Kept for tests and future manual refresh paths; normal probing is per-host.
        await self._probe_all_now()
        self._refresh_table()

    async def on_unmount(self) -> None:
        await self._stop_resolution_tasks()
        await self._stop_probe_tasks()
        if self._probe_executor is not None:
            self._probe_executor.shutdown(wait=True, cancel_futures=True)
            self._probe_executor = None

    def _refresh_table(self) -> None:
        table = self.query_one("#targets", DataTable)
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

    def on_click(self) -> None:
        self._focus_target_table()

    def action_toggle_address(self) -> None:
        self.show_address = not self.show_address
        self._refresh_table()

    def action_reset_selected(self) -> None:
        table = self.query_one("#targets", DataTable)
        row = table.cursor_row
        if row is not None and 0 <= row < len(self.targets):
            self.targets[row].samples.clear()
        self._refresh_table()

    def action_reset_all(self) -> None:
        for target in self.targets:
            target.samples.clear()
        self._refresh_table()

    def action_burst_selected(self) -> None:
        table = self.query_one("#targets", DataTable)
        row = table.cursor_row
        if row is not None and 0 <= row < len(self.targets):
            self.run_worker(self._probe_selected_and_refresh(row))

    def action_burst_all(self) -> None:
        self.run_worker(self._probe_all_and_refresh())

    async def action_quit(self) -> None:
        self.ended_at = datetime.now(timezone.utc)
        self.exit_reason = "user_quit"
        await self._stop_probe_tasks()
        self.exit()


class PinghueApp:
    """Small wrapper around the Textual app entry point."""

    def __init__(self, *, args: RunConfig, mode: ProbeMode) -> None:
        self.args = args
        self.mode = mode

    async def run_async(self) -> tuple[list[TargetRun], str, datetime, datetime]:
        app = PinghueTextualApp(args=self.args, mode=self.mode)
        await app.run_async()
        ended_at = (
            app.ended_at
            if app.ended_at != app.started_at
            else datetime.now(timezone.utc)
        )
        return app.targets, app.exit_reason, app.started_at, ended_at
