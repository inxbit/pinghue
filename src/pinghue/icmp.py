"""Event-loop ICMP echo engine.

Each running event loop gets one engine, created on first use and released
with the loop. The engine owns a few non-blocking, unprivileged datagram ICMP
sockets per address family and matches replies to in-flight probes from a
loop reader callback, so a probe costs no thread and no file descriptor of its
own. Timeouts run on loop timers.

Platform notes:

- Linux rewrites the echo identifier to the socket's local port and delivers
  each socket only its own replies. ICMP errors (destination unreachable, time
  exceeded) reach a datagram ICMP socket only through its error queue, and
  only with IP_RECVERR / IPV6_RECVERR set.
- macOS keeps the identifier, delivers every echo reply and ICMP error to
  every ICMP socket on the host, and includes the IPv4 header in what an IPv4
  socket reads (IPv6 sockets read from the ICMPv6 header).

Replies are therefore matched on the sequence number, the identifier where the
kernel does not rewrite it, the source address, and a random per-probe token
echoed back in the payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import platform
import socket
import struct
import sys
import time
import weakref
from collections.abc import Callable
from typing import Any, NamedTuple

from pinghue.models import SampleStatus

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

_LINUX = sys.platform.startswith("linux")

ICMP_ECHO_REPLY = 0
ICMP_DEST_UNREACH = 3
ICMP_ECHO_REQUEST = 8
ICMP_TIME_EXCEEDED = 11
ICMP_PARAMETER_PROBLEM = 12
ICMPV6_DEST_UNREACH = 1
ICMPV6_TIME_EXCEEDED = 3
ICMPV6_PARAMETER_PROBLEM = 4
ICMPV6_ECHO_REQUEST = 128
ICMPV6_ECHO_REPLY = 129

TOKEN_SIZE = 16
# 16 random token bytes plus padding give the classic 56-byte ping payload.
_PAYLOAD_PADDING = bytes(40)
_ECHO_HEADER = struct.Struct("!BBHHH")
_ECHO_IDENT_SEQUENCE = struct.Struct("!HH")
_IPPROTO_ICMP = 1
_IPPROTO_ICMPV6 = 58
_IPV4_MAPPED_PREFIX = bytes(10) + b"\xff\xff"

# Large enough for an echo reply, or a macOS ICMP error quoting the probe,
# with full IPv4 headers and options.
_RECEIVE_SIZE = 512
_ERROR_QUEUE_ANCILLARY_SIZE = 512
# Datagrams handled per reader wakeup before yielding to other loop work.
_READ_BUDGET = 256

# A shared socket queues every reply that lands while the loop is busy, so
# keep each socket's in-flight probes well inside its receive buffer (a small
# echo reply costs about 1 KiB of buffer space on Linux) and spread the rest
# over more sockets. Each socket also has its own 16-bit sequence space.
_RECEIVE_BUFFER_SIZES = (4 << 20, 1 << 20, 256 << 10)
_RECEIVE_BUFFER_BYTES_PER_PROBE = 2048
_MIN_PROBES_PER_SOCKET = 32
_MAX_PROBES_PER_SOCKET = 512
_MAX_SOCKETS_PER_FAMILY = 16
# A socket holds at most _MAX_PROBES_PER_SOCKET of the 65,536 sequence numbers,
# so a random draw almost always finds a free one; a scan backs it up.
_RANDOM_SEQUENCE_ATTEMPTS = 16

# Longer waits are cut to this (about 68 years), far beyond any useful timeout.
_MAX_ECHO_TIMEOUT_SECONDS = float(2**31)
_SEND_RETRY_DELAY_SECONDS = 0.001
_SEND_RETRY_MAX_DELAY_SECONDS = 0.05
# Bounds the immediate retries of a send that failed while ICMP errors for
# other probes kept arriving on its socket (see IcmpEngine._send).
_SEND_ATTEMPTS = 8
_SEND_BACKOFF_ERRNOS = {
    value
    for value in (errno.EAGAIN, errno.EWOULDBLOCK, getattr(errno, "ENOBUFS", None))
    if value is not None
}

# Linux socket API values that the socket module does not export everywhere.
_IP_RECVERR = 11
_IPV6_RECVERR = 25
_SO_TIMESTAMPNS = 35  # SO_TIMESTAMPNS_OLD on the asm-generic architectures below.
_SO_EE_ORIGIN_LOCAL = 1
_SO_EE_ORIGIN_ICMP = 2
_SO_EE_ORIGIN_ICMP6 = 3
_MSG_ERRQUEUE = getattr(socket, "MSG_ERRQUEUE", 0x2000)
_TIMESPEC = struct.Struct("@ll")
_SOCK_EXTENDED_ERR = struct.Struct("=IBBBBII")
_RECVERR_CMSG_TYPES = {
    (socket.IPPROTO_IP, _IP_RECVERR),
    (socket.IPPROTO_IPV6, _IPV6_RECVERR),
}
_KERNEL_TIMESTAMP_MACHINES = frozenset(
    {
        "x86_64",
        "amd64",
        "i386",
        "i686",
        "aarch64",
        "arm64",
        "armv7l",
        "armv6l",
        "riscv64",
        "s390x",
        "ppc64le",
        "ppc64",
    }
)
_KERNEL_TIMESTAMPS = _LINUX and platform.machine().lower() in _KERNEL_TIMESTAMP_MACHINES
_TIMESTAMP_ANCILLARY_SIZE = (
    socket.CMSG_SPACE(_TIMESPEC.size) if hasattr(socket, "CMSG_SPACE") else 64
)
# A kernel receive timestamp is used only when it agrees with the monotonic
# round trip: it must not be negative or exceed the monotonic value by more
# than this, so a wall-clock step falls back to the monotonic measurement.
_KERNEL_TIMESTAMP_SLACK_NS = 1_000_000

_ICMP_UNREACHABLE_MESSAGES = {
    0: "Destination network unreachable",
    1: "Destination host unreachable",
    2: "Destination protocol unreachable",
    3: "Destination port unreachable",
    4: "Fragmentation needed and DF set",
    5: "Source route failed",
    6: "Destination network unknown",
    7: "Destination host unknown",
    9: "Destination network administratively prohibited",
    10: "Destination host administratively prohibited",
    11: "Destination network unreachable for type of service",
    12: "Destination host unreachable for type of service",
    13: "Communication administratively prohibited",
}
_ICMPV6_UNREACHABLE_MESSAGES = {
    0: "No route to destination",
    1: "Communication with destination administratively prohibited",
    2: "Beyond scope of source address",
    3: "Address unreachable",
    4: "Port unreachable",
    5: "Source address failed ingress/egress policy",
    6: "Reject route to destination",
}

SocketFactory = Callable[[int, int, int], Any]


class EchoResult(NamedTuple):
    status: SampleStatus
    latency_ms: float | None = None
    error: str | None = None


def icmp_checksum(data: bytes) -> int:
    """Return the RFC 1071 Internet checksum of data."""
    if len(data) % 2:
        data += b"\x00"
    total: int = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def echo_request(*, ipv6: bool, ident: int, sequence: int, token: bytes) -> bytes:
    """Build an ICMP or ICMPv6 echo request carrying token in its payload."""
    payload = token + _PAYLOAD_PADDING
    if ipv6:
        # The kernel fills in the ICMPv6 checksum, which covers a pseudo-header.
        return _ECHO_HEADER.pack(ICMPV6_ECHO_REQUEST, 0, 0, ident, sequence) + payload
    checksum = icmp_checksum(_ECHO_HEADER.pack(ICMP_ECHO_REQUEST, 0, 0, ident, sequence) + payload)
    return _ECHO_HEADER.pack(ICMP_ECHO_REQUEST, 0, checksum, ident, sequence) + payload


def packed_address(address: str) -> bytes:
    """Return an IP address as packed bytes for comparison.

    The IPv6 scope is dropped and an IPv4-mapped IPv6 address is reduced to
    its IPv4 form, so differently written forms of one address compare equal.
    """
    host = address.partition("%")[0]
    if ":" not in host:
        return socket.inet_pton(socket.AF_INET, host)
    packed = socket.inet_pton(socket.AF_INET6, host)
    return packed[12:] if packed[:12] == _IPV4_MAPPED_PREFIX else packed


def _icmp_error_outcome(*, ipv6: bool, icmp_type: int, code: int) -> EchoResult | None:
    """Map an ICMP error message to a probe result, or None to keep waiting."""
    if ipv6:
        if icmp_type == ICMPV6_DEST_UNREACH:
            message = _ICMPV6_UNREACHABLE_MESSAGES.get(
                code, f"Destination unreachable (code {code})"
            )
            return EchoResult(SampleStatus.UNREACHABLE, error=message)
        if icmp_type == ICMPV6_TIME_EXCEEDED:
            message = "Hop limit exceeded in transit"
            if code != 0:
                message = "Fragment reassembly time exceeded"
            return EchoResult(SampleStatus.UNREACHABLE, error=message)
        if icmp_type == ICMPV6_PARAMETER_PROBLEM:
            return EchoResult(SampleStatus.ERROR, error=f"Parameter problem (code {code})")
        return None

    if icmp_type == ICMP_DEST_UNREACH:
        message = _ICMP_UNREACHABLE_MESSAGES.get(code, f"Destination unreachable (code {code})")
        return EchoResult(SampleStatus.UNREACHABLE, error=message)
    if icmp_type == ICMP_TIME_EXCEEDED:
        message = "Time to live exceeded" if code == 0 else "Fragment reassembly time exceeded"
        return EchoResult(SampleStatus.UNREACHABLE, error=message)
    if icmp_type == ICMP_PARAMETER_PROBLEM:
        return EchoResult(SampleStatus.ERROR, error=f"Parameter problem (code {code})")
    return None


def _errno_outcome(code: int) -> EchoResult:
    status = SampleStatus.UNREACHABLE if code in UNREACHABLE_ERRNOS else SampleStatus.ERROR
    return EchoResult(status, error=os.strerror(code))


def _quoted_echo(*, ipv6: bool, quote: bytes) -> tuple[bytes, bytes] | None:
    """Return (destination, echo header + payload) quoted by an ICMP error."""
    if ipv6:
        if len(quote) < 48 or quote[6] != _IPPROTO_ICMPV6:
            return None
        destination = quote[24:40]
        echo = quote[40:]
        request_type = ICMPV6_ECHO_REQUEST
    else:
        if len(quote) < 20 or quote[0] >> 4 != 4 or quote[9] != _IPPROTO_ICMP:
            return None
        destination = quote[16:20]
        echo = quote[(quote[0] & 0x0F) * 4 :]
        request_type = ICMP_ECHO_REQUEST
    if len(echo) < _ECHO_HEADER.size or echo[0] != request_type:
        return None
    if destination[:12] == _IPV4_MAPPED_PREFIX:
        destination = destination[12:]
    return destination, echo


def _kernel_receive_time_ns(ancdata: list[tuple[int, int, bytes]]) -> int | None:
    for level, kind, payload in ancdata:
        if (level, kind) == (socket.SOL_SOCKET, _SO_TIMESTAMPNS) and len(payload) >= _TIMESPEC.size:
            seconds, nanoseconds = _TIMESPEC.unpack_from(payload)
            return int(seconds) * 1_000_000_000 + int(nanoseconds)
    return None


def _sockaddr_host(raw: bytes) -> str | None:
    """Return the address in a raw struct sockaddr_in / sockaddr_in6."""
    if len(raw) < 2:
        return None
    family = int.from_bytes(raw[:2], sys.byteorder)
    if family == socket.AF_INET and len(raw) >= 8:
        return socket.inet_ntop(socket.AF_INET, raw[4:8])
    if family == socket.AF_INET6 and len(raw) >= 24:
        return socket.inet_ntop(socket.AF_INET6, raw[8:24])
    return None


def _socket_permission_error(exc: OSError) -> str:
    reason = exc.strerror or str(exc)
    return (
        f"permission denied: unprivileged ICMP sockets are not allowed for this user "
        f"({reason}); run pinghue --check"
    )


def _send_failure(exc: OSError, destination: str) -> EchoResult:
    if exc.errno == errno.EPERM:
        return EchoResult(
            SampleStatus.ERROR,
            error=(
                "permission denied: ICMP send blocked by local policy (EPERM, e.g. a firewall rule)"
            ),
        )
    if exc.errno == errno.EACCES:
        if destination == "255.255.255.255":
            reason = "EACCES, broadcast address"
        else:
            reason = "EACCES, e.g. a broadcast address or a prohibit route"
        return EchoResult(
            SampleStatus.ERROR,
            error=f"permission denied: ICMP send blocked by local policy ({reason})",
        )
    if exc.errno in UNREACHABLE_ERRNOS:
        return EchoResult(SampleStatus.UNREACHABLE, error=str(exc))
    return EchoResult(SampleStatus.ERROR, error=str(exc))


def _receive_buffer_size(sock: Any) -> int:
    for size in _RECEIVE_BUFFER_SIZES:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, size)
        except OSError:
            continue
        break
    try:
        return int(sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF))
    except (OSError, TypeError, ValueError):
        return 0


class _IcmpSocket:
    """One shared ICMP datagram socket and the probes in flight on it."""

    __slots__ = (
        "capacity",
        "fd",
        "ident",
        "ipv6",
        "pending",
        "recverr",
        "sock",
        "timestamps",
    )

    def __init__(self, sock: Any, *, ipv6: bool, capacity: int) -> None:
        self.sock = sock
        self.fd: int = sock.fileno()
        self.ipv6 = ipv6
        self.capacity = capacity
        # Linux replaces the identifier with the socket's port; elsewhere it is
        # the only thing that tells this socket's replies from other sockets'.
        self.ident = int.from_bytes(os.urandom(2), "big")
        self.pending: dict[int, _PendingEcho] = {}
        self.recverr = False
        self.timestamps = False

    def next_sequence(self) -> int:
        """Return a random sequence number that no in-flight probe uses.

        Random rather than counting up: every target sees the sequence numbers
        of its own probes, and must not learn from them those of the probes in
        flight to other targets on this socket, which an ICMP error with a
        short quote is matched on (see IcmpEngine._match).
        """
        for _ in range(_RANDOM_SEQUENCE_ATTEMPTS):
            sequence = int.from_bytes(os.urandom(2), "big")
            if sequence not in self.pending:
                return sequence
        start = int.from_bytes(os.urandom(2), "big")
        for offset in range(0x10000):
            sequence = (start + offset) & 0xFFFF
            if sequence not in self.pending:
                return sequence
        raise OSError(errno.ENOBUFS, "no free ICMP sequence number")


class _PendingEcho:
    __slots__ = (
        "destination",
        "future",
        "icmp_socket",
        "sequence",
        "timeout_ns",
        "timer",
        "token",
        "tx_monotonic_ns",
        "tx_wall_ns",
    )

    def __init__(
        self,
        icmp_socket: _IcmpSocket,
        *,
        sequence: int,
        destination: bytes,
        token: bytes,
        future: asyncio.Future[EchoResult],
        timeout_ns: int,
    ) -> None:
        self.icmp_socket = icmp_socket
        self.sequence = sequence
        self.destination = destination
        self.token = token
        self.future = future
        self.timeout_ns = timeout_ns
        self.timer: asyncio.TimerHandle | None = None
        self.tx_monotonic_ns = 0
        self.tx_wall_ns = 0


class IcmpEngine:
    """Shared, thread-free ICMP echo sockets for one event loop."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        socket_factory: SocketFactory = socket.socket,
        linux: bool = _LINUX,
        kernel_timestamps: bool = _KERNEL_TIMESTAMPS,
        probes_per_socket: int | None = None,
    ) -> None:
        # Only a weak reference: the engine is released with its loop.
        self._loop_ref = weakref.ref(loop)
        self._socket_factory = socket_factory
        self._linux = linux
        self._kernel_timestamps = kernel_timestamps and linux
        self._probes_per_socket = probes_per_socket
        self._sockets: dict[bool, list[_IcmpSocket]] = {False: [], True: []}
        self.closed = False

    def socket_count(self, *, ipv6: bool) -> int:
        return len(self._sockets[ipv6])

    async def echo(self, destination: str, *, ipv6: bool, timeout_s: float) -> EchoResult:
        """Send one echo request to an IP literal and wait for its outcome."""
        loop = asyncio.get_running_loop()
        # The CLI accepts any finite timeout; one near the float maximum would
        # overflow the nanosecond conversions below and fail every probe.
        timeout_s = min(timeout_s, _MAX_ECHO_TIMEOUT_SECONDS)
        deadline = loop.time() + timeout_s
        try:
            icmp_socket = self._socket_for(loop, ipv6=ipv6)
        except PermissionError as exc:
            return EchoResult(SampleStatus.ERROR, error=_socket_permission_error(exc))
        except OSError as exc:
            return EchoResult(SampleStatus.ERROR, error=f"cannot open ICMP socket: {exc}")
        except Exception as exc:  # noqa: BLE001 - e.g. a loop without add_reader support
            return EchoResult(SampleStatus.ERROR, error=f"ICMP engine unavailable: {exc}")
        try:
            entry = self._register(
                icmp_socket, destination=destination, loop=loop, timeout_s=timeout_s
            )
        except (OSError, OverflowError, ValueError) as exc:
            return EchoResult(SampleStatus.ERROR, error=str(exc))

        packet = echo_request(
            ipv6=ipv6,
            ident=icmp_socket.ident,
            sequence=entry.sequence,
            token=entry.token,
        )
        try:
            delay = _SEND_RETRY_DELAY_SECONDS
            while (failure := self._send(entry, packet, (destination, 0))) is not None:
                if failure.errno not in _SEND_BACKOFF_ERRNOS:
                    return _send_failure(failure, destination)
                if deadline - loop.time() <= delay:
                    return EchoResult(SampleStatus.ERROR, error=f"ICMP send failed: {failure}")
                await asyncio.sleep(delay)
                delay = min(delay * 2, _SEND_RETRY_MAX_DELAY_SECONDS)

            entry.timeout_ns = max(0, int((deadline - loop.time()) * 1_000_000_000))
            entry.timer = loop.call_at(deadline, self._expire, entry)
            return await entry.future
        finally:
            self._forget(entry)

    def close(self) -> None:
        """Close every socket and fail the probes still in flight."""
        if self.closed:
            return
        self.closed = True
        loop = self._loop_ref()
        for sockets in self._sockets.values():
            for icmp_socket in sockets:
                if loop is not None and not loop.is_closed():
                    with contextlib.suppress(Exception):
                        loop.remove_reader(icmp_socket.fd)
                closed = EchoResult(SampleStatus.ERROR, error="ICMP engine closed")
                for entry in list(icmp_socket.pending.values()):
                    with contextlib.suppress(RuntimeError):
                        self._finish(entry, closed)
                icmp_socket.pending.clear()
                with contextlib.suppress(OSError):
                    icmp_socket.sock.close()
            sockets.clear()

    def _socket_for(self, loop: asyncio.AbstractEventLoop, *, ipv6: bool) -> _IcmpSocket:
        sockets = self._sockets[ipv6]
        for icmp_socket in sockets:
            if len(icmp_socket.pending) < icmp_socket.capacity:
                return icmp_socket
        # Every socket on macOS sees every reply, so more sockets would only
        # multiply the reads there.
        if not sockets or (self._linux and len(sockets) < _MAX_SOCKETS_PER_FAMILY):
            try:
                icmp_socket = self._open_socket(loop, ipv6=ipv6)
            except OSError:
                if not sockets:
                    raise
            else:
                sockets.append(icmp_socket)
                return icmp_socket
        return min(sockets, key=lambda candidate: len(candidate.pending))

    def _open_socket(self, loop: asyncio.AbstractEventLoop, *, ipv6: bool) -> _IcmpSocket:
        if self.closed:
            raise OSError(errno.EBADF, "ICMP engine closed")
        if ipv6:
            sock = self._socket_factory(
                socket.AF_INET6, socket.SOCK_DGRAM, getattr(socket, "IPPROTO_ICMPV6", 58)
            )
        else:
            sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
        try:
            sock.setblocking(False)
            buffer_size = _receive_buffer_size(sock)
            capacity = self._probes_per_socket or max(
                _MIN_PROBES_PER_SOCKET,
                min(_MAX_PROBES_PER_SOCKET, buffer_size // _RECEIVE_BUFFER_BYTES_PER_PROBE),
            )
            icmp_socket = _IcmpSocket(sock, ipv6=ipv6, capacity=capacity)
            if self._linux:
                try:
                    if ipv6:
                        sock.setsockopt(socket.IPPROTO_IPV6, _IPV6_RECVERR, 1)
                    else:
                        sock.setsockopt(socket.IPPROTO_IP, _IP_RECVERR, 1)
                    icmp_socket.recverr = True
                except OSError:
                    icmp_socket.recverr = False
            if self._kernel_timestamps:
                try:
                    sock.setsockopt(socket.SOL_SOCKET, _SO_TIMESTAMPNS, 1)
                    icmp_socket.timestamps = True
                except OSError:
                    icmp_socket.timestamps = False
            loop.add_reader(icmp_socket.fd, self._on_readable, icmp_socket)
        except BaseException:
            sock.close()
            raise
        return icmp_socket

    def _register(
        self,
        icmp_socket: _IcmpSocket,
        *,
        destination: str,
        loop: asyncio.AbstractEventLoop,
        timeout_s: float,
    ) -> _PendingEcho:
        sequence = icmp_socket.next_sequence()
        entry = _PendingEcho(
            icmp_socket,
            sequence=sequence,
            destination=packed_address(destination),
            token=os.urandom(TOKEN_SIZE),
            future=loop.create_future(),
            timeout_ns=int(timeout_s * 1_000_000_000),
        )
        icmp_socket.pending[sequence] = entry
        return entry

    def _send(
        self, entry: _PendingEcho, packet: bytes, sockaddr: tuple[str, int]
    ) -> OSError | None:
        icmp_socket = entry.icmp_socket
        failure: OSError | None = None
        unexplained = 0
        for _attempt in range(_SEND_ATTEMPTS):
            # Stamp immediately before sendto: monotonic first, then the wall
            # clock that kernel receive timestamps use.
            entry.tx_monotonic_ns = time.perf_counter_ns()
            entry.tx_wall_ns = time.time_ns()
            try:
                icmp_socket.sock.sendto(packet, sockaddr)
            except OSError as exc:
                if exc.errno in _SEND_BACKOFF_ERRNOS:
                    return exc
                failure = exc
                # On Linux every ICMP error that reaches the socket, whichever
                # probe it is for, fails the socket's next send once with its
                # errno; the error itself waits on the error queue. Reading the
                # queue hands those errors to their probes and clears the
                # socket error, so the send is tried again while errors keep
                # turning up there. The error can also be flagged just after
                # the queue was read, so only a failure that repeats twice in a
                # row with nothing queued is this send's own.
                if icmp_socket.recverr and (
                    self._drain_error_queue(icmp_socket, _READ_BUDGET) < _READ_BUDGET
                ):
                    unexplained = 0
                    continue
                unexplained += 1
                if unexplained == 2:
                    return exc
                continue
            return None
        return failure

    def _forget(self, entry: _PendingEcho) -> None:
        pending = entry.icmp_socket.pending
        if pending.get(entry.sequence) is entry:
            del pending[entry.sequence]
        if entry.timer is not None:
            entry.timer.cancel()
            entry.timer = None

    def _finish(self, entry: _PendingEcho, result: EchoResult) -> None:
        self._forget(entry)
        if not entry.future.done():
            entry.future.set_result(result)

    def _expire(self, entry: _PendingEcho) -> None:
        entry.timer = None
        if entry.future.done():
            return
        # A reply may already be queued but unread: the reader runs after this
        # timer in some loop iterations, or stopped at its read budget.
        self._on_readable(entry.icmp_socket)
        if not entry.future.done():
            self._finish(entry, EchoResult(SampleStatus.TIMEOUT))

    def _on_readable(self, icmp_socket: _IcmpSocket) -> None:
        # Every probe on the socket depends on this callback, so nothing may
        # escape it; a malformed datagram is dropped where it is parsed.
        with contextlib.suppress(Exception):
            self._drain(icmp_socket)

    def _drain(self, icmp_socket: _IcmpSocket) -> None:
        budget = _READ_BUDGET
        if icmp_socket.recverr:
            budget = self._drain_error_queue(icmp_socket, budget)
        sock = icmp_socket.sock
        while budget > 0:
            budget -= 1
            try:
                if icmp_socket.timestamps:
                    data, ancdata, _flags, address = sock.recvmsg(
                        _RECEIVE_SIZE, _TIMESTAMP_ANCILLARY_SIZE
                    )
                else:
                    data, address = sock.recvfrom(_RECEIVE_SIZE)
                    ancdata = []
            except BlockingIOError:
                return
            except OSError:
                if not icmp_socket.recverr:
                    return
                # Linux fails one receive after an ICMP error arrives; the
                # error itself is read from the error queue.
                budget = self._drain_error_queue(icmp_socket, budget)
                continue
            with contextlib.suppress(Exception):
                self._handle_datagram(icmp_socket, data, ancdata, address)

    def _drain_error_queue(self, icmp_socket: _IcmpSocket, budget: int) -> int:
        """Handle up to budget queued errors and return the budget left."""
        sock = icmp_socket.sock
        while budget > 0:
            try:
                data, ancdata, _flags, address = sock.recvmsg(
                    _RECEIVE_SIZE, _ERROR_QUEUE_ANCILLARY_SIZE, _MSG_ERRQUEUE
                )
            except OSError:
                return budget
            budget -= 1
            with contextlib.suppress(Exception):
                self._handle_queued_error(icmp_socket, data, ancdata, address)
        return budget

    def _match(
        self,
        icmp_socket: _IcmpSocket,
        echo: bytes,
        address: bytes,
        *,
        quoted: bool,
    ) -> _PendingEcho | None:
        """Return the probe an echo header belongs to.

        For an echo reply, address is its source; for a quoted request, the
        original destination. A quote may be cut short, so only the quoted
        part of the token has to match.
        """
        ident, sequence = _ECHO_IDENT_SEQUENCE.unpack_from(echo, 4)
        entry = icmp_socket.pending.get(sequence)
        if entry is None or entry.future.done():
            return None
        if not self._linux and ident != icmp_socket.ident:
            return None
        if address != entry.destination:
            return None
        token = echo[_ECHO_HEADER.size : _ECHO_HEADER.size + TOKEN_SIZE]
        if quoted:
            return entry if entry.token.startswith(token) else None
        return entry if token == entry.token else None

    def _handle_datagram(
        self,
        icmp_socket: _IcmpSocket,
        data: bytes,
        ancdata: list[tuple[int, int, bytes]],
        address: Any,
    ) -> None:
        ipv6 = icmp_socket.ipv6
        if not ipv6 and not self._linux and data and data[0] >> 4 == 4:
            data = data[(data[0] & 0x0F) * 4 :]
        if len(data) < _ECHO_HEADER.size:
            return
        icmp_type, code = data[0], data[1]
        if icmp_type == (ICMPV6_ECHO_REPLY if ipv6 else ICMP_ECHO_REPLY):
            entry = self._match(icmp_socket, data, packed_address(address[0]), quoted=False)
            if entry is not None:
                self._finish_reply(entry, _kernel_receive_time_ns(ancdata))
            return
        if self._linux:
            return
        outcome = _icmp_error_outcome(ipv6=ipv6, icmp_type=icmp_type, code=code)
        if outcome is None:
            return
        quoted = _quoted_echo(ipv6=ipv6, quote=data[_ECHO_HEADER.size :])
        if quoted is None:
            return
        destination, echo = quoted
        entry = self._match(icmp_socket, echo, destination, quoted=True)
        if entry is not None:
            self._finish_error(entry, outcome, offender=address[0])

    def _handle_queued_error(
        self,
        icmp_socket: _IcmpSocket,
        data: bytes,
        ancdata: list[tuple[int, int, bytes]],
        address: Any,
    ) -> None:
        extended = next(
            (
                payload
                for level, kind, payload in ancdata
                if (level, kind) in _RECVERR_CMSG_TYPES and len(payload) >= _SOCK_EXTENDED_ERR.size
            ),
            None,
        )
        if extended is None or not address:
            return
        ee_errno, origin, icmp_type, code, _pad, _info, _data = _SOCK_EXTENDED_ERR.unpack_from(
            extended
        )
        if origin in (_SO_EE_ORIGIN_ICMP, _SO_EE_ORIGIN_ICMP6):
            outcome = _icmp_error_outcome(ipv6=icmp_socket.ipv6, icmp_type=icmp_type, code=code)
        elif origin == _SO_EE_ORIGIN_LOCAL:
            outcome = _errno_outcome(ee_errno)
        else:
            return
        request_type = ICMPV6_ECHO_REQUEST if icmp_socket.ipv6 else ICMP_ECHO_REQUEST
        if outcome is None or len(data) < _ECHO_HEADER.size or data[0] != request_type:
            return
        # The queued datagram is the probe's own echo request, and the address
        # is the destination it was sent to.
        entry = self._match(icmp_socket, data, packed_address(address[0]), quoted=True)
        if entry is not None:
            offender = _sockaddr_host(extended[_SOCK_EXTENDED_ERR.size :])
            self._finish_error(entry, outcome, offender=offender)

    def _finish_reply(self, entry: _PendingEcho, rx_wall_ns: int | None) -> None:
        rtt_ns = time.perf_counter_ns() - entry.tx_monotonic_ns
        if rx_wall_ns is not None:
            # The kernel stamped the reply when it arrived, so event-loop delay
            # before this read is not counted. Both stamps use the wall clock;
            # a step between them falls outside [0, monotonic] and is ignored.
            kernel_rtt_ns = rx_wall_ns - entry.tx_wall_ns
            if 0 <= kernel_rtt_ns <= rtt_ns + _KERNEL_TIMESTAMP_SLACK_NS:
                rtt_ns = kernel_rtt_ns
        if rtt_ns > entry.timeout_ns:
            self._finish(entry, EchoResult(SampleStatus.TIMEOUT))
        else:
            self._finish(entry, EchoResult(SampleStatus.OK, round(rtt_ns / 1_000_000, 2)))

    def _finish_error(
        self, entry: _PendingEcho, outcome: EchoResult, *, offender: str | None
    ) -> None:
        message = outcome.error or outcome.status.value
        if offender:
            try:
                same_host = packed_address(offender) == entry.destination
            except (OSError, ValueError):
                same_host = False
            if not same_host:
                message = f"{message} (from {offender})"
        self._finish(entry, EchoResult(outcome.status, error=message))


_engines: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, IcmpEngine] = (
    weakref.WeakKeyDictionary()
)


def engine_for_running_loop() -> IcmpEngine:
    """Return the running loop's ICMP engine, creating it on first use."""
    loop = asyncio.get_running_loop()
    engine = _engines.get(loop)
    if engine is None or engine.closed:
        for other_loop, other_engine in list(_engines.items()):
            if other_loop.is_closed():
                other_engine.close()
                del _engines[other_loop]
        engine = IcmpEngine(loop)
        _engines[loop] = engine
        weakref.finalize(loop, engine.close)
    return engine
