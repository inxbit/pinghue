import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import pinghue.runner as runner
from pinghue.models import AddressFamily, ProbeMode, ProbeSample, SampleStatus, TargetRun
from pinghue.probes import ResolvedTarget
from pinghue.runner import probe_target_loop, resolve_runs, run_no_tui, stagger_delay


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
            base + timedelta(seconds=0.4),
            base + timedelta(seconds=1.0),
            base + timedelta(seconds=1.0),
        ]
    )
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
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)
    monkeypatch.setattr(runner, "wait_for_stop_event", fake_wait_for_stop_event)

    args = SimpleNamespace(
        address_family=AddressFamily.AUTO.value,
        concurrency=1,
        count=2,
        duration=None,
        fail_threshold=3,
        interval=1.0,
        jitter_threshold=50.0,
        port=None,
        targets=["1.1.1.1"],
        timeout=1.0,
    )

    await run_no_tui(args, ProbeMode.ICMP)

    assert sleeps == [0.6]


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
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)
    monkeypatch.setattr(runner, "wait_for_stop_event", fake_wait_for_stop_event)

    args = SimpleNamespace(
        address_family=AddressFamily.AUTO.value,
        concurrency=1,
        count=2,
        duration=None,
        fail_threshold=3,
        interval=1.0,
        jitter_threshold=50.0,
        port=None,
        targets=["1.1.1.1"],
        timeout=1.0,
    )

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

    args = SimpleNamespace(
        address_family=AddressFamily.AUTO.value,
        concurrency=1,
        count=None,
        duration=None,
        fail_threshold=3,
        interval=1.0,
        jitter_threshold=50.0,
        port=None,
        targets=["1.1.1.1"],
        timeout=1.0,
    )

    targets, exit_reason, started_at, ended_at = await run_no_tui(args, ProbeMode.ICMP)

    assert targets == [target]
    assert exit_reason == "interrupted"
    assert ended_at >= started_at


async def test_run_no_tui_uses_dedicated_icmp_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
    )
    executors: list[object] = []
    seen_executors: list[object] = []

    class FakeExecutor:
        def __init__(self, *, max_workers: int) -> None:
            self.max_workers = max_workers
            self.shutdown_called = False
            executors.append(self)

        def shutdown(self, *, wait: bool = True, cancel_futures: bool = False) -> None:
            self.shutdown_called = True
            self.wait = wait
            self.cancel_futures = cancel_futures

    async def fake_resolve_runs(_: object) -> list[TargetRun]:
        return [target]

    async def fake_probe_once(*_: object, **kwargs: object) -> ProbeSample:
        seen_executors.append(kwargs["executor"])
        return ProbeSample(
            timestamp=datetime(2026, 5, 14, 18, 32, 11, tzinfo=timezone.utc),
            latency_ms=1.0,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "ThreadPoolExecutor", FakeExecutor, raising=False)
    monkeypatch.setattr(runner, "resolve_runs", fake_resolve_runs)
    monkeypatch.setattr(runner, "probe_once", fake_probe_once)

    args = SimpleNamespace(
        address_family=AddressFamily.AUTO.value,
        concurrency=5,
        count=1,
        duration=None,
        fail_threshold=3,
        interval=1.0,
        jitter_threshold=50.0,
        port=None,
        targets=["1.1.1.1"],
        timeout=1.0,
    )

    await run_no_tui(args, ProbeMode.ICMP)

    assert len(executors) == 1
    assert executors[0].max_workers == 5
    assert seen_executors == [executors[0]]
    assert executors[0].shutdown_called is True


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
