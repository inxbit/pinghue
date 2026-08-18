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
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, TypeVar

from pinghue.models import AddressFamily, ProbeSample, SampleStatus

MAX_FAILOVER_ADDRESSES = 8
MAX_DNS_DAEMON_THREADS = 16
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


async def _run_daemon_thread(
    function: Callable[[], _T],
    *,
    name: str,
    slots: threading.BoundedSemaphore,
    unavailable_error: str,
) -> _T:
    """Run blocking work on a bounded daemon thread and await its result."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[_T] = loop.create_future()
    if not slots.acquire(blocking=False):
        raise OSError(unavailable_error)

    def deliver(apply: Callable[[], None]) -> None:
        if not future.done():
            apply()

    def worker() -> None:
        try:
            result = function()
        except BaseException as exc:  # noqa: BLE001 - re-raised via the future
            outcome = partial(future.set_exception, exc)
        else:
            outcome = partial(future.set_result, result)
        finally:
            slots.release()
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, outcome)

    try:
        threading.Thread(target=worker, name=name, daemon=True).start()
    except BaseException:
        slots.release()
        raise
    return await future


async def _getaddrinfo_daemon(target: str, family: int) -> list[Any]:
    """Run getaddrinfo on a daemon thread.

    loop.getaddrinfo uses asyncio's default executor, whose non-daemon worker
    threads are joined without a bound at interpreter exit (and for up to
    THREAD_JOIN_TIMEOUT=300s by Runner.close), so a resolver call stuck past
    the libc timeouts would keep the finished process from exiting. A daemon
    thread is abandoned instead.
    """
    return await _run_daemon_thread(
        partial(socket.getaddrinfo, target, None, family=family, type=socket.SOCK_STREAM),
        name="pinghue-dns",
        slots=_dns_thread_slots,
        unavailable_error=(f"resolver worker limit reached ({MAX_DNS_DAEMON_THREADS} in flight)"),
    )


async def resolve_target(
    target: str,
    address_family: AddressFamily,
    *,
    numeric: bool = False,
) -> ResolvedTarget:
    """Resolve a target to the address used for probing."""
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

    # Prefer IPv4 in auto mode, then IPv6.
    if address_family == AddressFamily.AUTO:
        infos = sorted(infos, key=lambda item: 0 if item[0] == socket.AF_INET else 1)

    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)

    family = AddressFamily.IPV4 if infos[0][0] == socket.AF_INET else AddressFamily.IPV6
    # Cap the failover set so a name resolving to many dead addresses cannot
    # stretch one probe cycle to len(addresses) * timeout_s.
    return ResolvedTarget(
        target, addresses[0], family, addresses=tuple(addresses[:MAX_FAILOVER_ADDRESSES])
    )


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
