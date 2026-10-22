"""Run lifecycle regression tests: shutdown, pipelined startup, stagger and output."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

import pinghue.runner as runner
from pinghue.models import (
    AddressFamily,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
)
from pinghue.probes import ResolvedTarget
from pinghue.runner import (
    probe_target_loop,
    run_no_tui,
    target_run_from_resolution,
)


def no_tui_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 4,
        "count": None,
        "duration": None,
        "fail_threshold": 3,
        "interval": 0.05,
        "jitter_threshold": 50.0,
        "numeric": False,
        "output": None,
        "port": None,
        "targets": ["192.0.2.1"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def ok_sample() -> ProbeSample:
    return ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=1.0,
        status=SampleStatus.OK,
    )


def resolved(target: str, address: str | None = None) -> TargetRun:
    return target_run_from_resolution(
        ResolvedTarget(target, address or target, AddressFamily.IPV4, addresses=())
    )


async def resolve_at_once(target: str) -> TargetRun:
    return resolved(target)


def capture_stop_event(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.Event]:
    """Replace the signal handlers with a hook that exposes the run's stop event."""
    installed: list[asyncio.Event] = []

    def fake_install_stop_signal_handlers(stop_event: asyncio.Event) -> Callable[[], None]:
        installed.append(stop_event)
        return lambda: None

    monkeypatch.setattr(runner, "install_stop_signal_handlers", fake_install_stop_signal_handlers)
    return installed


async def settle(task: asyncio.Task[Any], installed: list[asyncio.Event]) -> bool:
    """Wait briefly for ``task``; on a hang, stop it so the suite fails fast."""
    done, _ = await asyncio.wait({task}, timeout=2.0)
    if task in done:
        return True
    for stop_event in installed:
        stop_event.set()
    done, _ = await asyncio.wait({task}, timeout=2.0)
    if task not in done:
        task.cancel()
        await asyncio.wait({task}, timeout=2.0)
    return False


async def applying_probe(target: TargetRun, **_: object) -> ProbeSample:
    sample = ok_sample()
    target.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)
    return sample


# Shutdown must end loops whose probe swallows its cancellation.


async def test_run_no_tui_deadline_ends_a_loop_whose_probe_swallows_the_cancel(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # tcp_probe keeps a measured success when shutdown cancels its close
    # teardown. That swallowed cancel must not keep the loop probing past the
    # deadline (the run never returned and --output was never written).
    installed = capture_stop_event(monkeypatch)
    probes = 0

    async def swallowing_probe(*_: object, **__: object) -> ProbeSample:
        nonlocal probes
        probes += 1
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0.5)  # slow close teardown
        return ok_sample()

    monkeypatch.setattr(runner, "resolve_run_target", lambda target, _args: resolve_at_once(target))
    monkeypatch.setattr(runner, "probe_once", swallowing_probe)

    run_task = asyncio.create_task(run_no_tui(no_tui_args(duration=0.05), ProbeMode.TCP))

    assert await settle(run_task, installed), "run_no_tui kept probing past its deadline"
    _, exit_reason, _, _ = run_task.result()
    assert exit_reason == "deadline"
    assert probes == 1
    # The success measured before the deadline is still reported once.
    assert capsys.readouterr().out.count(" ok latency=1.00ms") == 1


async def test_run_no_tui_deadline_ends_while_a_tcp_close_outlives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same hang through the real TCP probe: the deadline lands while the
    # connection's close teardown is still pending.
    installed = capture_stop_event(monkeypatch)
    close_wait = asyncio.StreamWriter.wait_closed

    async def slow_wait_closed(self: asyncio.StreamWriter) -> None:
        await asyncio.sleep(0.5)
        await close_wait(self)

    async def close_at_once(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    server = await asyncio.start_server(close_at_once, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(asyncio.StreamWriter, "wait_closed", slow_wait_closed)
    try:
        run_task = asyncio.create_task(
            run_no_tui(
                no_tui_args(
                    targets=["127.0.0.1"], port=port, numeric=True, duration=0.2, interval=1.0
                ),
                ProbeMode.TCP,
            )
        )
        stopped = await settle(run_task, installed)
    finally:
        server.close()

    assert stopped, "run_no_tui kept probing past its deadline"
    _, exit_reason, _, _ = run_task.result()
    assert exit_reason == "deadline"


async def test_run_no_tui_interruption_ends_sibling_loops_that_swallow_the_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A cancelled loop ends the run through the same shutdown path.
    installed = capture_stop_event(monkeypatch)

    async def fake_probe_once(target: TargetRun, **_: object) -> ProbeSample:
        if target.target == "192.0.2.2":
            await asyncio.sleep(0.02)
            raise asyncio.CancelledError
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0.5)
        return ok_sample()

    monkeypatch.setattr(runner, "resolve_run_target", lambda target, _args: resolve_at_once(target))
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    run_task = asyncio.create_task(
        run_no_tui(no_tui_args(targets=["192.0.2.1", "192.0.2.2"], interval=0.01), ProbeMode.TCP)
    )

    assert await settle(run_task, installed), "a loop that swallowed its cancel kept the run alive"
    _, exit_reason, _, _ = run_task.result()
    assert exit_reason == "interrupted"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="Task.cancelling() is new in 3.11")
async def test_probe_target_loop_ends_promptly_after_a_probe_swallows_its_cancel() -> None:
    # Without a stop event, only the task's own cancellation request can end
    # the loop; it must not sleep out another interval or probe again.
    probe_started = asyncio.Event()
    reported: list[ProbeSample | None] = []
    probes = 0

    async def swallowing_probe() -> ProbeSample:
        nonlocal probes
        probes += 1
        probe_started.set()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.sleep(0.5)
        return ok_sample()

    task = asyncio.create_task(
        probe_target_loop(
            TargetRun("192.0.2.1", resolved_address="192.0.2.1"),
            args=SimpleNamespace(count=None, interval=60.0),
            mode=ProbeMode.TCP,
            semaphore=asyncio.Semaphore(1),
            stop_event=asyncio.Event(),
            immediate_event=asyncio.Event(),
            initial_delay=0,
            on_sample=lambda _target, sample: reported.append(sample),
            probe_once_fn=swallowing_probe,
        )
    )
    await asyncio.wait_for(probe_started.wait(), timeout=1.0)
    task.cancel()

    assert await settle(task, []), "the loop kept running after its task was cancelled"
    assert probes == 1
    # The result measured before the cancellation is still reported.
    assert reported == [ok_sample()]


# Resolved targets stay pending until their first probe.


def test_target_run_from_resolution_is_pending_until_the_first_probe() -> None:
    run = resolved("service.example", "192.0.2.1")
    failed = target_run_from_resolution(ResolvedTarget("bad.example", None, None, "not found"))

    assert (run.status, run.error, run.resolved_address) == (
        TargetStatus.RESOLVING,
        None,
        "192.0.2.1",
    )
    assert (failed.status, failed.error) == (TargetStatus.DNS_FAILURE, "not found")


async def test_run_no_tui_reports_a_never_probed_target_as_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = capture_stop_event(monkeypatch)

    async def blocked_probe(*_: object, **__: object) -> ProbeSample:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(runner, "probe_once", blocked_probe)

    run_task = asyncio.create_task(run_no_tui(no_tui_args(duration=0.05), ProbeMode.ICMP))

    assert await settle(run_task, installed)
    targets, exit_reason, _, _ = run_task.result()
    assert exit_reason == "deadline"
    assert [(target.status, target.stats.sent) for target in targets] == [
        (TargetStatus.RESOLVING, 0)
    ]
    assert targets[0].resolved_address == "192.0.2.1"
    # Never-probed targets still fail closed for --fail-on-any-down.
    assert (
        runner.exit_code_for_targets(targets, fail_on_any_down=True, fail_on_all_down=False)
        == runner.EXIT_TARGETS_DOWN
    )
