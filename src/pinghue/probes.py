"""Async probe implementations."""

from __future__ import annotations

import asyncio
import contextlib
import errno
import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any

from pinghue.models import AddressFamily, ProbeSample, SampleStatus

MAX_FAILOVER_ADDRESSES = 8
MAX_DNS_DAEMON_THREADS = 16
_dns_thread_slots = threading.BoundedSemaphore(MAX_DNS_DAEMON_THREADS)

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


def _family_from_ip(address: str) -> AddressFamily:
    return AddressFamily.IPV6 if ipaddress.ip_address(address).version == 6 else AddressFamily.IPV4


def family_from_ip(address: str) -> AddressFamily:
    """Return pinghue's address family enum for an IP literal."""
    return _family_from_ip(address)


async def _getaddrinfo_daemon(target: str, family: int) -> list[Any]:
    """Run getaddrinfo on a daemon thread.

    loop.getaddrinfo uses asyncio's default executor, whose non-daemon worker
    threads are joined without a bound at interpreter exit (and for up to
    THREAD_JOIN_TIMEOUT=300s by Runner.close), so a resolver call stuck past
    the libc timeouts would keep the finished process from exiting. A daemon
    thread is abandoned instead.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[list[Any]] = loop.create_future()
    if not _dns_thread_slots.acquire(blocking=False):
        raise OSError(
            f"resolver worker limit reached ({MAX_DNS_DAEMON_THREADS} in flight)"
        )

    def deliver(apply: Callable[[], None]) -> None:
        if not future.done():
            apply()

    def worker() -> None:
        try:
            result = socket.getaddrinfo(target, None, family=family, type=socket.SOCK_STREAM)
        except BaseException as exc:  # noqa: BLE001 - re-raised via the future
            outcome = partial(future.set_exception, exc)
        else:
            outcome = partial(future.set_result, result)
        finally:
            _dns_thread_slots.release()
        # RuntimeError: loop already closed; the caller has moved on.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, outcome)

    threading.Thread(target=worker, name="pinghue-dns", daemon=True).start()
    return await future


async def resolve_target(
    target: str,
    address_family: AddressFamily,
    *,
    numeric: bool = False,
) -> ResolvedTarget:
    """Resolve a target to the address used for probing."""
    if numeric:
        try:
            return ResolvedTarget(target, target, _family_from_ip(target), addresses=(target,))
        except ValueError:
            return ResolvedTarget(target, None, None, "--numeric requires an IP literal")

    try:
        literal_family = _family_from_ip(target)
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
        status = (
            SampleStatus.UNREACHABLE
            if exc.errno in UNREACHABLE_ERRNOS
            else SampleStatus.ERROR
        )
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=status,
            error=str(exc),
        )
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
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
    executor: Executor | None = None,
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

    loop = asyncio.get_running_loop()
    try:
        latency_ms = await loop.run_in_executor(
            executor,
            partial(_run_icmp_echo, backend, address, timeout_s=timeout_s, ipv6=ipv6),
        )
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
