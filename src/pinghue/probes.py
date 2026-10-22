"""Async probe implementations."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import queue
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Any, TypeVar

from pinghue.models import AddressFamily, ProbeSample, SampleStatus

MAX_FAILOVER_ADDRESSES = 8
MAX_DNS_DAEMON_THREADS = 128
MAX_ICMP_DAEMON_THREADS = 1_024
_dns_thread_slots = threading.BoundedSemaphore(MAX_DNS_DAEMON_THREADS)
_T = TypeVar("_T")

UNREACHABLE_ERRNOS = {
    value
    for value in (
        getattr(errno, "EHOSTUNREACH", None),
        getattr(errno, "ENETUNREACH", None),
        getattr(errno, "EHOSTDOWN", None),
        getattr(errno, "ENETDOWN", None),
    )
    if value is not None
}


@dataclass(frozen=True)
class ResolvedTarget:
    target: str
    address: str | None
    family: AddressFamily | None
    error: str | None = None
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class _IcmpBackend:
    socket_v4: Callable[..., Any]
    socket_v6: Callable[..., Any]
    request: Callable[..., Any]
    unique_identifier: Callable[[], int]
    timeout_error: type[BaseException]
    permission_error: type[BaseException]
    unreachable_errors: tuple[type[BaseException], ...]
    library_error: type[BaseException]


_icmp_backend: _IcmpBackend | None = None
_icmp_import_error: ImportError | None = None


@dataclass(frozen=True)
class _DaemonWorkItem:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future[Any]
    function: Callable[[], Any]


class _DaemonWorkerPool:
    """Run blocking calls on a lazily created, bounded set of daemon threads."""

    def __init__(self, *, max_workers: int, name: str) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be greater than zero")
        self._max_workers = max_workers
        self._name = name
        self._work_queue: queue.SimpleQueue[_DaemonWorkItem] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._worker_count = 0
        self._outstanding_work_count = 0
        self._next_worker_id = 0

    async def run(self, function: Callable[[], _T]) -> _T:
        """Schedule blocking work and await its result on the calling loop."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[_T] = loop.create_future()

        worker_id: int | None = None
        with self._lock:
            self._outstanding_work_count += 1
            required_workers = min(self._outstanding_work_count, self._max_workers)
            if self._worker_count < required_workers:
                self._worker_count += 1
                self._next_worker_id += 1
                worker_id = self._next_worker_id

        if worker_id is not None:
            try:
                threading.Thread(
                    target=self._worker,
                    name=f"{self._name}-{worker_id}",
                    daemon=True,
                ).start()
            except BaseException:
                with self._lock:
                    self._worker_count -= 1
                    self._outstanding_work_count -= 1
                raise

        self._work_queue.put(_DaemonWorkItem(loop=loop, future=future, function=function))
        return await future

    @staticmethod
    def _deliver_result(future: asyncio.Future[Any], result: Any) -> None:
        if not future.done():
            future.set_result(result)

    @staticmethod
    def _deliver_exception(future: asyncio.Future[Any], exception: BaseException) -> None:
        if not future.done():
            future.set_exception(exception)

    def _worker(self) -> None:
        while True:
            item = self._work_queue.get()

            try:
                result = item.function()
            except BaseException as exc:  # noqa: BLE001 - delivered on the caller's loop
                callback = partial(self._deliver_exception, item.future, exc)
            else:
                callback = partial(self._deliver_result, item.future, result)

            with self._lock:
                self._outstanding_work_count -= 1
            with contextlib.suppress(RuntimeError):
                item.loop.call_soon_threadsafe(callback)


_icmp_worker_pool = _DaemonWorkerPool(
    max_workers=MAX_ICMP_DAEMON_THREADS,
    name="pinghue-icmp",
)


def _get_icmp_backend() -> _IcmpBackend:
    global _icmp_backend, _icmp_import_error

    if _icmp_backend is not None:
        return _icmp_backend
    if _icmp_import_error is not None:
        raise _icmp_import_error

    try:
        from icmplib.exceptions import (  # type: ignore[import-untyped]
            DestinationUnreachable,
            ICMPLibError,
            SocketPermissionError,
            TimeExceeded,
            TimeoutExceeded,
        )
        from icmplib.models import ICMPRequest  # type: ignore[import-untyped]
        from icmplib.sockets import (  # type: ignore[import-untyped]
            ICMPv4Socket,
            ICMPv6Socket,
        )
        from icmplib.utils import unique_identifier  # type: ignore[import-untyped]
    except ImportError as exc:
        _icmp_import_error = exc
        raise

    _icmp_backend = _IcmpBackend(
        socket_v4=ICMPv4Socket,
        socket_v6=ICMPv6Socket,
        request=ICMPRequest,
        unique_identifier=unique_identifier,
        timeout_error=TimeoutExceeded,
        permission_error=SocketPermissionError,
        unreachable_errors=(DestinationUnreachable, TimeExceeded),
        library_error=ICMPLibError,
    )
    return _icmp_backend


def _socket_family(address_family: AddressFamily) -> int:
    if address_family == AddressFamily.IPV4:
        return socket.AF_INET
    if address_family == AddressFamily.IPV6:
        return socket.AF_INET6
    return socket.AF_UNSPEC


def family_from_ip(address: str) -> AddressFamily:
    """Return pinghue's address family enum for an IP literal."""
    return AddressFamily.IPV6 if ipaddress.ip_address(address).version == 6 else AddressFamily.IPV4


# A timed-out getaddrinfo keeps its daemon thread, and that thread's slot, until
# libc returns. MAX_DNS_DAEMON_THREADS sits well above the runner's DNS
# concurrency so a few stuck names still leave threads for fresh lookups.
#
# A new lookup waits up to DNS_THREAD_WAIT_SECONDS for a free thread. The wait
# counts against the caller's lookup budget, so it stays short next to
# runner.DNS_LOOKUP_TIMEOUT_SECONDS.
DNS_THREAD_WAIT_SECONDS = 1.0
_DNS_THREAD_POLL_SECONDS = 0.05
# A successful answer that arrives after every caller gave up is kept for the
# next lookup of the same name; this outlives the runner's longest DNS backoff.
_DNS_LATE_ANSWER_MAX_AGE_SECONDS = 90.0


class ResolverBusyError(Exception):
    """No resolver thread came free in time, or none could start, so no lookup ran.

    This reflects local capacity, not a DNS verdict: callers keep the target's
    state and retry soon instead of reporting a DNS failure.
    """


_DnsKey = tuple[str, int]
_DnsWaiter = tuple[asyncio.AbstractEventLoop, asyncio.Future[list[Any]]]


@dataclass
class _DnsLookup:
    """One getaddrinfo call on a daemon thread, shared by callers of the same name."""

    slots: threading.BoundedSemaphore
    waiters: list[_DnsWaiter] = field(default_factory=list)


_dns_lock = threading.Lock()
_dns_lookups: dict[_DnsKey, _DnsLookup] = {}
_dns_late_answers: dict[_DnsKey, tuple[float, list[Any]]] = {}


def _deliver_dns_outcome(
    future: asyncio.Future[list[Any]],
    result: list[Any],
    error: BaseException | None,
) -> None:
    if future.done():
        return
    if error is None:
        future.set_result(result)
    else:
        future.set_exception(error)


def _settle_dns_lookup(
    key: _DnsKey,
    lookup: _DnsLookup,
    result: list[Any],
    error: BaseException | None,
) -> None:
    """Hand a finished lookup's outcome to its waiters and free its thread slot."""
    with _dns_lock:
        if _dns_lookups.get(key) is lookup:
            del _dns_lookups[key]
        waiters, lookup.waiters = lookup.waiters, []
        # Free the slot only once the lookup has left the registry, so whoever
        # takes the slot next can never join this finished call.
        lookup.slots.release()
    if waiters:
        for loop, future in waiters:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(_deliver_dns_outcome, future, result, error)
    elif error is None and result:
        # Every caller already gave up; keep the answer for the next lookup of
        # this name instead of paying for the slow resolution again. Only the
        # entries resolve_target would use are kept, so a huge answer cannot pin
        # memory; an answer with none keeps one entry to repeat its verdict.
        kept = _usable_addresses(result, prefer_ipv4=key[1] == socket.AF_UNSPEC) or result[:1]
        with _dns_lock:
            _dns_late_answers.pop(key, None)
            _dns_late_answers[key] = (time.monotonic(), kept)
            if len(_dns_late_answers) > MAX_DNS_DAEMON_THREADS:
                del _dns_late_answers[next(iter(_dns_late_answers))]


def _run_dns_lookup(key: _DnsKey, lookup: _DnsLookup, resolve: Callable[[], list[Any]]) -> None:
    try:
        result = resolve()
    except BaseException as exc:  # noqa: BLE001 - delivered to every waiter
        _settle_dns_lookup(key, lookup, [], exc)
    else:
        _settle_dns_lookup(key, lookup, result, None)


def _claim_dns_lookup(key: _DnsKey, waiter: _DnsWaiter) -> tuple[_DnsLookup, bool] | None:
    """Join the running lookup of a name, or register a new one if a slot is free.

    Returns the lookup and whether the caller must start its thread, or None
    when every resolver thread is taken. The caller holds _dns_lock.
    """
    lookup = _dns_lookups.get(key)
    start_thread = lookup is None
    if lookup is None:
        slots = _dns_thread_slots
        if not slots.acquire(blocking=False):
            return None
        lookup = _DnsLookup(slots=slots)
        _dns_lookups[key] = lookup
    lookup.waiters.append(waiter)
    return lookup, start_thread


async def _getaddrinfo_daemon(target: str, family: int) -> list[Any]:
    """Run getaddrinfo on a daemon thread.

    loop.getaddrinfo uses asyncio's default executor, whose non-daemon worker
    threads are joined without a bound at interpreter exit (and for up to
    THREAD_JOIN_TIMEOUT=300s by Runner.close), so a resolver call stuck past
    the libc timeouts would keep the finished process from exiting. A daemon
    thread is abandoned instead.

    A caller that stops waiting abandons only its wait: the thread keeps its
    slot until libc returns, so at most MAX_DNS_DAEMON_THREADS resolver threads
    ever exist. A lookup of a name whose previous call is still running joins
    that call instead of starting another thread. When every slot is taken, the
    lookup waits up to DNS_THREAD_WAIT_SECONDS for one, then raises
    ResolverBusyError; so does a thread that fails to start, once the rest of
    that wait has passed.
    """
    key = (target, family)
    loop = asyncio.get_running_loop()
    future: asyncio.Future[list[Any]] = loop.create_future()
    waiter = (loop, future)
    deadline = loop.time() + DNS_THREAD_WAIT_SECONDS
    while True:
        with _dns_lock:
            late_answer = _dns_late_answers.pop(key, None)
            if (
                late_answer is not None
                and time.monotonic() - late_answer[0] <= _DNS_LATE_ANSWER_MAX_AGE_SECONDS
            ):
                return late_answer[1]
            claimed = _claim_dns_lookup(key, waiter)
        if claimed is not None:
            break
        if loop.time() >= deadline:
            raise ResolverBusyError(f"all {MAX_DNS_DAEMON_THREADS} resolver threads are busy")
        await asyncio.sleep(_DNS_THREAD_POLL_SECONDS)

    lookup, start_thread = claimed
    if start_thread:
        resolve = partial(socket.getaddrinfo, target, None, family=family, type=socket.SOCK_STREAM)
        try:
            threading.Thread(
                target=_run_dns_lookup,
                args=(key, lookup, resolve),
                name="pinghue-dns",
                daemon=True,
            ).start()
        except BaseException as exc:
            with _dns_lock:
                lookup.waiters.remove(waiter)
            if not isinstance(exc, RuntimeError):
                _settle_dns_lookup(key, lookup, [], exc)
                raise
            # "can't start new thread" (a pids or RLIMIT_NPROC cap, memory) is
            # local capacity, not a DNS verdict. Free the slot, then wait out
            # DNS_THREAD_WAIT_SECONDS like a lookup that found every thread
            # busy: callers retry at once, so without the wait every pending
            # name would try to start a thread on every probe iteration.
            busy = ResolverBusyError(f"could not start a resolver thread: {exc}")
            _settle_dns_lookup(key, lookup, [], busy)
            await asyncio.sleep(max(0.0, deadline - loop.time()))
            raise busy from exc

    try:
        return await future
    except BaseException:
        # Leaving early (lookup budget, shutdown) only detaches this caller;
        # an answer that then has no one to receive it is kept for later.
        with _dns_lock, contextlib.suppress(ValueError):
            lookup.waiters.remove(waiter)
        raise


def _is_unspecified(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped.is_unspecified
    return ip.is_unspecified


UNSPECIFIED_ONLY_ERROR = (
    "getaddrinfo: only unspecified addresses returned (0.0.0.0 or ::); "
    "the name may be blocked by a DNS filter"
)


def _usable_addresses(infos: list[Any], *, prefer_ipv4: bool) -> list[Any]:
    """Return up to MAX_FAILOVER_ADDRESSES getaddrinfo entries, one per address, to probe.

    Keeps getaddrinfo order, with IPv4 first when preferred. Unspecified
    addresses (0.0.0.0, ::) are what DNS filters and sinkholes answer for
    blocked names; probing one reaches the local host on Linux and would report
    a dead name healthy, so they are dropped. One pass that stops filling each
    group at the cap keeps a huge answer from stalling the event loop.
    """
    seen: set[str] = set()
    preferred: list[Any] = []
    others: list[Any] = []
    for info in infos:
        address = info[4][0]
        group = preferred if not prefer_ipv4 or info[0] == socket.AF_INET else others
        if len(group) >= MAX_FAILOVER_ADDRESSES or address in seen:
            continue
        seen.add(address)
        if _is_unspecified(address):
            continue
        group.append(info)
        if len(preferred) >= MAX_FAILOVER_ADDRESSES:
            break
    return (preferred + others)[:MAX_FAILOVER_ADDRESSES]


async def resolve_target(
    target: str,
    address_family: AddressFamily,
    *,
    numeric: bool = False,
) -> ResolvedTarget:
    """Resolve a target to the address used for probing.

    Raises ResolverBusyError, rather than returning a DNS error, when no
    resolver thread was available and so no lookup ran.
    """
    if numeric:
        try:
            return ResolvedTarget(target, target, family_from_ip(target), addresses=(target,))
        except ValueError:
            return ResolvedTarget(target, None, None, "--numeric requires an IP literal")

    try:
        literal_family = family_from_ip(target)
    except ValueError:
        literal_family = None

    if literal_family is not None:
        if address_family != AddressFamily.AUTO and literal_family != address_family:
            return ResolvedTarget(target, None, None, f"target is not {address_family.value}")
        return ResolvedTarget(target, target, literal_family, addresses=(target,))

    try:
        infos = await _getaddrinfo_daemon(target, _socket_family(address_family))
    except socket.gaierror as exc:
        return ResolvedTarget(target, None, None, f"getaddrinfo: {exc.strerror or exc}")
    except OSError as exc:
        return ResolvedTarget(target, None, None, f"getaddrinfo: {exc}")

    if not infos:
        return ResolvedTarget(target, None, None, "getaddrinfo: no addresses returned")

    # Prefer IPv4 in auto mode, then IPv6. Cap the failover set so a name
    # resolving to many dead addresses cannot stretch one probe cycle to
    # len(addresses) * timeout_s.
    usable = _usable_addresses(infos, prefer_ipv4=address_family == AddressFamily.AUTO)
    if not usable:
        return ResolvedTarget(target, None, None, UNSPECIFIED_ONLY_ERROR)

    family = AddressFamily.IPV4 if usable[0][0] == socket.AF_INET else AddressFamily.IPV6
    addresses = tuple(info[4][0] for info in usable)
    return ResolvedTarget(target, addresses[0], family, addresses=addresses)


async def tcp_probe(address: str, port: int, *, timeout_s: float) -> ProbeSample:
    """Probe a TCP port with asyncio.open_connection."""
    timestamp = datetime.now(timezone.utc)
    start = time.perf_counter()
    writer: asyncio.StreamWriter | None = None

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(address, port),
            timeout=timeout_s,
        )
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
    except asyncio.TimeoutError:
        return ProbeSample(timestamp=timestamp, latency_ms=None, status=SampleStatus.TIMEOUT)
    except ConnectionRefusedError as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.REFUSED,
            error=str(exc) or "connection refused",
        )
    except OSError as exc:
        status = SampleStatus.UNREACHABLE if exc.errno in UNREACHABLE_ERRNOS else SampleStatus.ERROR
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=status,
            error=str(exc),
        )
    finally:
        if writer is not None:
            writer.close()
            # Once connect() succeeds, teardown is no longer part of the probe
            # result. Preserve the measured success when shutdown cancels a
            # slow wait_closed(); cancellation before connect still propagates.
            with contextlib.suppress(OSError, asyncio.CancelledError):
                await writer.wait_closed()

    return ProbeSample(timestamp=timestamp, latency_ms=latency_ms, status=SampleStatus.OK)


def _run_icmp_echo(
    backend: _IcmpBackend,
    address: str,
    *,
    timeout_s: float,
    ipv6: bool,
) -> float:
    """Send one ICMP echo and return the round-trip time in ms (blocking).

    Raises icmplib exceptions: TimeoutExceeded on no reply,
    DestinationUnreachable/TimeExceeded when the network reports the destination
    cannot be reached, and SocketPermissionError/ICMPLibError on socket errors.
    """
    socket_factory = backend.socket_v6 if ipv6 else backend.socket_v4
    with socket_factory(None, False) as sock:
        request = backend.request(
            destination=address,
            id=backend.unique_identifier(),
            sequence=0,
        )
        sock.send(request)
        reply = sock.receive(request, timeout_s)
        reply.raise_for_status()
        return round(float(reply.time - request.time) * 1000, 2)


async def icmp_probe(
    address: str,
    *,
    timeout_s: float,
    address_family: AddressFamily,
) -> ProbeSample:
    """Probe a target with a single ICMP echo via icmplib's low-level sockets.

    Reading the reply directly (rather than icmplib.ping, which swallows error
    replies) lets us tell an unreachable destination apart from a timeout.
    """
    timestamp = datetime.now(timezone.utc)
    try:
        backend = _get_icmp_backend()
    except ImportError as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=str(exc),
        )

    try:
        ipv6 = ipaddress.ip_address(address).version == 6
    except ValueError:
        ipv6 = address_family == AddressFamily.IPV6

    run_echo = partial(
        _run_icmp_echo,
        backend,
        address,
        timeout_s=timeout_s,
        ipv6=ipv6,
    )
    try:
        latency_ms = await _icmp_worker_pool.run(run_echo)
    except backend.timeout_error:
        return ProbeSample(timestamp=timestamp, latency_ms=None, status=SampleStatus.TIMEOUT)
    except backend.unreachable_errors as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.UNREACHABLE,
            error=str(exc),
        )
    except backend.permission_error as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=f"permission denied: {exc}",
        )
    except backend.library_error as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=str(exc),
        )
    except OSError as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=str(exc),
        )

    return ProbeSample(timestamp=timestamp, latency_ms=latency_ms, status=SampleStatus.OK)
