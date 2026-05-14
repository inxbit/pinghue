"""Textual application."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from pinghue import __version__
from pinghue.models import ProbeMode, TargetRun
from pinghue.runner import (
    _probe_once,
    _resolve_runs,
    probe_target_loop,
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


class PinghueApp:
    """Small wrapper that imports Textual only when the TUI is used."""

    def __init__(self, *, args: Any, mode: ProbeMode) -> None:
        self.args = args
        self.mode = mode

    async def run_async(self) -> tuple[list[TargetRun], str, datetime, datetime]:
        try:
            from textual.app import App, ComposeResult
            from textual.binding import Binding
            from textual.widgets import DataTable, Footer, Header, Static
        except ImportError as exc:  # pragma: no cover - dependency packaging guard
            raise RuntimeError(
                "Textual is required for TUI mode. Use --no-tui without Textual."
            ) from exc

        outer = self

        class _App(App[None]):
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
                Binding("up", "table_cursor_up", show=False, priority=True),
                Binding("down", "table_cursor_down", show=False, priority=True),
                Binding("pageup", "table_page_up", show=False, priority=True),
                Binding("pagedown", "table_page_down", show=False, priority=True),
                Binding("q", "quit", "Quit"),
                Binding("a", "toggle_address", "Address"),
                Binding("r", "reset_selected", "Reset"),
                Binding("R", "reset_all", "Reset all"),
                Binding("b", "burst_selected", "Probe now"),
                Binding("B", "burst_all", "Probe all"),
            ]

            def __init__(self) -> None:
                super().__init__()
                self.targets: list[TargetRun] = []
                self.started_at = datetime.now(timezone.utc)
                self.ended_at = self.started_at
                self.exit_reason = "user_quit"
                self.show_address = False
                self._semaphore = asyncio.Semaphore(outer.args.concurrency)
                self._stop_event = asyncio.Event()
                self._probe_tasks: list[asyncio.Task[None]] = []
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
                self.targets = await _resolve_runs(outer.args)
                self._column_widths = compute_table_layout(
                    width=max(self.size.width, 1),
                    targets=self.targets,
                    show_address=self.show_address,
                )
                for key in COLUMN_KEYS:
                    table.add_column(key, key=key, width=self._column_widths[key])
                self._focus_target_table()
                self._start_probe_tasks()
                self._refresh_table()
                self.set_interval(UI_REFRESH_INTERVAL, self._refresh_table)

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
                            args=outer.args,
                            mode=outer.mode,
                            semaphore=self._semaphore,
                            stop_event=self._stop_event,
                            immediate_event=immediate_event,
                            initial_delay=stagger_delay(
                                index=index,
                                count=active_count,
                                interval=outer.args.interval,
                            ),
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

            async def _probe_selected_now(self, index: int) -> None:
                if 0 <= index < len(self.targets):
                    await _probe_once(
                        self.targets[index],
                        args=outer.args,
                        mode=outer.mode,
                        semaphore=self._semaphore,
                    )

            async def _probe_all_now(self) -> None:
                probes = [
                    self._probe_selected_now(index)
                    for index, target in enumerate(self.targets)
                    if target.resolved_address
                ]
                if probes:
                    await asyncio.gather(*probes)

            async def _tick(self) -> None:
                # Kept for tests and future manual refresh paths; normal probing is per-host.
                await self._probe_all_now()
                self._refresh_table()

            async def on_unmount(self) -> None:
                await self._stop_probe_tasks()

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
                    mode=outer.mode.value,
                    history_width=self._column_widths["history"],
                    history_style=outer.args.history_style,
                    jitter_threshold_ms=outer.args.jitter_threshold,
                    slow_latency_ms=SLOW_LATENCY_MS,
                    cell_cache=self._cell_cache,
                )

            def on_click(self) -> None:
                self._focus_target_table()

            def action_table_cursor_up(self) -> None:
                self._focus_target_table().action_cursor_up()

            def action_table_cursor_down(self) -> None:
                self._focus_target_table().action_cursor_down()

            def action_table_page_up(self) -> None:
                self._focus_target_table().action_page_up()

            def action_table_page_down(self) -> None:
                self._focus_target_table().action_page_down()

            def action_toggle_address(self) -> None:
                self.show_address = not self.show_address
                self._refresh_table()

            def action_reset_selected(self) -> None:
                table = self.query_one("#targets", DataTable)
                if 0 <= table.cursor_row < len(self.targets):
                    self.targets[table.cursor_row].samples.clear()
                self._refresh_table()

            def action_reset_all(self) -> None:
                for target in self.targets:
                    target.samples.clear()
                self._refresh_table()

            async def action_burst_selected(self) -> None:
                table = self.query_one("#targets", DataTable)
                await self._probe_selected_now(table.cursor_row)
                self._refresh_table()

            async def action_burst_all(self) -> None:
                await self._probe_all_now()
                self._refresh_table()

            async def action_quit(self) -> None:
                self.ended_at = datetime.now(timezone.utc)
                self.exit_reason = "user_quit"
                await self._stop_probe_tasks()
                self.exit()

        app = _App()
        await app.run_async()
        ended_at = (
            app.ended_at
            if app.ended_at != app.started_at
            else datetime.now(timezone.utc)
        )
        return app.targets, app.exit_reason, app.started_at, ended_at
