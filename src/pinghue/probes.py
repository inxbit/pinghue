"""Async probe implementations."""

from __future__ import annotations

import asyncio
import errno
import ipaddress
import socket
import time
from collections.abc import Callable
from concurrent.futures import Executor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any

from pinghue.models import AddressFamily, ProbeSample, SampleStatus

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
    ping: Callable[..., Any]
    timeout_error: type[BaseException]
    permission_error: type[BaseException]
    name_lookup_error: type[BaseException]
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
        from icmplib import ping  # type: ignore[import-untyped]
        from icmplib.exceptions import (  # type: ignore[import-untyped]
            ICMPLibError,
            NameLookupError,
            SocketPermissionError,
            TimeoutExceeded,
        )
    except ImportError as exc:
        _icmp_import_error = exc
        raise

    _icmp_backend = _IcmpBackend(
        ping=ping,
        timeout_error=TimeoutExceeded,
        permission_error=SocketPermissionError,
        name_lookup_error=NameLookupError,
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

    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            target,
            None,
            family=_socket_family(address_family),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        return ResolvedTarget(target, None, None, f"getaddrinfo: {exc.strerror or exc}")
    except OSError as exc:
        return ResolvedTarget(target, None, None, f"getaddrinfo: {exc}")

    # Prefer IPv4 in auto mode, then IPv6.
    if address_family == AddressFamily.AUTO:
        infos = sorted(infos, key=lambda item: 0 if item[0] == socket.AF_INET else 1)

    addresses: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in addresses:
            addresses.append(address)

    family = AddressFamily.IPV4 if infos[0][0] == socket.AF_INET else AddressFamily.IPV6
    return ResolvedTarget(target, addresses[0], family, addresses=tuple(addresses))


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


async def icmp_probe(
    address: str,
    *,
    timeout_s: float,
    address_family: AddressFamily,
    executor: Executor | None = None,
) -> ProbeSample:
    """Probe a target with icmplib."""
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

    family = None
    if address_family == AddressFamily.IPV4:
        family = 4
    elif address_family == AddressFamily.IPV6:
        family = 6

    loop = asyncio.get_running_loop()
    try:
        result: Any = await loop.run_in_executor(
            executor,
            partial(
                backend.ping,
                address,
                count=1,
                interval=0,
                timeout=timeout_s,
                family=family,
                privileged=False,
            ),
        )
    except Exception as exc:
        if isinstance(exc, backend.timeout_error):
            return ProbeSample(
                timestamp=timestamp,
                latency_ms=None,
                status=SampleStatus.TIMEOUT,
            )
        if isinstance(exc, backend.permission_error):
            return ProbeSample(
                timestamp=timestamp,
                latency_ms=None,
                status=SampleStatus.ERROR,
                error=f"permission denied: {exc}",
            )
        if isinstance(exc, backend.name_lookup_error):
            return ProbeSample(
                timestamp=timestamp,
                latency_ms=None,
                status=SampleStatus.ERROR,
                error=str(exc),
            )
        if isinstance(exc, backend.library_error):
            return ProbeSample(
                timestamp=timestamp,
                latency_ms=None,
                status=SampleStatus.UNREACHABLE,
                error=str(exc),
            )
        raise

    if not result.is_alive:
        return ProbeSample(timestamp=timestamp, latency_ms=None, status=SampleStatus.TIMEOUT)

    return ProbeSample(
        timestamp=timestamp,
        latency_ms=round(float(result.avg_rtt), 2),
        status=SampleStatus.OK,
    )
