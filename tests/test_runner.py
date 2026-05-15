from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import pinghue.runner as runner
from pinghue.models import AddressFamily, ProbeMode, TargetRun, TargetStatus


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 1,
        "fail_on_down": False,
        "fail_threshold": 3,
        "history_style": "bar",
        "host_label": "maintenance-window",
        "interval": 1.0,
        "jitter_threshold": 50.0,
        "no_samples": False,
        "no_tui": True,
        "numeric": False,
        "output": None,
        "port": None,
        "targets": ["1.1.1.1"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_run_writes_configured_host_label(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return [], "completed", timestamp, timestamp

    def fake_write_output_json(_: Path, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)
    monkeypatch.setattr(runner, "write_output_json", fake_write_output_json)

    exit_code = await runner.run(
        build_args(output=tmp_path / "out.json", host_label="operator-selected"),
        mode=ProbeMode.ICMP,
    )

    assert exit_code == 0
    assert captured["host"] == "operator-selected"


async def test_run_fail_on_down_returns_nonzero_when_all_targets_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun("1.1.1.1", status=TargetStatus.DOWN)

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return [target], "completed", timestamp, timestamp

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    exit_code = await runner.run(build_args(fail_on_down=True), mode=ProbeMode.ICMP)

    assert exit_code == 2


async def test_run_fail_on_down_returns_zero_when_any_target_is_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        TargetRun("1.1.1.1", status=TargetStatus.DOWN),
        TargetRun("8.8.8.8", status=TargetStatus.HEALTHY),
    ]

    async def fake_run_no_tui(
        _: object,
        __: ProbeMode,
    ) -> tuple[list[TargetRun], str, datetime, datetime]:
        timestamp = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
        return targets, "completed", timestamp, timestamp

    monkeypatch.setattr(runner, "run_no_tui", fake_run_no_tui)

    exit_code = await runner.run(build_args(fail_on_down=True), mode=ProbeMode.ICMP)

    assert exit_code == 0
