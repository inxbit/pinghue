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


def build_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 4,
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
            status=TargetStatus.DOWN,
        )

    async def fake_probe_once(target: TargetRun, **_kwargs: Any) -> ProbeSample:
        sample = _ok_sample()
        target.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)
        probed.append(target.target)
        return sample

    # resolve_run_target is looked up in the app module; probe_once is called
    # both from the app (burst actions) and the runner (probe loop).
    monkeypatch.setattr(app_module, "resolve_run_target", fake_resolve)
    monkeypatch.setattr(app_module, "probe_once", fake_probe_once)
    monkeypatch.setattr(runner_module, "probe_once", fake_probe_once)
    return probed


async def _wait_until(pilot: Any, predicate: Any, *, ticks: int = 60) -> bool:
    for _ in range(ticks):
        if predicate():
            return True
        await pilot.pause()
    return False


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
