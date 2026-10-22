"""Runtime orchestration for TUI and no-TUI modes."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
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

ProbeOnce = Callable[[], Awaitable[ProbeSample | None]]
SampleCallback = Callable[[TargetRun, ProbeSample | None], None]
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

    # Resolved but not yet probed: stay pending until the first sample lands.
    # DOWN needs evidence, and a run that ends before this target's first probe
    # must not report a healthy host as down.
    return TargetRun(
        target=resolved.target,
        resolved_address=resolved.address,
        resolved_family=resolved.family,
        resolved_addresses=resolved.addresses,
        status=TargetStatus.RESOLVING,
        error=None,
    )


def _is_ip_literal(target: str) -> bool:
    try:
        family_from_ip(target)
    except ValueError:
        return False
    return True


async def resolve_run_target(target: str, args: RunConfig) -> TargetRun:
    family = AddressFamily(args.address_family)
    if args.numeric or _is_ip_literal(target):
        # Nothing to look up: skip the resolver queue so IP literals can be
        # probed at once instead of waiting behind slow DNS names.
        run = target_run_from_resolution(await resolve_target(target, family, numeric=args.numeric))
        run._last_resolve_time = _monotonic_time()
        return run
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
    """Resolve every target before returning, in requested order.

    Neither run mode uses this: both resolve each target with
    ``resolve_run_target`` and start probing it as soon as it resolves. It
    remains for callers that want every lookup settled up front.
    """
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


async def probe_once(
    target: TargetRun,
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
) -> ProbeSample | None:
    now = _monotonic_time()
    last_resolve_time = target._last_resolve_time
    resolve_cooldown_elapsed = (
        last_resolve_time is None or now - last_resolve_time >= DNS_RETRY_INTERVAL_SECONDS
    )
    needs_resolution = not target.resolved_address
    refresh_stale_resolution = (
        not needs_resolution
        and not args.numeric
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
                numeric=args.numeric,
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


def stop_signals() -> tuple[signal.Signals, ...]:
    """Return the signals that end a run cleanly: SIGINT, SIGTERM and SIGHUP.

    SIGHUP (closed terminal, dropped SSH session) is left alone when it was
    already ignored at startup, so a ``nohup`` run keeps going after logout.
    """
    signals = [signal.SIGINT, signal.SIGTERM]
    hangup = getattr(signal, "SIGHUP", None)
    if hangup is not None and signal.getsignal(hangup) is not signal.SIG_IGN:
        signals.append(hangup)
    return tuple(signals)


def install_stop_signal_callback(callback: Callable[[], object]) -> StopSignalCleanup:
    """Run ``callback`` on the event loop for each of ``stop_signals()``."""
    loop = asyncio.get_running_loop()
    installed: list[signal.Signals] = []

    for signal_number in stop_signals():
        try:
            loop.add_signal_handler(signal_number, callback)
        except (NotImplementedError, RuntimeError, ValueError):
            continue
        installed.append(signal_number)

    def cleanup() -> None:
        for signal_number in installed:
            loop.remove_signal_handler(signal_number)

    return cleanup


def install_stop_signal_handlers(stop_event: asyncio.Event) -> StopSignalCleanup:
    """Install SIGINT/SIGTERM/SIGHUP handlers that let no-TUI mode write final output."""
    return install_stop_signal_callback(stop_event.set)


def stagger_delay(*, index: int, count: int, interval: float) -> float:
    """Return a start delay that spreads hosts evenly across one interval."""
    if count <= 0:
        return 0.0

    return round((interval / count) * index, 6)


def stagger_window(*, interval: float, remaining: float | None) -> float:
    """Return the span that first probes are spread across.

    Normally one interval. A ``--duration`` shorter than two intervals spreads
    them across half of the remaining run instead, so every host still gets
    its first probe well before the deadline.
    """
    if remaining is None:
        return interval
    return max(0.0, min(interval, remaining / 2))


def first_probe_delay(*, index: int, count: int, window: float, elapsed: float) -> float:
    """Return how long target ``index`` waits before its first probe.

    Slots are spread evenly across ``window`` from a fixed run start, so a
    target that resolves late still lands in its own slot instead of bunching
    up with others, and one whose slot has already passed starts at once.
    """
    return max(0.0, stagger_delay(index=index, count=count, interval=window) - elapsed)


def record_probe_loop_failure(target: TargetRun, error: BaseException) -> None:
    """Mark a target whose probe loop ended on an unexpected error.

    The target then reports the failure instead of keeping its last state as
    if it were still being probed.
    """
    target.status = TargetStatus.ERROR
    target.error = f"Unexpected probe loop error: {error}"


def _cancel_requested() -> bool:
    """Return whether the running task has a pending cancellation request.

    ``Task.cancelling()`` is new in Python 3.11. On 3.10 this is always False
    and loops rely on the stop event, which both modes set before cancelling.
    """
    if sys.version_info < (3, 11):
        return False
    task = asyncio.current_task()
    return task is not None and task.cancelling() > 0


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
    mode passes ``on_sample`` to print each result as it lands. Shutdown sets
    ``stop_event`` before cancelling the task, so the loop ends even when a
    probe swallows its cancellation; a result such a probe returns was
    measured before the stop and is still reported.
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
    # A "probe now" request made before the loop started is served by the
    # first probe rather than triggering a second one right after it.
    immediate_event.clear()

    probes_completed = 0
    probe_limit = args.count
    while not stop_event.is_set():
        probe_started = _monotonic_time()
        try:
            if probe_once_fn is None:
                sample = await probe_once(target, args=args, mode=mode, semaphore=semaphore)
            else:
                sample = await probe_once_fn()
        except Exception as exc:
            # Record an unexpected failure like any failed probe so the row
            # keeps updating and the run carries on instead of losing the loop.
            sample = ProbeSample(
                timestamp=datetime.now(timezone.utc),
                latency_ms=None,
                status=SampleStatus.ERROR,
                error=f"Unexpected probe error: {exc}",
            )
            target.apply_sample(
                sample,
                fail_threshold=args.fail_threshold,
                jitter_threshold_ms=args.jitter_threshold,
            )
        if on_sample is not None:
            on_sample(target, sample)

        probes_completed += 1
        if probe_limit is not None and probes_completed >= probe_limit:
            return

        # A probe may swallow the cancellation that shutdown sends it (TCP
        # close teardown keeps a success measured before the stop), so check
        # for a stop here rather than waiting out another interval.
        if stop_event.is_set() or _cancel_requested():
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


def _iteration_sleep(*, interval: float, iteration_elapsed: float) -> float:
    sleep_for = interval - iteration_elapsed
    if sleep_for <= 0:
        sleep_for = MIN_OVERRUN_SLEEP
    return sleep_for


async def _run_probe_loops_until_shutdown(
    targets: list[TargetRun],
    *,
    args: RunConfig,
    mode: ProbeMode,
    semaphore: asyncio.Semaphore,
    stop_event: asyncio.Event,
    deadline_at: float | None,
    on_sample: SampleCallback,
) -> str | None:
    """Resolve and probe every target until done, a stop is requested, or the deadline.

    ``targets`` holds the pending runs in requested order. Each target's probe
    loop starts as soon as that target resolves, so IP literals and fast
    lookups are probed while slow names are still resolving. Each resolved run
    replaces its pending entry in place, so an interrupted or deadline run
    keeps every completed lookup in the requested order.

    Returns ``None`` when every loop completed on its own (``--count``), otherwise
    the stop reason. Lookups and loops still running at shutdown are cancelled
    promptly.
    """
    loop = asyncio.get_running_loop()
    if stop_event.is_set():
        return "interrupted"
    if deadline_at is not None and loop.time() >= deadline_at:
        return "deadline"

    # First probes are spread across one interval like the TUI, so N hosts do
    # not fire in one burst and stay phase-locked into periodic bursts. With
    # --count the last host therefore finishes up to one interval later.
    run_start = loop.time()
    window = stagger_window(
        interval=args.interval,
        remaining=None if deadline_at is None else deadline_at - run_start,
    )
    # No-TUI mode has no "probe now" key, so one shared, never-set event serves
    # every loop.
    immediate_event = asyncio.Event()

    async def resolve_and_probe(index: int) -> None:
        run = targets[index]
        try:
            resolved = await resolve_run_target(run.target, args)
            resolved.samples = SampleWindow(resolved.samples, maxlen=run.samples.maxlen)
            targets[index] = run = resolved
            if stop_event.is_set():
                return
            await probe_target_loop(
                resolved,
                args=args,
                mode=mode,
                semaphore=semaphore,
                stop_event=stop_event,
                immediate_event=immediate_event,
                initial_delay=first_probe_delay(
                    index=index,
                    count=len(targets),
                    window=window,
                    elapsed=loop.time() - run_start,
                ),
                on_sample=on_sample,
            )
        except Exception as exc:
            # One target's unexpected failure must not end the whole run
            # without a report; record it on that target instead.
            record_probe_loop_failure(run, exc)

    tasks = [asyncio.create_task(resolve_and_probe(index)) for index in range(len(targets))]
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
        # gather() settles as soon as one loop is cancelled, so sibling loops
        # may still be running here; the finally block cancels them.
        if any(task.cancelled() for task in tasks):
            return "interrupted"
        return None
    finally:
        # Set the stop event before cancelling: a probe that swallows its
        # cancellation (TCP close teardown) then ends its loop instead of
        # probing on past the deadline and keeping the run alive.
        stop_event.set()
        for task in tasks:
            task.cancel()
        all_loops.cancel()
        stop_wait.cancel()
        await asyncio.gather(all_loops, stop_wait, *tasks, return_exceptions=True)


def _discard_output(stream: TextIO) -> None:
    """Point ``stream``'s descriptor at the null device once its reader is gone.

    Python flushes standard streams at exit; without this, the unwritten tail
    raises BrokenPipeError again there and the exit status becomes 120.
    """
    try:
        descriptor = stream.fileno()
        devnull = os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError, ValueError):
        return
    try:
        os.dup2(devnull, descriptor)
    except OSError:
        pass
    finally:
        os.close(devnull)


def flush_or_discard_output(stream: TextIO | None) -> None:
    """Flush ``stream``, or discard what it still holds when writing fails.

    Restoring the terminal after a hangup leaves bytes that can never be
    written; the flush at exit would then fail and turn the exit status into 120.
    """
    if stream is None:
        return
    try:
        stream.flush()
    except (OSError, ValueError):
        _discard_output(stream)


def _reader_is_gone(error: BaseException) -> bool:
    """Return whether a write failed because nothing reads the stream any more."""
    if isinstance(error, (BrokenPipeError, ConnectionResetError)):
        return True
    # A hung-up terminal fails writes with EIO.
    return isinstance(error, OSError) and error.errno == errno.EIO


def _warn(message: str) -> None:
    # print() to a stderr of None (closed at startup) is a no-op.
    with contextlib.suppress(OSError, ValueError):
        print(f"pinghue: warning: {message}", file=sys.stderr, flush=True)


class _SampleLinePrinter:
    """Print no-TUI probe lines and push them to the reader within one loop turn.

    Python block-buffers stdout when it is a pipe or a file (``| tee``,
    journald), which would hold lines back by kilobytes. One flush is scheduled
    per event-loop turn instead of one ``write()`` per line, so a burst of
    results still reaches the reader together and promptly.

    When the reader goes away (``| head``, a hung-up terminal) printing stops
    and ``on_closed`` asks the run to stop as if interrupted, so the run still
    ends cleanly and ``--output`` is still written. Any other write failure
    (a full disk, for example) stops the run the same way after a warning on
    stderr. A ``stream`` of None (its descriptor was closed at startup) drops
    the lines and lets the run go on.
    """

    def __init__(self, stream: TextIO | None, *, on_closed: Callable[[], object]) -> None:
        self._stream = stream
        self._on_closed = on_closed
        self._flush_scheduled = False
        self._closed = False

    def __call__(self, target: TargetRun, sample: ProbeSample | None) -> None:
        if self._closed or self._stream is None:
            return
        try:
            print_sample(target, sample, stream=self._stream)
        except (OSError, ValueError) as exc:
            self._close(self._stream, exc)
            return
        if not self._flush_scheduled:
            self._flush_scheduled = True
            asyncio.get_running_loop().call_soon(self.flush)

    def flush(self) -> None:
        self._flush_scheduled = False
        stream = self._stream
        if self._closed or stream is None:
            return
        try:
            stream.flush()
        except (OSError, ValueError) as exc:
            self._close(stream, exc)

    def _close(self, stream: TextIO, error: BaseException) -> None:
        self._closed = True
        if not _reader_is_gone(error):
            _warn(f"could not write probe lines, stopping the run: {error}")
        _discard_output(stream)
        self._on_closed()


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
    # stderr so the exported document stays machine-parseable. A stream that is
    # None (closed at startup) drops the lines rather than using the other one.
    sample_stream = (
        sys.stderr if args.output is not None and str(args.output) == "-" else sys.stdout
    )
    print_line = _SampleLinePrinter(sample_stream, on_closed=stop_event.set)

    try:
        stop_reason = await _run_probe_loops_until_shutdown(
            targets,
            args=args,
            mode=mode,
            semaphore=semaphore,
            stop_event=stop_event,
            deadline_at=deadline_at,
            on_sample=print_line,
        )
        if stop_reason is not None:
            exit_reason = stop_reason
    except (asyncio.CancelledError, KeyboardInterrupt):
        exit_reason = "interrupted"
    finally:
        cleanup_signal_handlers()
        print_line.flush()

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
