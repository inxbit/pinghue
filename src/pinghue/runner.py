"""Runtime orchestration for TUI and no-TUI modes."""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Executor
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
    SampleWindow,
    TargetRun,
    TargetStatus,
    retained_samples_per_target,
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


def _monotonic_time() -> float:
    return time.monotonic()


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
        run = TargetRun(
            target=target,
            resolved_address=None,
            resolved_family=None,
            resolved_addresses=(),
            status=TargetStatus.DNS_FAILURE,
            error=str(exc),
        )
    else:
        run = target_run_from_resolution(resolved)
    run._last_resolve_time = _monotonic_time()
    return run


def apply_retained_sample_budget(runs: list[TargetRun]) -> None:
    """Apply the run-wide retained-sample budget uniformly to all targets."""
    samples_window = retained_samples_per_target(len(runs))
    for run in runs:
        run.samples = SampleWindow(run.samples, maxlen=samples_window)


def _pending_target_runs(targets: Sequence[str]) -> list[TargetRun]:
    """Return ordered unresolved targets for interrupted/deadline output."""
    runs = [TargetRun(target=target, status=TargetStatus.RESOLVING) for target in targets]
    apply_retained_sample_budget(runs)
    return runs


async def resolve_runs(args: RunConfig) -> list[TargetRun]:
    runs = await asyncio.gather(
        *(resolve_run_target(target, args) for target in args.targets)
    )
    apply_retained_sample_budget(runs)
    return runs


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
    now = _monotonic_time()
    last_resolve_time = target._last_resolve_time
    resolve_cooldown_elapsed = (
        last_resolve_time is None
        or now - last_resolve_time >= DNS_RETRY_INTERVAL_SECONDS
    )
    needs_resolution = not target.resolved_address
    refresh_stale_resolution = (
        not needs_resolution
        and not getattr(args, "numeric", False)
        and target.status != TargetStatus.PERMISSION_DENIED
        and target.samples.consecutive_failures >= args.fail_threshold
        and resolve_cooldown_elapsed
    )

    if needs_resolution or refresh_stale_resolution:
        if needs_resolution and not resolve_cooldown_elapsed:
            return None
        target._last_resolve_time = now
        family = AddressFamily(args.address_family)
        try:
            resolved = await _resolve_target_bounded(
                target.target,
                family,
                numeric=getattr(args, "numeric", False),
            )
            if resolved.error:
                if needs_resolution:
                    target.status = TargetStatus.DNS_FAILURE
                    target.error = resolved.error
                    return None
            elif resolved.address is not None:
                target.resolved_address = resolved.address
                target.resolved_family = resolved.family
                target.resolved_addresses = resolved.addresses
                if needs_resolution:
                    target.status = TargetStatus.DOWN
                    target.error = None
        except Exception as exc:
            if needs_resolution:
                target.status = TargetStatus.DNS_FAILURE
                target.error = str(exc)
                return None
        finally:
            # Anchor the cooldown to completion so targets queued behind the
            # resolver semaphore do not immediately issue another lookup.
            target._last_resolve_time = _monotonic_time()

    primary = target.resolved_address
    if not primary:
        return None
    addresses = list(target.resolved_addresses) if target.resolved_addresses else [primary]
    if primary in addresses:
        addresses.remove(primary)
        addresses.insert(0, primary)

    sample: ProbeSample | None = None
    primary_failure: ProbeSample | None = None

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
            if primary_failure is None:
                primary_failure = sample
            if sample.status == SampleStatus.OK:
                target.resolved_address = address
                target.resolved_family = family_from_ip(address)
                break

    if sample is None:
        return None
    if sample.status != SampleStatus.OK and primary_failure is not None:
        sample = primary_failure

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
        stop_wait = asyncio.create_task(stop_event.wait())
        immediate_wait = asyncio.create_task(immediate_event.wait())
        try:
            await asyncio.wait(
                (stop_wait, immediate_wait),
                timeout=initial_delay,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_wait.cancel()
            immediate_wait.cancel()
            await asyncio.gather(stop_wait, immediate_wait, return_exceptions=True)
        if stop_event.is_set():
            return
        immediate_event.clear()

    probes_completed = 0
    probe_limit = getattr(args, "count", None)
    while not stop_event.is_set():
        probe_started = _monotonic_time()
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
            iteration_elapsed=_monotonic_time() - probe_started,
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


async def _run_probe_batch(
    probes: Sequence[Awaitable[ProbeSample | None]],
    stop_event: asyncio.Event,
    *,
    deadline_at: float | None,
) -> tuple[list[tuple[int, ProbeSample | None]], str | None]:
    """Run one probe batch, cancelling it promptly at shutdown or deadline."""
    loop = asyncio.get_running_loop()
    tasks = [asyncio.ensure_future(probe) for probe in probes]
    batch = asyncio.gather(*tasks)

    def completed_results() -> list[tuple[int, ProbeSample | None]]:
        return [
            (index, task.result())
            for index, task in enumerate(tasks)
            if task.done() and not task.cancelled()
        ]

    if stop_event.is_set():
        batch.cancel()
        await asyncio.gather(batch, return_exceptions=True)
        return completed_results(), "interrupted"
    if deadline_at is not None and loop.time() >= deadline_at:
        batch.cancel()
        await asyncio.gather(batch, return_exceptions=True)
        return completed_results(), "deadline"

    stop_wait = asyncio.create_task(stop_event.wait())
    try:
        timeout = (
            None
            if deadline_at is None
            else max(0.0, deadline_at - loop.time())
        )
        await asyncio.wait(
            (batch, stop_wait),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_event.is_set():
            batch.cancel()
            await asyncio.gather(batch, return_exceptions=True)
            return completed_results(), "interrupted"
        if deadline_at is not None and loop.time() >= deadline_at:
            batch.cancel()
            await asyncio.gather(batch, return_exceptions=True)
            return completed_results(), "deadline"
        if not batch.done():
            batch.cancel()
            await asyncio.gather(batch, return_exceptions=True)
            return completed_results(), "deadline"
        return list(enumerate(await batch)), None
    finally:
        stop_wait.cancel()
        await asyncio.gather(stop_wait, return_exceptions=True)
        if not batch.done():
            batch.cancel()
            await asyncio.gather(batch, return_exceptions=True)


async def _resolve_runs_until_shutdown(
    args: RunConfig,
    stop_event: asyncio.Event,
    *,
    deadline_at: float | None,
    pending_runs: list[TargetRun] | None = None,
) -> tuple[list[TargetRun], str | None]:
    """Resolve targets while honoring both process signals and run duration."""
    unresolved = (
        pending_runs if pending_runs is not None else _pending_target_runs(args.targets)
    )
    loop = asyncio.get_running_loop()
    if stop_event.is_set():
        return unresolved, "interrupted"
    if deadline_at is not None and loop.time() >= deadline_at:
        return unresolved, "deadline"

    resolution = asyncio.create_task(resolve_runs(args))
    stop_wait = asyncio.create_task(stop_event.wait())
    try:
        timeout = (
            None
            if deadline_at is None
            else max(0.0, deadline_at - loop.time())
        )
        await asyncio.wait(
            (resolution, stop_wait),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_event.is_set():
            return unresolved, "interrupted"
        if deadline_at is not None and loop.time() >= deadline_at:
            return unresolved, "deadline"
        if not resolution.done():
            return unresolved, "deadline"
        return await resolution, None
    finally:
        stop_wait.cancel()
        if not resolution.done():
            resolution.cancel()
        await asyncio.gather(stop_wait, resolution, return_exceptions=True)


async def run_no_tui(
    args: RunConfig,
    mode: ProbeMode,
) -> tuple[list[TargetRun], str, datetime, datetime]:
    loop = asyncio.get_running_loop()
    deadline_at = None if args.duration is None else loop.time() + args.duration
    started_at = datetime.now(timezone.utc)
    targets = _pending_target_runs(args.targets)
    semaphore = asyncio.Semaphore(args.concurrency)
    stop_event = asyncio.Event()
    cleanup_signal_handlers = install_stop_signal_handlers(stop_event)
    exit_reason = "completed"
    iteration = 0
    executor: Executor | None = None
    # With `--output -` the JSON document owns stdout; per-probe lines move to
    # stderr so the exported document stays machine-parseable.
    sample_stream = sys.stderr if args.output is not None and str(args.output) == "-" else None

    try:
        targets, stop_reason = await _resolve_runs_until_shutdown(
            args,
            stop_event,
            deadline_at=deadline_at,
            pending_runs=targets,
        )
        if stop_reason is not None:
            exit_reason = stop_reason

        while stop_reason is None and not stop_event.is_set():
            iteration += 1
            iteration_started_at = _monotonic_time()
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
            samples, stop_reason = await _run_probe_batch(
                probes,
                stop_event,
                deadline_at=deadline_at,
            )

            for target_index, sample in samples:
                print_sample(targets[target_index], sample, stream=sample_stream)

            if stop_reason is not None:
                exit_reason = stop_reason
                break

            if stop_event.is_set():
                exit_reason = "interrupted"
                break

            if args.count is not None and iteration >= args.count:
                break

            iteration_elapsed = _monotonic_time() - iteration_started_at
            duration_remaining = (
                None if deadline_at is None else max(0.0, deadline_at - loop.time())
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
