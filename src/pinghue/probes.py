"""Async probe implementations."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from pinghue.models import AddressFamily, ProbeSample, SampleStatus


@dataclass(frozen=True)
class ResolvedTarget:
    target: str
    address: str | None
    family: AddressFamily | None
    error: str | None = None


def _socket_family(address_family: AddressFamily) -> int:
    if address_family == AddressFamily.IPV4:
        return socket.AF_INET
    if address_family == AddressFamily.IPV6:
        return socket.AF_INET6
    return socket.AF_UNSPEC


def _family_from_ip(address: str) -> AddressFamily:
    return AddressFamily.IPV6 if ipaddress.ip_address(address).version == 6 else AddressFamily.IPV4


async def resolve_target(
    target: str,
    address_family: AddressFamily,
    *,
    numeric: bool = False,
) -> ResolvedTarget:
    """Resolve a target to the address used for probing."""
    if numeric:
        try:
            return ResolvedTarget(target, target, _family_from_ip(target))
        except ValueError:
            return ResolvedTarget(target, None, None, "--numeric requires an IP literal")

    try:
        literal_family = _family_from_ip(target)
    except ValueError:
        literal_family = None

    if literal_family is not None:
        if address_family != AddressFamily.AUTO and literal_family != address_family:
            return ResolvedTarget(target, None, None, f"target is not {address_family.value}")
        return ResolvedTarget(target, target, literal_family)

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

    family = AddressFamily.IPV4 if infos[0][0] == socket.AF_INET else AddressFamily.IPV6
    return ResolvedTarget(target, infos[0][4][0], family)


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
            if exc.errno in {socket.EAI_NONAME, 64, 65, 101, 113}
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
) -> ProbeSample:
    """Probe a target with icmplib."""
    timestamp = datetime.now(timezone.utc)
    try:
        from icmplib import ping  # type: ignore[import-untyped]
        from icmplib.exceptions import (  # type: ignore[import-untyped]
            ICMPLibError,
            NameLookupError,
            SocketPermissionError,
            TimeoutExceeded,
        )
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

    try:
        result = await asyncio.to_thread(
            ping,
            address,
            count=1,
            interval=0,
            timeout=timeout_s,
            family=family,
            privileged=False,
        )
    except TimeoutExceeded:
        return ProbeSample(timestamp=timestamp, latency_ms=None, status=SampleStatus.TIMEOUT)
    except SocketPermissionError as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=f"permission denied: {exc}",
        )
    except NameLookupError as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=str(exc),
        )
    except ICMPLibError as exc:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.UNREACHABLE,
            error=str(exc),
        )

    if not result.is_alive:
        return ProbeSample(timestamp=timestamp, latency_ms=None, status=SampleStatus.TIMEOUT)

    return ProbeSample(
        timestamp=timestamp,
        latency_ms=round(float(result.avg_rtt), 2),
        status=SampleStatus.OK,
    )
