"""Runtime orchestration for TUI and no-TUI modes."""

from __future__ import annotations

import asyncio
import random
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
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
from pinghue.probes import (
    ResolvedTarget,
    ResolverBusyError,
    family_from_ip,
    icmp_probe,
    resolve_target,
    tcp_probe,
)

ProbeOnce = Callable[[], Awaitable[ProbeSample | None]]
SampleCallback = Callable[[TargetRun, ProbeSample | None], None]
StopSignalCleanup = Callable[[], None]
MIN_OVERRUN_SLEEP = 0.1
# Lookups awaited at once; probes.MAX_DNS_DAEMON_THREADS leaves room above this
# for lookups abandoned at their budget whose threads are still running.
DNS_RESOLVE_CONCURRENCY = 32
DNS_RETRY_INTERVAL_SECONDS = 10.0
# Above glibc's default 5 s per-nameserver timeout (resolv.conf "timeout:5"), so
# a lookup that fails over from a dead first nameserver, or retransmits one
# lost query, still answers within budget.
DNS_LOOKUP_TIMEOUT_SECONDS = 12.0
# Retry delays after 1, 2 and 3+ lookup timeouts, so names in a blackholed zone
# back off instead of taking a resolver thread every cycle.
DNS_TIMEOUT_BACKOFF_SECONDS = (10.0, 30.0, 60.0)
# A failure that took at least glibc's default per-nameserver timeout also
# counts as a timeout: the resolver gave up waiting rather than answering.
DNS_SLOW_FAILURE_SECONDS = 5.0
# Each retry waits up to this fraction longer, so names that failed together
# do not retry in lockstep.
DNS_RETRY_JITTER_FRACTION = 0.2

_dns_semaphore: asyncio.Semaphore | None = None
_dns_semaphore_loop: asyncio.AbstractEventLoop | None = None


@dataclass(frozen=True)
class _DnsRetry:
    timeouts: int
    delay_s: float


_dns_retries: dict[str, _DnsRetry] = {}
_dns_retries_loop: asyncio.AbstractEventLoop | None = None

# Stale-address refreshes running beside their targets' probes, at most one per
# target. Keyed by id(target); the entry holds the target, so the id stays its
# own until a later probe takes the finished lookup.
_DnsRefreshes = dict[int, tuple[TargetRun, asyncio.Task[ResolvedTarget | None]]]
_dns_refreshes: _DnsRefreshes = {}
_dns_refreshes_loop: asyncio.AbstractEventLoop | None = None


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


def _get_dns_retries() -> dict[str, _DnsRetry]:
    global _dns_retries, _dns_retries_loop
    loop = asyncio.get_running_loop()
    if _dns_retries_loop is not loop:
        _dns_retries = {}
        _dns_retries_loop = loop
    return _dns_retries


def _get_dns_refreshes() -> _DnsRefreshes:
    global _dns_refreshes, _dns_refreshes_loop
    loop = asyncio.get_running_loop()
    if _dns_refreshes_loop is not loop:
        _dns_refreshes = {}
        _dns_refreshes_loop = loop
    return _dns_refreshes


def _dns_retry_delay(target: str) -> float:
    """Return how long after its last finished lookup a name may be looked up again."""
    retry = _get_dns_retries().get(target)
    return DNS_RETRY_INTERVAL_SECONDS if retry is None else retry.delay_s


def _resolve_cooldown_elapsed(target: TargetRun, now: float) -> bool:
    last_resolve_time = target._last_resolve_time
    return last_resolve_time is None or now - last_resolve_time >= _dns_retry_delay(target.target)


def _record_dns_outcome(target: str, *, failed: bool, timed_out: bool) -> None:
    """Set when a name may be looked up again after this lookup's verdict.

    An answer clears the name's backoff. A failure waits
    DNS_RETRY_INTERVAL_SECONDS, or the DNS_TIMEOUT_BACKOFF_SECONDS step for the
    timeouts seen so far, plus jitter.
    """
    retries = _get_dns_retries()
    if not failed:
        retries.pop(target, None)
        return
    previous = retries.get(target)
    # A quick failure after timeouts keeps the backoff level; only an answer resets it.
    timeouts = (previous.timeouts if previous else 0) + int(timed_out)
    base_delay = (
        DNS_TIMEOUT_BACKOFF_SECONDS[min(timeouts, len(DNS_TIMEOUT_BACKOFF_SECONDS)) - 1]
        if timeouts
        else DNS_RETRY_INTERVAL_SECONDS
    )
    jitter = random.uniform(0.0, DNS_RETRY_JITTER_FRACTION)
    retries[target] = _DnsRetry(timeouts=timeouts, delay_s=base_delay * (1.0 + jitter))


async def _resolve_target_bounded(
    target: str,
    family: AddressFamily,
    *,
    numeric: bool,
) -> ResolvedTarget | None:
    """Resolve under the shared DNS concurrency bound and lookup budget.

    Returns None when no resolver thread was available, so no lookup ran: that is
    local capacity, not a DNS verdict, and callers keep the target's state.
    Each verdict sets the name's next retry delay.
    """
    loop = asyncio.get_running_loop()
    async with _get_dns_semaphore():
        started = loop.time()
        try:
            resolved = await asyncio.wait_for(
                resolve_target(target, family, numeric=numeric),
                timeout=DNS_LOOKUP_TIMEOUT_SECONDS,
            )
        except ResolverBusyError:
            return None
        except asyncio.TimeoutError:
            _record_dns_outcome(target, failed=True, timed_out=True)
            return ResolvedTarget(
                target,
                None,
                None,
                f"getaddrinfo timed out after {DNS_LOOKUP_TIMEOUT_SECONDS:g}s",
            )
    _record_dns_outcome(
        target,
        failed=resolved.error is not None,
        timed_out=loop.time() - started >= DNS_SLOW_FAILURE_SECONDS,
    )
    return resolved


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
        if resolved is None:
            # No lookup ran, so there is no verdict yet: stay pending, and with
            # no cooldown the first probe iteration retries right away.
            return TargetRun(target=target, status=TargetStatus.RESOLVING)
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
    runs = await asyncio.gather(*(resolve_run_target(target, args) for target in args.targets))
    apply_retained_sample_budget(runs)
    return runs


async def _probe_address(address: str, *, args: RunConfig, mode: ProbeMode) -> ProbeSample:
    if mode == ProbeMode.TCP:
        return await tcp_probe(address, args.port or 0, timeout_s=args.timeout)

    return await icmp_probe(
        address,
        timeout_s=args.timeout,
        address_family=AddressFamily(args.address_family),
    )


def _is_ip_literal(target: str) -> bool:
    try:
        family_from_ip(target)
    except ValueError:
        return False
    return True


async def _refresh_lookup(target: TargetRun, family: AddressFamily) -> ResolvedTarget | None:
    try:
        return await _resolve_target_bounded(target.target, family, numeric=False)
    except Exception:
        # A refresh only ever replaces addresses; a failed one keeps them.
        return None
    finally:
        # Anchor the cooldown to completion, capacity misses included, so a
        # refresh queued behind the resolver bound is not reissued at once.
        target._last_resolve_time = _monotonic_time()


def _refresh_stale_resolution(target: TargetRun, *, args: RunConfig, now: float) -> None:
    """Look a failing target's name up again without delaying its probes.

    The lookup runs as a background task, at most one per target, while the
    known addresses keep being probed: a slow or hung lookup (a dead
    nameserver, a blackholed zone) or a busy resolver must not hide a recovery.
    The first probe after the lookup finishes applies a new answer if the
    target is still failing. IP-literal targets have nothing to look up.
    """
    refreshes = _get_dns_refreshes()
    stale = (
        not args.numeric
        and target.status != TargetStatus.PERMISSION_DENIED
        and target.samples.consecutive_failures >= args.fail_threshold
    )
    running = refreshes.get(id(target))
    if running is not None:
        task = running[1]
        if not task.done():
            return
        del refreshes[id(target)]
        resolved = None if task.cancelled() else task.result()
        if stale and resolved is not None and not resolved.error and resolved.address:
            target.resolved_address = resolved.address
            target.resolved_family = resolved.family
            target.resolved_addresses = resolved.addresses

    if not stale or _is_ip_literal(target.target) or not _resolve_cooldown_elapsed(target, now):
        return
    target._last_resolve_time = now
    task = asyncio.create_task(_refresh_lookup(target, AddressFamily(args.address_family)))
    refreshes[id(target)] = (target, task)


async def probe_once(
    target: TargetRun,
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
) -> ProbeSample | None:
    now = _monotonic_time()
    if target.resolved_address:
        _refresh_stale_resolution(target, args=args, now=now)
    else:
        if not _resolve_cooldown_elapsed(target, now):
            return None
        last_resolve_time = target._last_resolve_time
        target._last_resolve_time = now
        resolved: ResolvedTarget | None
        try:
            resolved = await _resolve_target_bounded(
                target.target,
                AddressFamily(args.address_family),
                numeric=args.numeric,
            )
        except Exception as exc:
            resolved = ResolvedTarget(target.target, None, None, str(exc))

        if resolved is None:
            # No resolver thread was available, so no lookup ran and there is no
            # DNS verdict: keep the target's state and retry on the next
            # iteration rather than after the DNS cooldown.
            target._last_resolve_time = last_resolve_time
            return None
        # Anchor the cooldown to completion so targets queued behind the
        # resolver semaphore do not immediately issue another lookup.
        target._last_resolve_time = _monotonic_time()
        if resolved.error:
            target.status = TargetStatus.DNS_FAILURE
            target.error = resolved.error
            return None
        if resolved.address is not None:
            target.resolved_address = resolved.address
            target.resolved_family = resolved.family
            target.resolved_addresses = resolved.addresses
            # Resolved but not probed yet: stay pending, not DOWN, until the
            # first sample lands.
            target.status = TargetStatus.RESOLVING
            target.error = None

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
                sample = await _probe_address(address, args=args, mode=mode)
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
    on_sample: SampleCallback | None = None,
    probe_once_fn: ProbeOnce | None = None,
) -> None:
    """Run one target's probe loop without blocking other targets or UI refresh.

    Both the TUI and no-TUI mode drive their probes through this loop; no-TUI
    mode passes ``on_sample`` to print each result as it lands.
    """
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
    probe_limit = args.count
    while not stop_event.is_set():
        probe_started = _monotonic_time()
        if probe_once_fn is None:
            sample = await probe_once(target, args=args, mode=mode, semaphore=semaphore)
        else:
            sample = await probe_once_fn()
        if on_sample is not None:
            on_sample(target, sample)

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
        if target.status == TargetStatus.RESOLVING:
            # No lookup could run yet (resolver threads busy): no verdict to print.
            return
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


def _iteration_sleep(*, interval: float, iteration_elapsed: float) -> float:
    sleep_for = interval - iteration_elapsed
    if sleep_for <= 0:
        sleep_for = MIN_OVERRUN_SLEEP
    return sleep_for


async def _run_probe_loops_until_shutdown(
    targets: Sequence[TargetRun],
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
    stop_event: asyncio.Event,
    deadline_at: float | None,
    on_sample: SampleCallback,
) -> str | None:
    """Run every target's probe loop until they finish, a stop is requested, or the deadline.

    Returns ``None`` when every loop completed on its own (``--count``), otherwise
    the stop reason. Loops still running at shutdown are cancelled promptly.
    """
    loop = asyncio.get_running_loop()
    if stop_event.is_set():
        return "interrupted"
    if deadline_at is not None and loop.time() >= deadline_at:
        return "deadline"

    # No-TUI mode has no "probe now" key, so one shared, never-set event serves
    # every loop; probes start together rather than staggered across an interval.
    immediate_event = asyncio.Event()
    tasks = [
        asyncio.create_task(
            probe_target_loop(
                target,
                args=args,
                mode=mode,
                semaphore=semaphore,
                stop_event=stop_event,
                immediate_event=immediate_event,
                initial_delay=0.0,
                on_sample=on_sample,
            )
        )
        for target in targets
    ]
    all_loops = asyncio.gather(*tasks)
    stop_wait = asyncio.create_task(stop_event.wait())
    try:
        timeout = None if deadline_at is None else max(0.0, deadline_at - loop.time())
        await asyncio.wait(
            (all_loops, stop_wait),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_event.is_set():
            return "interrupted"
        if deadline_at is not None and loop.time() >= deadline_at:
            return "deadline"
        if not all_loops.done():
            return "deadline"
        # gather() settles on the first failure, so sibling loops may still be
        # running here; the finally block cancels them.
        for task in tasks:
            if not task.done():
                continue
            if task.cancelled():
                return "interrupted"
            error = task.exception()
            if error is not None:
                raise error
        return None
    finally:
        for task in tasks:
            task.cancel()
        all_loops.cancel()
        stop_wait.cancel()
        await asyncio.gather(all_loops, stop_wait, *tasks, return_exceptions=True)


async def _resolve_runs_until_shutdown(
    args: RunConfig,
    stop_event: asyncio.Event,
    *,
    deadline_at: float | None,
    pending_runs: list[TargetRun] | None = None,
) -> tuple[list[TargetRun], str | None]:
    """Resolve targets while honoring both process signals and run duration."""
    unresolved = pending_runs if pending_runs is not None else _pending_target_runs(args.targets)
    loop = asyncio.get_running_loop()
    if stop_event.is_set():
        return unresolved, "interrupted"
    if deadline_at is not None and loop.time() >= deadline_at:
        return unresolved, "deadline"

    resolution = asyncio.create_task(resolve_runs(args))
    stop_wait = asyncio.create_task(stop_event.wait())
    try:
        timeout = None if deadline_at is None else max(0.0, deadline_at - loop.time())
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
    # With `--output -` the JSON document owns stdout; per-probe lines move to
    # stderr so the exported document stays machine-parseable.
    sample_stream = sys.stderr if args.output is not None and str(args.output) == "-" else None

    def print_to_stream(target: TargetRun, sample: ProbeSample | None) -> None:
        print_sample(target, sample, stream=sample_stream)

    try:
        targets, stop_reason = await _resolve_runs_until_shutdown(
            args,
            stop_event,
            deadline_at=deadline_at,
            pending_runs=targets,
        )
        if stop_reason is None:
            stop_reason = await _run_probe_loops_until_shutdown(
                targets,
                args=args,
                mode=mode,
                semaphore=semaphore,
                stop_event=stop_event,
                deadline_at=deadline_at,
                on_sample=print_to_stream,
            )
        if stop_reason is not None:
            exit_reason = stop_reason
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

    return exit_code_for_targets(
        targets,
        fail_on_any_down=args.fail_on_any_down,
        fail_on_all_down=args.fail_on_all_down,
    )
