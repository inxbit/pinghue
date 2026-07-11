import asyncio
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import pinghue.models as models
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
from pinghue.runner import probe_target_loop, resolve_runs, run_no_tui, stagger_delay


def no_tui_args(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "address_family": AddressFamily.AUTO.value,
        "concurrency": 1,
        "count": None,
        "duration": None,
        "fail_threshold": 3,
        "interval": 1.0,
        "jitter_threshold": 50.0,
        "numeric": False,
        "output": None,
        "port": None,
        "targets": ["1.1.1.1"],
        "timeout": 1.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_stagger_delay_spreads_hosts_across_interval() -> None:
    assert stagger_delay(index=0, count=4, interval=1.0) == 0.0
    assert stagger_delay(index=1, count=4, interval=1.0) == 0.25
    assert stagger_delay(index=3, count=4, interval=1.0) == 0.75
    assert stagger_delay(index=0, count=0, interval=1.0) == 0.0


async def test_probe_target_loop_runs_independent_probe_without_global_wait() -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    args = SimpleNamespace(interval=60.0)
    stop_event = asyncio.Event()
    immediate_event = asyncio.Event()
    calls = 0

    async def fake_probe_once() -> None:
        nonlocal calls
        calls += 1
        stop_event.set()

    await probe_target_loop(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
        stop_event=stop_event,
        immediate_event=immediate_event,
        initial_delay=0,
        probe_once_fn=fake_probe_once,
    )

    assert calls == 1


async def test_probe_target_loop_stops_after_count_limit() -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    args = SimpleNamespace(count=2, interval=0.01)
    stop_event = asyncio.Event()
    immediate_event = asyncio.Event()
    calls = 0

    async def fake_probe_once() -> None:
        nonlocal calls
        calls += 1

    await asyncio.wait_for(
        probe_target_loop(
            target,
            args=args,
            mode=ProbeMode.ICMP,
            semaphore=asyncio.Semaphore(1),
            stop_event=stop_event,
            immediate_event=immediate_event,
            initial_delay=0,
            probe_once_fn=fake_probe_once,
        ),
        timeout=0.1,
    )

    assert calls == 2


async def test_probe_target_loop_immediate_event_interrupts_and_consumes_initial_stagger() -> None:
    stop_event = asyncio.Event()
    immediate_event = asyncio.Event()
    first_probe = asyncio.Event()
    calls = 0

    async def fake_probe_once() -> None:
        nonlocal calls
        calls += 1
        first_probe.set()

    task = asyncio.create_task(
        probe_target_loop(
            TargetRun("1.1.1.1", resolved_address="1.1.1.1"),
            args=SimpleNamespace(count=None, interval=0.25),
            mode=ProbeMode.ICMP,
            semaphore=asyncio.Semaphore(1),
            stop_event=stop_event,
            immediate_event=immediate_event,
            initial_delay=1.0,
            probe_once_fn=fake_probe_once,
        )
    )

    try:
        await asyncio.sleep(0)
        immediate_event.set()
        await asyncio.wait_for(first_probe.wait(), timeout=0.15)
        await asyncio.sleep(0.05)
        assert calls == 1
    finally:
        stop_event.set()
        immediate_event.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_probe_target_loop_subtracts_probe_duration_from_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # L2: the per-target loop must compensate the inter-probe wait for the time
    # the probe itself took, rather than always waiting the full interval.
    recorded: list[tuple[float, float]] = []

    def spy_iteration_sleep(
        *, interval: float, iteration_elapsed: float, _duration_remaining: float | None = None
    ) -> float:
        recorded.append((interval, iteration_elapsed))
        return 0.0

    monkeypatch.setattr(runner, "_iteration_sleep", spy_iteration_sleep)

    args = SimpleNamespace(count=2, interval=5.0)
    stop_event = asyncio.Event()
    immediate_event = asyncio.Event()

    async def fake_probe_once() -> None:
        await asyncio.sleep(0.05)

    await asyncio.wait_for(
        probe_target_loop(
            TargetRun("1.1.1.1", resolved_address="1.1.1.1"),
            args=args,
            mode=ProbeMode.ICMP,
            semaphore=asyncio.Semaphore(1),
            stop_event=stop_event,
            immediate_event=immediate_event,
            initial_delay=0,
            probe_once_fn=fake_probe_once,
        ),
        timeout=1.0,
    )

    assert len(recorded) == 1
    interval, elapsed = recorded[0]
    assert interval == 5.0
    assert 0.03 <= elapsed < 1.0


async def test_run_no_tui_accounts_for_probe_duration_between_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    base = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
    times = iter(
        [
            base,
            base,
            base - timedelta(seconds=0.4),
            base + timedelta(seconds=1.0),
            base + timedelta(seconds=1.0),
        ]
    )
    monotonic_times = iter([10.0, 10.4, 11.0])
    sleeps: list[float] = []

    class FakeDateTime:
        @classmethod
        def now(cls, _tz: timezone) -> datetime:
            return next(times)

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **__: object) -> ProbeSample:
        return ProbeSample(
            timestamp=base,
            latency_ms=1.0,
            status=SampleStatus.OK,
        )

    async def fake_wait_for_stop_event(_: asyncio.Event, delay: float) -> bool:
        sleeps.append(delay)
        return False

    monkeypatch.setattr(runner, "datetime", FakeDateTime)
    monkeypatch.setattr(
        runner,
        "_monotonic_time",
        lambda: next(monotonic_times),
        raising=False,
    )
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)
    monkeypatch.setattr(runner, "wait_for_stop_event", fake_wait_for_stop_event)

    args = no_tui_args(count=2)

    await run_no_tui(args, ProbeMode.ICMP)

    assert sleeps == pytest.approx([0.6])


async def test_run_no_tui_yields_after_slow_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    base = datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc)
    times = iter(
        [
            base,
            base,
            base + timedelta(seconds=1.5),
            base + timedelta(seconds=1.6),
            base + timedelta(seconds=1.7),
        ]
    )
    monotonic_times = iter([10.0, 11.5, 11.6])
    sleeps: list[float] = []

    class FakeDateTime:
        @classmethod
        def now(cls, _tz: timezone) -> datetime:
            return next(times)

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **__: object) -> ProbeSample:
        return ProbeSample(
            timestamp=base,
            latency_ms=1.0,
            status=SampleStatus.OK,
        )

    async def fake_wait_for_stop_event(_: asyncio.Event, delay: float) -> bool:
        sleeps.append(delay)
        return False

    monkeypatch.setattr(runner, "datetime", FakeDateTime)
    monkeypatch.setattr(
        runner,
        "_monotonic_time",
        lambda: next(monotonic_times),
        raising=False,
    )
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)
    monkeypatch.setattr(runner, "wait_for_stop_event", fake_wait_for_stop_event)

    args = no_tui_args(count=2)

    await run_no_tui(args, ProbeMode.ICMP)

    assert sleeps == [0.1]


async def test_run_no_tui_returns_interrupted_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **__: object) -> ProbeSample:
        raise asyncio.CancelledError

    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    args = no_tui_args()

    targets, exit_reason, started_at, ended_at = await run_no_tui(args, ProbeMode.ICMP)

    assert targets == [target]
    assert exit_reason == "interrupted"
    assert ended_at >= started_at


async def test_run_no_tui_skips_probes_when_stop_is_already_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    probe_calls = 0

    def fake_install_stop_signal_handlers(stop_event: asyncio.Event) -> object:
        stop_event.set()
        return lambda: None

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **__: object) -> ProbeSample:
        nonlocal probe_calls
        probe_calls += 1
        return ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=1.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(
        runner, "install_stop_signal_handlers", fake_install_stop_signal_handlers
    )
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    args = no_tui_args(count=1, port=443)

    _, exit_reason, _, _ = await run_no_tui(args, ProbeMode.TCP)

    assert (probe_calls, exit_reason) == (0, "interrupted")


async def test_run_no_tui_reports_interrupted_when_count_final_probe_requests_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    installed_stop_event: asyncio.Event | None = None

    def fake_install_stop_signal_handlers(stop_event: asyncio.Event) -> object:
        nonlocal installed_stop_event
        installed_stop_event = stop_event
        return lambda: None

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **__: object) -> ProbeSample:
        assert installed_stop_event is not None
        installed_stop_event.set()
        return ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=1.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(
        runner, "install_stop_signal_handlers", fake_install_stop_signal_handlers
    )
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    args = no_tui_args(count=1, port=443)

    _, exit_reason, _, _ = await run_no_tui(args, ProbeMode.TCP)

    assert exit_reason == "interrupted"


async def test_run_no_tui_prints_probes_completed_before_interruption(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fast = TargetRun(
        target="fast.example",
        resolved_address="192.0.2.1",
        resolved_family=AddressFamily.IPV4,
    )
    blocked = TargetRun(
        target="blocked.example",
        resolved_address="192.0.2.2",
        resolved_family=AddressFamily.IPV4,
    )
    installed_stop_event: asyncio.Event | None = None
    blocked_probe_cancelled = asyncio.Event()

    def fake_install_stop_signal_handlers(stop_event: asyncio.Event) -> object:
        nonlocal installed_stop_event
        installed_stop_event = stop_event
        return lambda: None

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [fast, blocked]

    async def fake_probe_once(
        target: TargetRun, *_: object, **__: object
    ) -> ProbeSample:
        if target is blocked:
            try:
                await asyncio.Event().wait()
            finally:
                blocked_probe_cancelled.set()
            raise AssertionError("unreachable")

        sample = ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=1.0,
            status=SampleStatus.OK,
        )
        fast.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)
        assert installed_stop_event is not None
        installed_stop_event.set()
        return sample

    monkeypatch.setattr(
        runner, "install_stop_signal_handlers", fake_install_stop_signal_handlers
    )
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    targets, exit_reason, _, _ = await run_no_tui(
        no_tui_args(targets=[fast.target, blocked.target], port=443),
        ProbeMode.TCP,
    )

    assert exit_reason == "interrupted"
    assert [target.stats.sent for target in targets] == [1, 0]
    assert blocked_probe_cancelled.is_set()
    output = capsys.readouterr().out
    assert "fast.example ok latency=1.00ms" in output
    assert "blocked.example" not in output


async def test_run_no_tui_cancels_an_active_probe_batch_when_stop_is_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun("1.1.1.1", resolved_address="1.1.1.1")
    installed_stop_event: asyncio.Event | None = None
    probe_started = asyncio.Event()
    probe_cancelled = asyncio.Event()

    def fake_install_stop_signal_handlers(stop_event: asyncio.Event) -> object:
        nonlocal installed_stop_event
        installed_stop_event = stop_event
        return lambda: None

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def blocked_probe(*_: object, **__: object) -> ProbeSample:
        probe_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            probe_cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(
        runner, "install_stop_signal_handlers", fake_install_stop_signal_handlers
    )
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", blocked_probe)
    args = no_tui_args(port=443, timeout=30.0)

    run_task = asyncio.create_task(run_no_tui(args, ProbeMode.TCP))
    await asyncio.wait_for(probe_started.wait(), timeout=0.2)
    assert installed_stop_event is not None
    installed_stop_event.set()

    done, _ = await asyncio.wait({run_task}, timeout=0.2)
    if run_task not in done:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    assert run_task in done
    _, exit_reason, _, _ = run_task.result()
    assert exit_reason == "interrupted"
    assert probe_cancelled.is_set()


async def test_run_no_tui_duration_cancels_an_active_probe_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun("1.1.1.1", resolved_address="1.1.1.1")
    probe_started = asyncio.Event()
    probe_cancelled = asyncio.Event()

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def blocked_probe(*_: object, **__: object) -> ProbeSample:
        probe_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            probe_cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", blocked_probe)
    args = no_tui_args(duration=0.01, port=443, timeout=30.0)

    run_task = asyncio.create_task(run_no_tui(args, ProbeMode.TCP))
    await asyncio.wait_for(probe_started.wait(), timeout=0.2)
    done, _ = await asyncio.wait({run_task}, timeout=0.2)
    if run_task not in done:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    assert run_task in done
    _, exit_reason, _, _ = run_task.result()
    assert exit_reason == "deadline"
    assert probe_cancelled.is_set()


async def test_run_no_tui_duration_cancels_active_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution_started = asyncio.Event()
    resolution_cancelled = asyncio.Event()

    async def blocked_resolve_runs(_: object) -> list[TargetRun]:
        resolution_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            resolution_cancelled.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(runner, "resolve_runs", blocked_resolve_runs)
    args = no_tui_args(
        duration=0.01,
        port=443,
        targets=["example.com"],
        timeout=30.0,
    )

    run_task = asyncio.create_task(run_no_tui(args, ProbeMode.TCP))
    await asyncio.wait_for(resolution_started.wait(), timeout=0.2)
    done, _ = await asyncio.wait({run_task}, timeout=0.2)
    if run_task not in done:
        run_task.cancel()
        await asyncio.gather(run_task, return_exceptions=True)

    assert run_task in done
    targets, exit_reason, _, _ = run_task.result()
    assert [target.target for target in targets] == ["example.com"]
    assert [target.status for target in targets] == [TargetStatus.RESOLVING]
    assert runner.exit_code_for_targets(
        targets,
        fail_on_any_down=True,
        fail_on_all_down=False,
    ) == runner.EXIT_TARGETS_DOWN
    assert exit_reason == "deadline"
    assert resolution_cancelled.is_set()


async def test_run_no_tui_duration_includes_synchronous_startup_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_pending_runs = runner._pending_target_runs
    resolution_calls = 0

    def slow_pending_runs(targets: list[str]) -> list[TargetRun]:
        time.sleep(0.02)
        return original_pending_runs(targets)

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        nonlocal resolution_calls
        resolution_calls += 1
        return [TargetRun("1.1.1.1", resolved_address="1.1.1.1")]

    monkeypatch.setattr(runner, "_pending_target_runs", slow_pending_runs)
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)

    _, exit_reason, _, _ = await run_no_tui(
        no_tui_args(duration=0.001, count=1),
        ProbeMode.ICMP,
    )

    assert exit_reason == "deadline"
    assert resolution_calls == 0


async def test_probe_batch_rejects_work_when_deadline_is_already_expired() -> None:
    called = False

    async def immediate_probe() -> None:
        nonlocal called
        called = True

    samples, stop_reason = await runner._run_probe_batch(
        [immediate_probe()],
        asyncio.Event(),
        deadline_at=asyncio.get_running_loop().time() - 1.0,
    )

    assert samples == []
    assert stop_reason == "deadline"
    assert called is False


async def test_resolution_rejects_work_and_preserves_targets_after_expired_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolution_calls = 0

    async def immediate_resolution(_: object) -> list[TargetRun]:
        nonlocal resolution_calls
        resolution_calls += 1
        return []

    monkeypatch.setattr(runner, "resolve_runs", immediate_resolution)
    args = SimpleNamespace(targets=["a.example", "b.example"])

    targets, stop_reason = await runner._resolve_runs_until_shutdown(
        args,
        asyncio.Event(),
        deadline_at=asyncio.get_running_loop().time() - 1.0,
    )

    assert [target.target for target in targets] == ["a.example", "b.example"]
    assert [target.status for target in targets] == [
        TargetStatus.RESOLVING,
        TargetStatus.RESOLVING,
    ]
    assert stop_reason == "deadline"
    assert resolution_calls == 0


async def test_run_no_tui_uses_daemon_icmp_bridge_without_dedicated_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    seen_executors: list[object] = []

    def no_dedicated_executor(*_: object, **__: object) -> object:
        pytest.fail("ICMP probes must use the daemon-thread bridge")

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **kwargs: object) -> ProbeSample:
        seen_executors.append(kwargs["executor"])
        return ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=1.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "ThreadPoolExecutor", no_dedicated_executor, raising=False)
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    args = no_tui_args(concurrency=5, count=1)

    await run_no_tui(args, ProbeMode.ICMP)

    assert seen_executors == [None]


async def test_resolve_runs_resolves_targets_concurrently_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    max_active = 0

    async def fake_resolve_target(
        target: str,
        family: AddressFamily,
        *,
        numeric: bool,
    ) -> ResolvedTarget:
        nonlocal active, max_active
        assert numeric is False
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return ResolvedTarget(
            target=target,
            address=f"192.0.2.{len(target)}",
            family=family,
            addresses=(f"192.0.2.{len(target)}",),
        )

    monkeypatch.setattr(runner, "resolve_target", fake_resolve_target)
    args = SimpleNamespace(
        address_family=AddressFamily.IPV4.value,
        numeric=False,
        targets=["aa", "bbbb", "c"],
    )

    runs = await resolve_runs(args)

    assert max_active > 1
    assert [run.target for run in runs] == ["aa", "bbbb", "c"]
    assert [run.resolved_address for run in runs] == ["192.0.2.2", "192.0.2.4", "192.0.2.1"]


async def test_resolve_runs_caps_aggregate_retained_samples_across_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_count = 200
    total_budget = 100_000
    effective_window = min(
        models.MAX_TARGET_SAMPLES,
        max(1, total_budget // target_count),
    )

    async def fake_resolve_run_target(target: str, _args: object) -> TargetRun:
        return TargetRun(target, resolved_address=target)

    monkeypatch.setattr(runner, "resolve_run_target", fake_resolve_run_target)
    runs = await resolve_runs(
        SimpleNamespace(targets=[f"192.0.2.{index}" for index in range(target_count)])
    )

    assert {run.samples.maxlen for run in runs} == {effective_window}

    retained_sample = ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=1.0,
        status=SampleStatus.OK,
    )
    for run in runs:
        for _ in range(effective_window + 1):
            run.samples.append(retained_sample)

    assert sum(len(run.samples) for run in runs) == total_budget
    assert total_budget == models.MAX_TOTAL_RETAINED_SAMPLES


async def test_probe_once_fails_over_to_next_resolved_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    target = TargetRun(
        target="service.example",
        resolved_address="192.0.2.1",
        resolved_family=AddressFamily.IPV4,
        resolved_addresses=("192.0.2.1", "192.0.2.2"),
    )

    async def fake_tcp_probe(address: str, port: int, *, timeout_s: float) -> ProbeSample:
        assert port == 443
        assert timeout_s == 1.0
        calls.append(address)
        if address == "192.0.2.1":
            return ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
                latency_ms=None,
                status=SampleStatus.UNREACHABLE,
                error="unreachable",
            )
        return ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 12, tzinfo=timezone.utc),
            latency_ms=3.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "tcp_probe", fake_tcp_probe)
    args = SimpleNamespace(
        address_family=AddressFamily.AUTO.value,
        fail_threshold=1,
        jitter_threshold=50.0,
        port=443,
        timeout=1.0,
    )

    sample = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.TCP,
        semaphore=asyncio.Semaphore(1),
    )

    assert calls == ["192.0.2.1", "192.0.2.2"]
    assert sample is not None
    assert sample.status == SampleStatus.OK
    assert target.resolved_address == "192.0.2.2"
    assert target.status.name == "HEALTHY"


async def test_probe_once_retains_primary_failure_when_all_addresses_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    target = TargetRun(
        target="service.example",
        resolved_address="192.0.2.1",
        resolved_family=AddressFamily.IPV4,
        resolved_addresses=("192.0.2.1", "2001:db8::1"),
    )
    primary_failure = ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
        latency_ms=None,
        status=SampleStatus.UNREACHABLE,
        error="primary unreachable",
    )
    alternate_failure = ProbeSample(
        timestamp=datetime(2026, 5, 14, 18, 32, 12, tzinfo=timezone.utc),
        latency_ms=None,
        status=SampleStatus.TIMEOUT,
        error="alternate timed out",
    )

    async def fake_tcp_probe(address: str, port: int, *, timeout_s: float) -> ProbeSample:
        assert port == 443
        assert timeout_s == 1.0
        calls.append(address)
        return primary_failure if address == "192.0.2.1" else alternate_failure

    monkeypatch.setattr(runner, "tcp_probe", fake_tcp_probe)
    args = SimpleNamespace(
        address_family=AddressFamily.AUTO.value,
        fail_threshold=1,
        jitter_threshold=50.0,
        port=443,
        timeout=1.0,
    )

    sample = await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.TCP,
        semaphore=asyncio.Semaphore(1),
    )

    assert calls == ["192.0.2.1", "2001:db8::1"]
    assert sample == primary_failure
    assert target.resolved_address == "192.0.2.1"
    assert target.resolved_family == AddressFamily.IPV4
    assert target.samples[-1] == primary_failure
    assert target.error == "primary unreachable"


async def test_probe_once_passes_executor_to_icmp_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object()
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    seen_executors: list[object] = []

    async def fake_icmp_probe(*_: object, **kwargs: object) -> ProbeSample:
        seen_executors.append(kwargs["executor"])
        return ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=2.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "icmp_probe", fake_icmp_probe)
    args = SimpleNamespace(
        address_family=AddressFamily.IPV4.value,
        fail_threshold=1,
        jitter_threshold=50.0,
        port=None,
        timeout=1.0,
    )

    await runner.probe_once(
        target,
        args=args,
        mode=ProbeMode.ICMP,
        semaphore=asyncio.Semaphore(1),
        executor=executor,
    )

    assert seen_executors == [executor]
