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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Any, TypeVar

from pinghue.icmp import UNREACHABLE_ERRNOS as UNREACHABLE_ERRNOS
from pinghue.icmp import engine_for_running_loop
from pinghue.models import AddressFamily, ProbeSample, SampleStatus

MAX_FAILOVER_ADDRESSES = 8
MAX_DNS_DAEMON_THREADS = 128
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
    ResolverBusyError; so does a thread that fails to start.
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
            # local capacity, not a DNS verdict.
            busy = ResolverBusyError(f"could not start a resolver thread: {exc}")
            _settle_dns_lookup(key, lookup, [], busy)
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
        return ResolvedTarget(
            target,
            None,
            None,
            "getaddrinfo: only unspecified addresses returned (0.0.0.0 or ::); "
            "the name may be blocked by a DNS filter",
        )

    family = AddressFamily.IPV4 if usable[0][0] == socket.AF_INET else AddressFamily.IPV6
    addresses = tuple(info[4][0] for info in usable)
    return ResolvedTarget(target, addresses[0], family, addresses=addresses)


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
