import asyncio
from types import SimpleNamespace

from pinghue.models import AddressFamily, ProbeMode, TargetRun
from pinghue.runner import probe_target_loop, stagger_delay


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
        probe_once=fake_probe_once,
    )

    assert calls == 1
