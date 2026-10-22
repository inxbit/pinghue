import asyncio
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

import pytest

import pinghue.runner as runner
from pinghue.cli import (
    CONCURRENCY_DEFAULT_MINIMUM,
    CONCURRENCY_MAXIMUM,
    default_concurrency,
    parse_args,
)
from pinghue.models import ProbeMode, ProbeSample, SampleStatus


@pytest.mark.parametrize(
    ("target_count", "interval", "timeout", "expected"),
    [
        (0, 1.0, 1.0, CONCURRENCY_DEFAULT_MINIMUM),
        (1, 1.0, 1.0, CONCURRENCY_DEFAULT_MINIMUM),
        (50, 1.0, 1.0, CONCURRENCY_DEFAULT_MINIMUM),
        # 500 silent targets each hold a slot for 1 s of every 1.1 s cycle.
        (500, 1.0, 1.0, 569),
        (1000, 1.0, 1.0, CONCURRENCY_MAXIMUM),
        (5000, 1.0, 1.0, CONCURRENCY_MAXIMUM),
        # A short timeout holds each slot for a fraction of the interval.
        (500, 1.0, 0.2, 125),
        (500, 5.0, 1.0, 125),
        # A timeout longer than the interval stretches the cycle to timeout + overrun.
        (200, 0.5, 2.0, 239),
    ],
)
def test_default_concurrency_sizes_slots_for_every_target_timing_out(
    target_count: int,
    interval: float,
    timeout: float,
    expected: int,
) -> None:
    assert default_concurrency(target_count, interval=interval, timeout=timeout) == expected


def test_default_concurrency_stays_finite_for_huge_timeouts() -> None:
    assert default_concurrency(5000, interval=1.0, timeout=1e308) == CONCURRENCY_MAXIMUM


def test_parse_args_sizes_default_concurrency_from_the_workload() -> None:
    targets = [f"127.0.{i // 250}.{i % 250 + 1}" for i in range(500)]

    assert parse_args(["127.0.0.1"]).concurrency == CONCURRENCY_DEFAULT_MINIMUM
    assert parse_args(targets).concurrency == 569
    assert parse_args([*targets, "--timeout", "0.2"]).concurrency == 125
    assert parse_args(["--check"]).concurrency == CONCURRENCY_DEFAULT_MINIMUM


def test_parse_args_keeps_explicit_concurrency_for_large_runs() -> None:
    targets = [f"127.0.{i // 250}.{i % 250 + 1}" for i in range(1000)]

    assert parse_args([*targets, "--concurrency", "8"]).concurrency == 8
    assert parse_args(["127.0.0.1", "--concurrency", "1024"]).concurrency == 1024


async def test_default_concurrency_keeps_healthy_cadence_with_many_silent_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 250 of 300 targets never answer and hold a probe slot for the whole timeout.
    # The old fixed default of 64 slots stretched every target's cycle, healthy
    # ones included, to about 250 x 0.2 s / 64 = 0.78 s instead of the 0.2 s interval.
    interval = 0.2
    silent = [f"127.0.2.{i}" for i in range(1, 251)]
    healthy = [f"127.0.1.{i}" for i in range(1, 51)]
    args = parse_args(
        [*silent, *healthy, "-n", "--no-tui", "-i", str(interval), "--duration", "2.0"]
    )
    assert args.concurrency > CONCURRENCY_DEFAULT_MINIMUM
    silent_addresses = set(silent)
    probe_starts: dict[str, list[float]] = defaultdict(list)

    async def fake_probe_address(address: str, **_: object) -> ProbeSample:
        probe_starts[address].append(time.monotonic())
        if address in silent_addresses:
            await asyncio.sleep(args.timeout)
            return ProbeSample(
                timestamp=datetime.now(timezone.utc),
                latency_ms=None,
                status=SampleStatus.TIMEOUT,
                error="Request timed out",
            )
        return ProbeSample(
            timestamp=datetime.now(timezone.utc),
            latency_ms=0.1,
            status=SampleStatus.OK,
        )

    monkeypatch.setattr(runner, "_probe_address", fake_probe_address)

    await asyncio.wait_for(runner.run_no_tui(args, ProbeMode.ICMP), timeout=10.0)

    healthy_gaps = [
        later - earlier
        for address in healthy
        for earlier, later in zip(probe_starts[address], probe_starts[address][1:], strict=False)
    ]
    assert min(len(probe_starts[address]) for address in healthy) >= 6
    assert statistics.median(healthy_gaps) <= 1.5 * interval
