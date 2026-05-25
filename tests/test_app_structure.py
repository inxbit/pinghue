import asyncio
from types import SimpleNamespace
from typing import Any

import pinghue.app as app_module
from pinghue.app import PinghueTextualApp
from pinghue.models import AddressFamily, ProbeMode, TargetRun, TargetStatus


def test_tui_app_class_is_module_scoped() -> None:
    assert PinghueTextualApp.__module__ == "pinghue.app"


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 1,
        "fail_threshold": 3,
        "history_style": "bar",
        "interval": 1.0,
        "jitter_threshold": 50.0,
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


def test_burst_selected_schedules_background_worker(monkeypatch) -> None:
    app = PinghueTextualApp(args=build_args(), mode=ProbeMode.ICMP)
    app.targets = [TargetRun("1.1.1.1", resolved_address="1.1.1.1")]
    table = FakeTable(cursor_row=0)
    scheduled: list[object] = []

    def fake_run_worker(coro: object) -> None:
        scheduled.append(coro)
        if hasattr(coro, "close"):
            coro.close()

    monkeypatch.setattr(app, "query_one", lambda *_args, **_kwargs: table)
    monkeypatch.setattr(app, "run_worker", fake_run_worker)

    app.action_burst_selected()

    assert len(scheduled) == 1


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
