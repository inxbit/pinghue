"""Async probe implementations."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial
from typing import Any, TypeVar

from pinghue.icmp import UNREACHABLE_ERRNOS as UNREACHABLE_ERRNOS
from pinghue.icmp import engine_for_running_loop
from pinghue.models import AddressFamily, ProbeSample, SampleStatus

MAX_FAILOVER_ADDRESSES = 8
MAX_DNS_DAEMON_THREADS = 16
_dns_thread_slots = threading.BoundedSemaphore(MAX_DNS_DAEMON_THREADS)
_T = TypeVar("_T")

# A clean TCP close finishes on the next loop iteration; this only bounds a
# teardown that stalls after the connect was already measured.
TCP_CLOSE_TIMEOUT_SECONDS = 1.0
# Linux struct tcp_info: tcpi_rtt (smoothed RTT, microseconds) at offset 68 and
# tcpi_total_retrans at offset 100.
_TCP_INFO = getattr(socket, "TCP_INFO", None) if sys.platform.startswith("linux") else None
_TCP_INFO_SIZE = 104
_TCP_INFO_RTT_OFFSET = 68
_TCP_INFO_TOTAL_RETRANS_OFFSET = 100


@dataclass(frozen=True)
class ResolvedTarget:
    target: str
    address: str | None
    family: AddressFamily | None
    error: str | None = None
    addresses: tuple[str, ...] = ()


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


def _kernel_connect_rtt_ms(writer: asyncio.StreamWriter) -> float | None:
    """Return the kernel's handshake RTT when it covers the whole connect.

    tcpi_rtt only reflects the last SYN exchange, so after a SYN retransmission
    it would hide the time spent waiting; None keeps the measured connect time.
    """
    if _TCP_INFO is None:
        return None
    try:
        sock = writer.get_extra_info("socket")
        if sock is None:
            return None
        info = sock.getsockopt(socket.IPPROTO_TCP, _TCP_INFO, _TCP_INFO_SIZE)
        (rtt_us,) = struct.unpack_from("=I", info, _TCP_INFO_RTT_OFFSET)
        (total_retrans,) = struct.unpack_from("=I", info, _TCP_INFO_TOTAL_RETRANS_OFFSET)
    except (AttributeError, OSError, TypeError, struct.error):
        return None
    if total_retrans or rtt_us <= 0:
        return None
    return float(rtt_us) / 1000


async def tcp_probe(address: str, port: int, *, timeout_s: float) -> ProbeSample:
    """Probe a TCP port with asyncio.open_connection.

    The connect time is measured on the event loop, so it also counts any
    time the loop was busy elsewhere. On Linux the kernel's handshake RTT
    replaces it when that is smaller and no SYN was retransmitted.
    """
    timestamp = datetime.now(timezone.utc)
    start = time.perf_counter()
    writer: asyncio.StreamWriter | None = None

    try:
        _, connected = await asyncio.wait_for(
            asyncio.open_connection(address, port),
            timeout=timeout_s,
        )
        writer = connected
        latency_ms = (time.perf_counter() - start) * 1000
        kernel_rtt_ms = _kernel_connect_rtt_ms(connected)
        if kernel_rtt_ms is not None:
            latency_ms = min(latency_ms, kernel_rtt_ms)
        latency_ms = round(latency_ms, 2)
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
            # result. Preserve the measured success when shutdown cancels or a
            # slow wait_closed() outlasts the bound; cancellation before connect
            # still propagates.
            with contextlib.suppress(OSError, asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), timeout=TCP_CLOSE_TIMEOUT_SECONDS)

    return ProbeSample(timestamp=timestamp, latency_ms=latency_ms, status=SampleStatus.OK)


async def icmp_probe(
    address: str,
    *,
    timeout_s: float,
    address_family: AddressFamily,  # noqa: ARG001 - the IP literal decides the family
) -> ProbeSample:
    """Probe an IP address with one ICMP echo through the loop's ICMP engine.

    The engine reports ICMP destination-unreachable and time-exceeded errors
    that quote the probe as unreachable, apart from a timeout.
    """
    timestamp = datetime.now(timezone.utc)
    try:
        ip = ipaddress.ip_address(address.partition("%")[0])
    except ValueError:
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=f"ICMP probes need an IP address, not {address!r}",
        )

    ipv6 = ip.version == 6
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # An IPv4-mapped address names an IPv4 host; ICMPv6 cannot reach it.
        address = str(ip.ipv4_mapped)
        ipv6 = False

    try:
        engine = engine_for_running_loop()
    except Exception as exc:  # noqa: BLE001 - report instead of failing the probe loop
        return ProbeSample(
            timestamp=timestamp,
            latency_ms=None,
            status=SampleStatus.ERROR,
            error=f"ICMP engine unavailable: {exc}",
        )

    result = await engine.echo(address, ipv6=ipv6, timeout_s=timeout_s)
    return ProbeSample(
        timestamp=timestamp,
        latency_ms=result.latency_ms,
        status=result.status,
        error=result.error,
    )
