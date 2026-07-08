"""Runtime orchestration for TUI and no-TUI modes."""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TextIO

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
from pinghue.probes import ResolvedTarget, family_from_ip, icmp_probe, resolve_target, tcp_probe

ProbeOnce = Callable[[], Awaitable[None]]
StopSignalCleanup = Callable[[], None]
MIN_OVERRUN_SLEEP = 0.1
DNS_RESOLVE_CONCURRENCY = 16
DNS_RETRY_INTERVAL_SECONDS = 10.0
DNS_LOOKUP_TIMEOUT_SECONDS = 5.0

_dns_semaphore: asyncio.Semaphore | None = None
_dns_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_dns_semaphore() -> asyncio.Semaphore:
    global _dns_semaphore, _dns_semaphore_loop
    loop = asyncio.get_running_loop()
    semaphore = _dns_semaphore
    if semaphore is None or _dns_semaphore_loop is not loop:
        semaphore = asyncio.Semaphore(DNS_RESOLVE_CONCURRENCY)
        _dns_semaphore = semaphore
        _dns_semaphore_loop = loop
    return semaphore


async def _resolve_target_bounded(
    target: str,
    family: AddressFamily,
    *,
    numeric: bool,
) -> ResolvedTarget:
    async with _get_dns_semaphore():
        try:
            return await asyncio.wait_for(
                resolve_target(target, family, numeric=numeric),
                timeout=DNS_LOOKUP_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return ResolvedTarget(
                target,
                None,
                None,
                f"getaddrinfo timed out after {DNS_LOOKUP_TIMEOUT_SECONDS:g}s",
            )


def probe_config(args: RunConfig, mode: ProbeMode) -> ProbeConfig:
    return ProbeConfig(
        mode=mode,
        port=args.port,
        interval_s=args.interval,
        timeout_s=args.timeout,
        address_family=AddressFamily(args.address_family),
    )


def target_run_from_resolution(resolved: ResolvedTarget) -> TargetRun:
    if resolved.error:
        return TargetRun(
            target=resolved.target,
            resolved_address=None,
            resolved_family=None,
            resolved_addresses=(),
            status=TargetStatus.DNS_FAILURE,
            error=resolved.error,
        )

    return TargetRun(
        target=resolved.target,
        resolved_address=resolved.address,
        resolved_family=resolved.family,
        resolved_addresses=resolved.addresses,
        status=TargetStatus.DOWN,
        error=None,
    )


async def resolve_run_target(target: str, args: RunConfig) -> TargetRun:
    family = AddressFamily(args.address_family)
    try:
        resolved = await _resolve_target_bounded(target, family, numeric=args.numeric)
    except Exception as exc:
        return TargetRun(
            target=target,
            resolved_address=None,
            resolved_family=None,
            resolved_addresses=(),
            status=TargetStatus.DNS_FAILURE,
            error=str(exc),
        )
    return target_run_from_resolution(resolved)


async def resolve_runs(args: RunConfig) -> list[TargetRun]:
    return await asyncio.gather(
        *(resolve_run_target(target, args) for target in args.targets)
    )


async def _probe_address(
    address: str,
    *,
    args: RunConfig,
    mode: ProbeMode,
    executor: Executor | None = None,
) -> ProbeSample:
    if mode == ProbeMode.TCP:
        return await tcp_probe(address, args.port or 0, timeout_s=args.timeout)

    return await icmp_probe(
        address,
        timeout_s=args.timeout,
        address_family=AddressFamily(args.address_family),
        executor=executor,
    )


async def probe_once(
    target: TargetRun,
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
    executor: Executor | None = None,
) -> ProbeSample | None:
    if not target.resolved_address:
        now = time.monotonic()
        if now - target._last_resolve_time < DNS_RETRY_INTERVAL_SECONDS:
            return None
        target._last_resolve_time = now
        family = AddressFamily(args.address_family)
        try:
            resolved = await _resolve_target_bounded(
                target.target,
                family,
                numeric=args.numeric,
            )
            if resolved.error:
                target.status = TargetStatus.DNS_FAILURE
                target.error = resolved.error
                return None
            target.resolved_address = resolved.address
            target.resolved_family = resolved.family
            target.resolved_addresses = resolved.addresses
            target.status = TargetStatus.DOWN
            target.error = None
        except Exception as exc:
            target.status = TargetStatus.DNS_FAILURE
            target.error = str(exc)
            return None

    primary = target.resolved_address
    if not primary:
        return None
    addresses = list(target.resolved_addresses) if target.resolved_addresses else [primary]
    if primary in addresses:
        addresses.remove(primary)
        addresses.insert(0, primary)

    sample: ProbeSample | None = None

    async with semaphore:
        for address in addresses:
            try:
                sample = await _probe_address(
                    address,
                    args=args,
                    mode=mode,
                    executor=executor,
                )
            except Exception as exc:
                sample = ProbeSample(
                    timestamp=datetime.now(timezone.utc),
                    latency_ms=None,
                    status=SampleStatus.ERROR,
                    error=f"Unexpected probe error: {exc}",
                )
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
    executor: Executor | None = None,
    probe_once_fn: ProbeOnce | None = None,
) -> None:
    """Run one target's probe loop without blocking other targets or UI refresh."""
    if initial_delay > 0:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=initial_delay)
            return
        except asyncio.TimeoutError:
            pass

    probes_completed = 0
    probe_limit = getattr(args, "count", None)
    while not stop_event.is_set():
        probe_started = time.monotonic()
        if probe_once_fn is None:
            await probe_once(
                target,
                args=args,
                mode=mode,
                semaphore=semaphore,
                executor=executor,
            )
        else:
            await probe_once_fn()

        probes_completed += 1
        if probe_limit is not None and probes_completed >= probe_limit:
            return

        if stop_event.is_set():
            return

        # Subtract the probe's own duration so cadence tracks the interval even
        # when probes are slow (e.g. timeout > interval against a dead host).
        wait_timeout = _iteration_sleep(
            interval=args.interval,
            iteration_elapsed=time.monotonic() - probe_started,
        )
        try:
            await asyncio.wait_for(immediate_event.wait(), timeout=wait_timeout)
        except asyncio.TimeoutError:
            continue
        finally:
            immediate_event.clear()


def print_sample(
    target: TargetRun, sample: ProbeSample | None, *, stream: TextIO | None = None
) -> None:
    out = sys.stdout if stream is None else stream
    target_text = sanitize_display(target.target)
    if sample is None:
        print(
            f"{datetime.now(timezone.utc).isoformat()} "
            f"{target_text} dns_failure error={sanitize_display(target.error or '')}",
            file=out,
        )
        return

    latency = "-" if sample.latency_ms is None else f"{sample.latency_ms:.2f}ms"
    error = "" if sample.error is None else f" error={sanitize_display(sample.error)}"
    print(
        f"{sample.timestamp.isoformat()} {target_text} "
        f"{sample.status.value} latency={latency}{error}",
        file=out,
    )


async def wait_for_stop_event(stop_event: asyncio.Event, timeout: float) -> bool:
    """Return whether the stop event fired before the timeout elapsed."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return False
    return True


def _iteration_sleep(
    *,
    interval: float,
    iteration_elapsed: float,
    duration_remaining: float | None = None,
) -> float:
    sleep_for = interval - iteration_elapsed
    if sleep_for <= 0:
        sleep_for = MIN_OVERRUN_SLEEP

    if duration_remaining is not None:
        sleep_for = min(sleep_for, max(0.0, duration_remaining))

    return sleep_for


def _icmp_executor(args: RunConfig, mode: ProbeMode) -> ThreadPoolExecutor | None:
    if mode != ProbeMode.ICMP:
        return None
    return ThreadPoolExecutor(max_workers=args.concurrency)


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
    executor = _icmp_executor(args, mode)
    # With `--output -` the JSON document owns stdout; per-probe lines move to
    # stderr so the exported document stays machine-parseable.
    sample_stream = sys.stderr if args.output is not None and str(args.output) == "-" else None

    try:
        targets = await resolve_runs(args)

        while True:
            iteration += 1
            iteration_started_at = datetime.now(timezone.utc)
            probes = [
                probe_once(
                    target,
                    args=args,
                    mode=mode,
                    semaphore=semaphore,
                    executor=executor,
                )
                for target in targets
            ]
            samples = await asyncio.gather(*probes) if probes else []
            sample_by_target = iter(samples)

            for target in targets:
                sample = next(sample_by_target)
                print_sample(target, sample, stream=sample_stream)

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
            duration_remaining = (
                None if args.duration is None else args.duration - duration_elapsed
            )
            sleep_for = _iteration_sleep(
                interval=args.interval,
                iteration_elapsed=iteration_elapsed,
                duration_remaining=duration_remaining,
            )

            if sleep_for > 0 and await wait_for_stop_event(stop_event, sleep_for):
                exit_reason = "interrupted"
                break
    except (asyncio.CancelledError, KeyboardInterrupt):
        exit_reason = "interrupted"
    finally:
        cleanup_signal_handlers()
        if executor is not None:
            # Don't block the event loop joining in-flight pings; drop queued
            # probes. Lingering threads finish within timeout_s and their
            # results are discarded.
            executor.shutdown(wait=False, cancel_futures=True)

    ended_at = datetime.now(timezone.utc)
    return targets, exit_reason, started_at, ended_at


# Exit 3 keeps "target down" distinct from argparse usage errors (exit 2).
EXIT_TARGETS_DOWN = 3


def exit_code_for_targets(
    targets: list[TargetRun],
    *,
    fail_on_any_down: bool,
    fail_on_all_down: bool,
) -> int:
    """Return the process exit code for completed target statuses."""
    if not targets or (not fail_on_any_down and not fail_on_all_down):
        return 0

    usable_statuses = {TargetStatus.HEALTHY, TargetStatus.INTERMITTENT}
    down_targets = [target for target in targets if target.status not in usable_statuses]

    if fail_on_any_down:
        return EXIT_TARGETS_DOWN if down_targets else 0

    return EXIT_TARGETS_DOWN if len(down_targets) == len(targets) else 0


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
            overwrite=args.overwrite,
            output_mode=args.output_mode,
        )

    fail_on_all_down = args.fail_on_all_down or args.fail_on_down
    return exit_code_for_targets(
        targets,
        fail_on_any_down=args.fail_on_any_down,
        fail_on_all_down=fail_on_all_down,
    )
