"""Run lifecycle regression tests: shutdown, pipelined startup, stagger and output."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import io
import json
import os
import signal
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import pinghue.runner as runner
from pinghue.cli import parse_args
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
    first_probe_delay,
    probe_target_loop,
    run_no_tui,
    stagger_window,
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


# Probing starts per target as soon as that target resolves.


async def test_run_no_tui_probes_resolved_targets_while_lookups_are_pending(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    installed = capture_stop_event(monkeypatch)
    lookup_cancelled = asyncio.Event()
    probed = asyncio.Event()

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        if target == "slow.example":
            try:
                await asyncio.Event().wait()
            finally:
                lookup_cancelled.set()
        return resolved(target)

    async def recording_probe(target: TargetRun, **kwargs: object) -> ProbeSample:
        sample = await applying_probe(target, **kwargs)
        probed.set()
        return sample

    monkeypatch.setattr(runner, "resolve_run_target", fake_resolve_run_target)
    monkeypatch.setattr(runner, "probe_once", recording_probe)

    run_task = asyncio.create_task(
        run_no_tui(no_tui_args(targets=["slow.example", "192.0.2.1"]), ProbeMode.ICMP)
    )
    try:
        await asyncio.wait({asyncio.ensure_future(probed.wait())}, timeout=1.0)
        probed_before_lookup_finished = probed.is_set()
    finally:
        installed[0].set()
    assert await settle(run_task, installed)

    assert probed_before_lookup_finished
    targets, exit_reason, _, _ = run_task.result()
    assert exit_reason == "interrupted"
    # Completed lookups are kept in place and requested order is preserved.
    assert [target.target for target in targets] == ["slow.example", "192.0.2.1"]
    assert [target.status for target in targets] == [
        TargetStatus.RESOLVING,
        TargetStatus.HEALTHY,
    ]
    assert lookup_cancelled.is_set()
    assert "192.0.2.1 ok latency=1.00ms" in capsys.readouterr().out


async def test_run_no_tui_count_waits_for_targets_that_resolve_late(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = capture_stop_event(monkeypatch)
    probed: list[str] = []

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        if target == "slow.example":
            await asyncio.sleep(0.05)
        return resolved(target, "192.0.2.9" if target == "slow.example" else None)

    async def recording_probe(target: TargetRun, **kwargs: object) -> ProbeSample:
        probed.append(target.target)
        return await applying_probe(target, **kwargs)

    monkeypatch.setattr(runner, "resolve_run_target", fake_resolve_run_target)
    monkeypatch.setattr(runner, "probe_once", recording_probe)

    run_task = asyncio.create_task(
        run_no_tui(
            no_tui_args(targets=["slow.example", "192.0.2.1"], count=2, interval=0.01),
            ProbeMode.ICMP,
        )
    )

    assert await settle(run_task, installed)
    targets, exit_reason, _, _ = run_task.result()
    assert exit_reason == "completed"
    assert sorted(probed) == ["192.0.2.1", "192.0.2.1", "slow.example", "slow.example"]
    assert [target.stats.sent for target in targets] == [2, 2]


async def test_ip_literals_do_not_queue_behind_dns_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def queued_lookup(*_: object, **__: object) -> ResolvedTarget:
        pytest.fail("an IP literal waited for a resolver slot")

    monkeypatch.setattr(runner, "_resolve_target_bounded", queued_lookup)

    literal = await runner.resolve_run_target("2001:db8::1", no_tui_args())
    numeric_name = await runner.resolve_run_target("service.example", no_tui_args(numeric=True))

    assert (literal.status, literal.resolved_address, literal.resolved_family) == (
        TargetStatus.RESOLVING,
        "2001:db8::1",
        AddressFamily.IPV6,
    )
    assert literal._last_resolve_time is not None
    assert (numeric_name.status, numeric_name.error) == (
        TargetStatus.DNS_FAILURE,
        "--numeric requires an IP literal",
    )


# No-TUI first probes are staggered like the TUI.


def test_stagger_window_compresses_for_short_durations() -> None:
    assert stagger_window(interval=1.0, remaining=None) == 1.0
    assert stagger_window(interval=1.0, remaining=10.0) == 1.0
    assert stagger_window(interval=1.0, remaining=0.5) == 0.25
    assert stagger_window(interval=1.0, remaining=-1.0) == 0.0


def test_first_probe_delay_keeps_slots_from_a_fixed_start() -> None:
    assert first_probe_delay(index=2, count=4, window=1.0, elapsed=0.2) == pytest.approx(0.3)
    # A target that resolves after its slot has passed starts at once.
    assert first_probe_delay(index=1, count=4, window=1.0, elapsed=0.9) == 0.0


async def _record_initial_delays(
    monkeypatch: pytest.MonkeyPatch, **overrides: object
) -> dict[str, float]:
    delays: dict[str, float] = {}

    async def fake_probe_target_loop(target: TargetRun, *, initial_delay: float, **_: Any) -> None:
        delays[target.target] = initial_delay

    monkeypatch.setattr(runner, "probe_target_loop", fake_probe_target_loop)
    _, exit_reason, _, _ = await run_no_tui(no_tui_args(**overrides), ProbeMode.ICMP)
    assert exit_reason == "completed"
    return delays


async def test_run_no_tui_staggers_first_probes_across_the_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [f"192.0.2.{index}" for index in range(1, 5)]

    delays = await _record_initial_delays(monkeypatch, targets=targets, count=1, interval=1.0)

    assert delays[targets[0]] == 0.0
    for target, slot in zip(targets[1:], (0.25, 0.5, 0.75), strict=True):
        assert slot - 0.1 < delays[target] <= slot


async def test_run_no_tui_short_duration_compresses_the_stagger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every host must get a first probe well before a short deadline.
    targets = [f"192.0.2.{index}" for index in range(1, 5)]

    delays = await _record_initial_delays(monkeypatch, targets=targets, duration=0.4, interval=1.0)

    assert max(delays.values()) <= 0.15


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


# Defensive handling: one target's failure neither freezes it nor ends the run.


async def test_probe_target_loop_records_an_unexpected_probe_error_and_keeps_probing() -> None:
    target = TargetRun("192.0.2.1", resolved_address="192.0.2.1")
    reported: list[ProbeSample | None] = []
    calls = 0

    async def flaky_probe() -> ProbeSample:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("socket layer exploded")
        return ok_sample()

    await asyncio.wait_for(
        probe_target_loop(
            target,
            args=SimpleNamespace(count=2, interval=0.01, fail_threshold=3, jitter_threshold=50.0),
            mode=ProbeMode.ICMP,
            semaphore=asyncio.Semaphore(1),
            stop_event=asyncio.Event(),
            immediate_event=asyncio.Event(),
            initial_delay=0,
            on_sample=lambda _target, sample: reported.append(sample),
            probe_once_fn=flaky_probe,
        ),
        timeout=1.0,
    )

    assert calls == 2
    first = reported[0]
    assert first is not None
    assert first.status == SampleStatus.ERROR
    assert first.error == "Unexpected probe error: socket layer exploded"
    # Recorded like any failed probe, so the row shows the failure and its text.
    assert list(target.samples) == [first]
    assert target.error == first.error


async def test_run_no_tui_keeps_running_when_one_target_loop_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = capture_stop_event(monkeypatch)
    real_probe_target_loop = runner.probe_target_loop

    async def failing_loop(target: TargetRun, **kwargs: Any) -> None:
        if target.target == "192.0.2.1":
            raise RuntimeError("loop bug")
        await real_probe_target_loop(target, **kwargs)

    monkeypatch.setattr(runner, "probe_target_loop", failing_loop)
    monkeypatch.setattr(runner, "probe_once", applying_probe)

    run_task = asyncio.create_task(
        run_no_tui(no_tui_args(targets=["192.0.2.1", "192.0.2.2"], count=1), ProbeMode.ICMP)
    )

    assert await settle(run_task, installed)
    targets, exit_reason, _, _ = run_task.result()
    assert exit_reason == "completed"
    assert [target.status for target in targets] == [TargetStatus.ERROR, TargetStatus.HEALTHY]
    assert targets[0].error == "Unexpected probe loop error: loop bug"


# SIGHUP ends a no-TUI run cleanly, unless it was ignored at startup.


def test_stop_signals_include_hangup_unless_it_is_ignored() -> None:
    hangup = getattr(signal, "SIGHUP", None)
    if hangup is None:
        pytest.skip("SIGHUP is not available on this platform")
    previous = signal.signal(hangup, signal.SIG_DFL)
    try:
        assert set(runner.stop_signals()) == {signal.SIGINT, signal.SIGTERM, hangup}
        signal.signal(hangup, signal.SIG_IGN)
        assert set(runner.stop_signals()) == {signal.SIGINT, signal.SIGTERM}
    finally:
        signal.signal(hangup, previous)


async def test_stop_signal_handlers_leave_an_ignored_hangup_ignored() -> None:
    # nohup: the run must keep going after the terminal goes away.
    hangup = getattr(signal, "SIGHUP", None)
    if hangup is None:
        pytest.skip("SIGHUP is not available on this platform")
    previous = signal.signal(hangup, signal.SIG_IGN)
    try:
        cleanup = runner.install_stop_signal_handlers(asyncio.Event())
        installed_handler = signal.getsignal(hangup)
        cleanup()
        restored_handler = signal.getsignal(hangup)
    finally:
        signal.signal(hangup, previous)

    assert installed_handler is signal.SIG_IGN
    assert restored_handler is signal.SIG_IGN


async def test_run_no_tui_stops_cleanly_on_sighup(monkeypatch: pytest.MonkeyPatch) -> None:
    hangup = getattr(signal, "SIGHUP", None)
    if hangup is None:
        pytest.skip("SIGHUP is not available on this platform")
    probe_started = asyncio.Event()

    async def blocked_probe(*_: object, **__: object) -> ProbeSample:
        probe_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    monkeypatch.setattr(runner, "probe_once", blocked_probe)
    # Safety net: without the handler the signal is caught here and the run
    # times out below, instead of the default action ending the test process.
    previous = signal.signal(hangup, lambda *_: None)
    try:
        run_task = asyncio.create_task(run_no_tui(no_tui_args(timeout=30.0), ProbeMode.ICMP))
        await asyncio.wait_for(probe_started.wait(), timeout=1.0)
        os.kill(os.getpid(), hangup)
        stopped = await settle(run_task, [])
    finally:
        signal.signal(hangup, previous)

    assert stopped, "SIGHUP did not stop the no-TUI run"
    _, exit_reason, _, _ = run_task.result()
    assert exit_reason == "interrupted"


# Per-probe lines reach a pipe promptly; a closed reader stops the run.


class RecordingStream(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1
        super().flush()


class BrokenPipeStream(io.StringIO):
    def write(self, _text: str) -> int:
        raise BrokenPipeError(errno.EPIPE, "Broken pipe")


class FailingStream(io.StringIO):
    def __init__(self, error: OSError) -> None:
        super().__init__()
        self.error = error

    def write(self, _text: str) -> int:
        raise self.error


async def test_sample_lines_are_flushed_once_per_event_loop_turn() -> None:
    stream = RecordingStream()
    printer = runner._SampleLinePrinter(stream, on_closed=lambda: None)
    target = TargetRun("192.0.2.1", resolved_address="192.0.2.1")

    for _ in range(3):
        printer(target, ok_sample())
    assert stream.flushes == 0
    await asyncio.sleep(0)

    assert stream.flushes == 1
    assert len(stream.getvalue().splitlines()) == 3


@pytest.mark.parametrize(
    "error",
    [
        BrokenPipeError(errno.EPIPE, "Broken pipe"),
        OSError(errno.EIO, "Input/output error"),
    ],
    ids=["closed-pipe", "hung-up-terminal"],
)
async def test_sample_printer_stops_the_run_once_the_reader_goes_away(
    capsys: pytest.CaptureFixture[str], error: OSError
) -> None:
    closed: list[bool] = []
    printer = runner._SampleLinePrinter(FailingStream(error), on_closed=lambda: closed.append(True))
    target = TargetRun("192.0.2.1", resolved_address="192.0.2.1")

    printer(target, ok_sample())
    printer(target, ok_sample())
    printer.flush()

    assert closed == [True]
    # Nobody is reading any more, so there is nothing to report.
    assert capsys.readouterr().err == ""


async def test_sample_printer_says_why_when_a_write_fails_for_another_reason(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A full disk is not a reader that went away: the run must not end
    # silently as if it had been interrupted.
    closed: list[bool] = []
    printer = runner._SampleLinePrinter(
        FailingStream(OSError(errno.ENOSPC, "No space left on device")),
        on_closed=lambda: closed.append(True),
    )
    target = TargetRun("192.0.2.1", resolved_address="192.0.2.1")

    printer(target, ok_sample())
    printer(target, ok_sample())

    assert closed == [True]
    assert capsys.readouterr().err == (
        "pinghue: warning: could not write probe lines, stopping the run: "
        "[Errno 28] No space left on device\n"
    )


async def test_run_writes_the_report_when_stdout_is_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # `pinghue --no-tui ... >&-` starts with sys.stdout set to None. The lines
    # have nowhere to go, but the run must still finish and write --output.
    report = tmp_path / "report.json"
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(runner, "probe_once", applying_probe)
    args = parse_args(
        ["--no-tui", "-n", "-c", "2", "-i", "0.1", "--output", str(report)]
        + ["192.0.2.1", "192.0.2.2"]
    )

    exit_code = await asyncio.wait_for(runner.run(args, mode=ProbeMode.ICMP), timeout=5.0)

    assert exit_code == 0
    document = json.loads(report.read_text())
    assert document["run"]["exit_reason"] == "completed"
    assert [target["stats"]["sent"] for target in document["targets"]] == [2, 2]


async def test_run_no_tui_keeps_lines_out_of_the_json_document_when_stderr_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With `--output -` the lines belong on stderr. A closed stderr must drop
    # them instead of mixing them into the JSON document on stdout.
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(runner, "probe_once", applying_probe)

    _, exit_reason, _, _ = await asyncio.wait_for(
        run_no_tui(no_tui_args(count=1, output="-"), ProbeMode.ICMP), timeout=5.0
    )

    assert exit_reason == "completed"
    assert stdout.getvalue() == ""


def test_flush_or_discard_output_drops_what_a_hung_up_terminal_cannot_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The TUI's terminal-restore output stays buffered after a hangup; the
    # flush at exit then failed and turned the exit status into 120.
    class HungUpStream(io.StringIO):
        def flush(self) -> None:
            raise OSError(errno.EIO, "Input/output error")

    discarded: list[object] = []
    monkeypatch.setattr(runner, "_discard_output", discarded.append)
    hung_up = HungUpStream()
    healthy = RecordingStream()

    runner.flush_or_discard_output(hung_up)
    runner.flush_or_discard_output(healthy)
    runner.flush_or_discard_output(None)

    assert discarded == [hung_up]
    assert healthy.flushes == 1


async def test_run_no_tui_stops_cleanly_when_stdout_is_a_broken_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = capture_stop_event(monkeypatch)
    monkeypatch.setattr(sys, "stdout", BrokenPipeStream())
    monkeypatch.setattr(runner, "probe_once", applying_probe)

    run_task = asyncio.create_task(run_no_tui(no_tui_args(), ProbeMode.ICMP))

    assert await settle(run_task, installed), "a closed reader did not stop the run"
    targets, exit_reason, _, _ = run_task.result()
    assert exit_reason == "interrupted"
    assert targets[0].stats.sent == 1
