"""Runtime orchestration for TUI and no-TUI modes."""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from pinghue.config import RunConfig
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
)
from pinghue.probes import family_from_ip, icmp_probe, resolve_target, tcp_probe

ProbeOnce = Callable[[], Awaitable[None]]
StopSignalCleanup = Callable[[], None]


def probe_config(args: RunConfig, mode: ProbeMode) -> ProbeConfig:
    return ProbeConfig(
        mode=mode,
        port=args.port,
        interval_s=args.interval,
        timeout_s=args.timeout,
        address_family=AddressFamily(args.address_family),
    )


async def resolve_runs(args: RunConfig) -> list[TargetRun]:
    family = AddressFamily(args.address_family)
    resolved_targets = await asyncio.gather(
        *(resolve_target(target, family, numeric=args.numeric) for target in args.targets)
    )
    runs: list[TargetRun] = []

    for resolved in resolved_targets:
        if resolved.error:
            runs.append(
                TargetRun(
                    target=resolved.target,
                    resolved_address=None,
                    resolved_family=None,
                    resolved_addresses=(),
                    status=TargetStatus.DNS_FAILURE,
                    error=resolved.error,
                )
            )
        else:
            runs.append(
                TargetRun(
                    target=resolved.target,
                    resolved_address=resolved.address,
                    resolved_family=resolved.family,
                    resolved_addresses=resolved.addresses,
                    status=TargetStatus.DOWN,
                    error=None,
                )
            )

    return runs


async def _probe_address(
    address: str,
    *,
    args: RunConfig,
    mode: ProbeMode,
) -> ProbeSample:
    if mode == ProbeMode.TCP:
        return await tcp_probe(address, args.port or 0, timeout_s=args.timeout)

    return await icmp_probe(
        address,
        timeout_s=args.timeout,
        address_family=AddressFamily(args.address_family),
    )


async def probe_once(
    target: TargetRun,
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
) -> ProbeSample | None:
    if not target.resolved_address:
        return None

    addresses = target.resolved_addresses or (target.resolved_address,)
    sample: ProbeSample | None = None

    async with semaphore:
        for address in addresses:
            sample = await _probe_address(address, args=args, mode=mode)
            if sample.status == SampleStatus.OK:
                target.resolved_address = address
                target.resolved_family = family_from_ip(address)
                break

    if sample is None:
        return None

    target.apply_sample(
        sample,
        fail_threshold=args.fail_threshold,
        jitter_threshold_ms=args.jitter_threshold,
    )

    return sample


def install_stop_signal_handlers(stop_event: asyncio.Event) -> StopSignalCleanup:
    """Install SIGINT/SIGTERM handlers that let no-TUI mode write final output."""
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_number, stop_event.set)
        except (NotImplementedError, RuntimeError, ValueError):
            continue
        installed.append(signal_number)

    def cleanup() -> None:
        for signal_number in installed:
            loop.remove_signal_handler(signal_number)

    return cleanup


def stagger_delay(*, index: int, count: int, interval: float) -> float:
    """Return a start delay that spreads hosts evenly across one interval."""
    if count <= 0:
        return 0.0

    return round((interval / count) * index, 6)


async def probe_target_loop(
    target: TargetRun,
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
    stop_event: asyncio.Event,
    immediate_event: asyncio.Event,
    initial_delay: float,
    probe_once_fn: ProbeOnce | None = None,
) -> None:
    """Run one target's probe loop without blocking other targets or UI refresh."""
    if initial_delay > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
            return
        except asyncio.TimeoutError:
            pass

    while not stop_event.is_set():
        if probe_once_fn is None:
            await probe_once(target, args=args, mode=mode, semaphore=semaphore)
        else:
            await probe_once_fn()

        if stop_event.is_set():
            return

        try:
            await asyncio.wait_for(immediate_event.wait(), timeout=args.interval)
        except asyncio.TimeoutError:
            continue
        finally:
            immediate_event.clear()


def print_sample(target: TargetRun, sample: ProbeSample | None) -> None:
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


async def wait_for_stop_event(stop_event: asyncio.Event, timeout: float) -> bool:
    """Return whether the stop event fired before the timeout elapsed."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    return True


async def run_no_tui(
    args: RunConfig,
    mode: ProbeMode,
) -> tuple[list[TargetRun], str, datetime, datetime]:
    started_at = datetime.now(timezone.utc)
    targets: list[TargetRun] = []
    semaphore = asyncio.Semaphore(args.concurrency)
    stop_event = asyncio.Event()
    cleanup_signal_handlers = install_stop_signal_handlers(stop_event)
    exit_reason = "completed"
    iteration = 0

    try:
        targets = await resolve_runs(args)

        while True:
            iteration += 1
            iteration_started_at = datetime.now(timezone.utc)
            probes = [
                probe_once(target, args=args, mode=mode, semaphore=semaphore)
                for target in targets
                if target.resolved_address
            ]
            samples = await asyncio.gather(*probes) if probes else []
            sample_by_target = iter(samples)

            for target in targets:
                sample = next(sample_by_target) if target.resolved_address else None
                print_sample(target, sample)

            if args.count is not None and iteration >= args.count:
                break

            if stop_event.is_set():
                exit_reason = "interrupted"
                break

            now = datetime.now(timezone.utc)
            duration_elapsed = (now - started_at).total_seconds()
            if args.duration is not None and duration_elapsed >= args.duration:
                exit_reason = "deadline"
                break

            iteration_elapsed = (now - iteration_started_at).total_seconds()
            sleep_for = max(0.0, args.interval - iteration_elapsed)
            if args.duration is not None:
                sleep_for = min(sleep_for, max(0.0, args.duration - duration_elapsed))

            if sleep_for > 0 and await wait_for_stop_event(stop_event, sleep_for):
                exit_reason = "interrupted"
                break
    except (asyncio.CancelledError, KeyboardInterrupt):
        exit_reason = "interrupted"
    finally:
        cleanup_signal_handlers()

    ended_at = datetime.now(timezone.utc)
    return targets, exit_reason, started_at, ended_at


def exit_code_for_targets(targets: list[TargetRun], *, fail_on_down: bool) -> int:
    """Return the process exit code for completed target statuses."""
    if not fail_on_down or not targets:
        return 0

    usable_statuses = {TargetStatus.HEALTHY, TargetStatus.INTERMITTENT}
    return 0 if any(target.status in usable_statuses for target in targets) else 2


async def run(args: RunConfig, *, mode: ProbeMode) -> int:
    probe = probe_config(args, mode)

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
            host=sanitize_display(args.host_label),
            exit_reason=exit_reason,
            probe=probe,
            targets=targets,
            include_samples=not args.no_samples,
        )

    return exit_code_for_targets(targets, fail_on_down=args.fail_on_down)
