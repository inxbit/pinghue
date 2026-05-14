"""Runtime orchestration for TUI and no-TUI modes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from pinghue.display import sanitize_display
from pinghue.export import write_output_json
from pinghue.models import (
    AddressFamily,
    ProbeConfig,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    TargetRun,
    TargetStatus,
    classify_samples,
)
from pinghue.probes import icmp_probe, resolve_target, tcp_probe

ProbeOnce = Callable[[], Awaitable[None]]


def _probe_config(args: Any, mode: ProbeMode) -> ProbeConfig:
    return ProbeConfig(
        mode=mode,
        port=args.port,
        interval_s=args.interval,
        timeout_s=args.timeout,
        address_family=AddressFamily(args.address_family),
    )


async def _resolve_runs(args: Any) -> list[TargetRun]:
    family = AddressFamily(args.address_family)
    runs: list[TargetRun] = []

    for target in args.targets:
        resolved = await resolve_target(target, family, numeric=args.numeric)
        if resolved.error:
            runs.append(
                TargetRun(
                    target=target,
                    resolved_address=None,
                    resolved_family=None,
                    status=TargetStatus.DNS_FAILURE,
                    error=resolved.error,
                )
            )
        else:
            runs.append(
                TargetRun(
                    target=target,
                    resolved_address=resolved.address,
                    resolved_family=resolved.family,
                    status=TargetStatus.DOWN,
                    error=None,
                )
            )

    return runs


async def _probe_once(
    target: TargetRun,
    *,
    args: Any,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
) -> ProbeSample | None:
    if not target.resolved_address:
        return None

    async with semaphore:
        if mode == ProbeMode.TCP:
            sample = await tcp_probe(target.resolved_address, args.port, timeout_s=args.timeout)
        else:
            sample = await icmp_probe(
                target.resolved_address,
                timeout_s=args.timeout,
                address_family=AddressFamily(args.address_family),
            )

    target.samples.append(sample)
    if (
        sample.status == SampleStatus.ERROR
        and sample.error
        and "permission" in sample.error.lower()
    ):
        target.status = TargetStatus.PERMISSION_DENIED
        target.error = sample.error
    else:
        target.status = classify_samples(
            target.samples,
            fail_threshold=args.fail_threshold,
            jitter_threshold_ms=args.jitter_threshold,
        )
        target.error = (
            sample.error if target.status in {TargetStatus.DOWN, TargetStatus.ERROR} else None
        )

    return sample


def stagger_delay(*, index: int, count: int, interval: float) -> float:
    """Return a start delay that spreads hosts evenly across one interval."""
    if count <= 0:
        return 0.0

    return round((interval / count) * index, 6)


async def probe_target_loop(
    target: TargetRun,
    *,
    args: Any,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
    stop_event: asyncio.Event,
    immediate_event: asyncio.Event,
    initial_delay: float,
    probe_once: ProbeOnce | None = None,
) -> None:
    """Run one target's probe loop without blocking other targets or UI refresh."""
    if initial_delay > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
            return
        except asyncio.TimeoutError:
            pass

    while not stop_event.is_set():
        if probe_once is None:
            await _probe_once(target, args=args, mode=mode, semaphore=semaphore)
        else:
            await probe_once()

        if stop_event.is_set():
            return

        try:
            await asyncio.wait_for(immediate_event.wait(), timeout=args.interval)
        except asyncio.TimeoutError:
            continue
        finally:
            immediate_event.clear()


def _print_sample(target: TargetRun, sample: ProbeSample | None) -> None:
    target_text = sanitize_display(target.target)
    if sample is None:
        print(
            f"{datetime.now(timezone.utc).isoformat()} "
            f"{target_text} dns_failure error={sanitize_display(target.error or '')}"
        )
        return

    latency = "-" if sample.latency_ms is None else f"{sample.latency_ms:.2f}ms"
    error = "" if sample.error is None else f" error={sanitize_display(sample.error)}"
    print(
        f"{sample.timestamp.isoformat()} {target_text} "
        f"{sample.status.value} latency={latency}{error}"
    )


async def run_no_tui(
    args: Any,
    mode: ProbeMode,
) -> tuple[list[TargetRun], str, datetime, datetime]:
    started_at = datetime.now(timezone.utc)
    targets = await _resolve_runs(args)
    semaphore = asyncio.Semaphore(args.concurrency)
    exit_reason = "completed"
    iteration = 0

    while True:
        iteration += 1
        probes = [
            _probe_once(target, args=args, mode=mode, semaphore=semaphore)
            for target in targets
            if target.resolved_address
        ]
        samples = await asyncio.gather(*probes) if probes else []
        sample_by_target = iter(samples)

        for target in targets:
            sample = next(sample_by_target) if target.resolved_address else None
            _print_sample(target, sample)

        if args.count is not None and iteration >= args.count:
            break

        duration_elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
        if args.duration is not None and duration_elapsed >= args.duration:
            exit_reason = "deadline"
            break

        await asyncio.sleep(args.interval)

    ended_at = datetime.now(timezone.utc)
    return targets, exit_reason, started_at, ended_at


async def run(args: Any, *, mode: ProbeMode) -> int:
    probe = _probe_config(args, mode)

    if args.no_tui:
        targets, exit_reason, started_at, ended_at = await run_no_tui(args, mode)
    else:
        from pinghue.app import PinghueApp

        app = PinghueApp(args=args, mode=mode)
        targets, exit_reason, started_at, ended_at = await app.run_async()

    if args.output:
        write_output_json(
            args.output,
            started_at=started_at,
            ended_at=ended_at,
            host=os.uname().nodename,
            exit_reason=exit_reason,
            probe=probe,
            targets=targets,
            include_samples=not args.no_samples,
        )

    return 0
