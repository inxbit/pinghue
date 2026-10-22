import asyncio
import contextlib
import errno
import gc
import socket
import struct
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from pinghue import icmp
from pinghue.models import AddressFamily, ProbeSample, SampleStatus, TargetRun, TargetStatus
from pinghue.probes import icmp_probe

ECHO_HEADER = struct.Struct("!BBHHH")
IP_RECVERR = 11
IPV6_RECVERR = 25
SO_TIMESTAMPNS = 35
MSG_ERRQUEUE = getattr(socket, "MSG_ERRQUEUE", 0x2000)

Responder = Callable[["FakeIcmpSocket", bytes, Any], None]


class FakeIcmpSocket:
    """Datagram ICMP socket double; a socketpair provides the readiness fd."""

    def __init__(self, family: int, _type: int, _proto: int) -> None:
        self.family = family
        self._wake_reader, self._wake_writer = socket.socketpair()
        self._wake_reader.setblocking(False)
        self.options: dict[tuple[int, int], int] = {}
        self.sent: list[tuple[bytes, Any]] = []
        self.send_errors: list[OSError] = []
        # Runs before each send error is raised, e.g. to queue an ICMP error.
        self.on_send_error: Callable[[FakeIcmpSocket], None] | None = None
        self.receive_errors: list[OSError] = []
        self.replies: deque[tuple[bytes, list[tuple[int, int, bytes]], int, Any]] = deque()
        self.errors: deque[tuple[bytes, list[tuple[int, int, bytes]], int, Any]] = deque()
        self.responder: Responder | None = None
        self.closed = False

    def fileno(self) -> int:
        return self._wake_reader.fileno()

    def setblocking(self, flag: bool) -> None:
        self.blocking = flag

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options[(level, option)] = value

    def getsockopt(self, level: int, option: int, _buflen: int = 0) -> int:
        return self.options.get((level, option), 0)

    def sendto(self, data: bytes, address: Any) -> int:
        if self.send_errors:
            if self.on_send_error is not None:
                self.on_send_error(self)
            raise self.send_errors.pop(0)
        self.sent.append((bytes(data), address))
        if self.responder is not None:
            self.responder(self, bytes(data), address)
        return len(data)

    def deliver(
        self, data: bytes, address: Any, ancdata: list[tuple[int, int, bytes]] | None = None
    ) -> None:
        self.replies.append((data, list(ancdata or []), 0, address))
        self._wake_writer.send(b"r")

    def queue_error(self, data: bytes, address: Any, ancdata: list[tuple[int, int, bytes]]) -> None:
        self.errors.append((data, list(ancdata), MSG_ERRQUEUE, address))
        self._wake_writer.send(b"e")

    def recvmsg(
        self, _bufsize: int, _ancbufsize: int = 0, flags: int = 0
    ) -> tuple[bytes, list[tuple[int, int, bytes]], int, Any]:
        if not flags & MSG_ERRQUEUE and self.receive_errors:
            raise self.receive_errors.pop(0)
        queue = self.errors if flags & MSG_ERRQUEUE else self.replies
        if not queue:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        with contextlib.suppress(BlockingIOError):
            self._wake_reader.recv(1)
        return queue.popleft()

    def recvfrom(self, bufsize: int) -> tuple[bytes, Any]:
        data, _ancdata, _flags, address = self.recvmsg(bufsize)
        return data, address

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._wake_reader.close()
            self._wake_writer.close()


class FakeSocketFactory:
    def __init__(
        self,
        *,
        responder: Responder | None = None,
        error: OSError | None = None,
        send_errors: list[OSError] | None = None,
    ) -> None:
        self.responder = responder
        self.error = error
        self.send_errors = send_errors or []
        self.sockets: list[FakeIcmpSocket] = []

    def __call__(self, family: int, socket_type: int, proto: int) -> FakeIcmpSocket:
        if self.error is not None:
            raise self.error
        sock = FakeIcmpSocket(family, socket_type, proto)
        sock.responder = self.responder
        sock.send_errors = list(self.send_errors)
        self.sockets.append(sock)
        return sock


def ipv4_header(source: str, destination: str, *, proto: int = 1, ihl: int = 5) -> bytes:
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x40 | ihl,
        0,
        0,
        0,
        0,
        64,
        proto,
        0,
        socket.inet_aton(source),
        socket.inet_aton(destination),
    )
    return header + bytes(4 * ihl - 20)


def ipv6_header(source: str, destination: str, *, next_header: int = 58) -> bytes:
    return (
        struct.pack("!IHBB", 0x60000000, 0, next_header, 64)
        + socket.inet_pton(socket.AF_INET6, source)
        + socket.inet_pton(socket.AF_INET6, destination)
    )


def echo_reply_for(
    request: bytes, *, ident: int | None = None, token: bytes | None = None
) -> bytes:
    request_type, _code, _checksum, request_ident, sequence = ECHO_HEADER.unpack_from(request)
    reply_type = icmp.ICMPV6_ECHO_REPLY if request_type == 128 else icmp.ICMP_ECHO_REPLY
    payload = request[ECHO_HEADER.size :]
    if token is not None:
        payload = token + payload[len(token) :]
    return (
        ECHO_HEADER.pack(reply_type, 0, 0, request_ident if ident is None else ident, sequence)
        + payload
    )


def extended_error(
    *, ee_errno: int, origin: int, icmp_type: int, code: int, offender: str | None
) -> bytes:
    extended = struct.pack("=IBBBBII", ee_errno, origin, icmp_type, code, 0, 0, 0)
    if offender is None:
        return extended + bytes(16)
    if ":" in offender:
        return extended + struct.pack(
            "=HHI16sI", socket.AF_INET6, 0, 0, socket.inet_pton(socket.AF_INET6, offender), 0
        )
    return extended + struct.pack("=HH4s8x", socket.AF_INET, 0, socket.inet_aton(offender))


def timestamp_cmsg(wall_ns: int) -> tuple[int, int, bytes]:
    seconds, nanoseconds = divmod(wall_ns, 1_000_000_000)
    return (socket.SOL_SOCKET, SO_TIMESTAMPNS, struct.pack("@ll", seconds, nanoseconds))


def linux_echo(*, source: str | None = None, token: bytes | None = None) -> Responder:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        sock.deliver(echo_reply_for(data, token=token), (source or address[0], 0))

    return respond


@pytest.fixture
def engines() -> Iterator[list[icmp.IcmpEngine]]:
    created: list[icmp.IcmpEngine] = []
    yield created
    for engine in created:
        engine.close()


def make_engine(
    engines: list[icmp.IcmpEngine],
    factory: FakeSocketFactory,
    *,
    linux: bool = True,
    kernel_timestamps: bool = False,
    probes_per_socket: int | None = None,
) -> icmp.IcmpEngine:
    engine = icmp.IcmpEngine(
        asyncio.get_running_loop(),
        socket_factory=factory,
        linux=linux,
        kernel_timestamps=kernel_timestamps,
        probes_per_socket=probes_per_socket,
    )
    engines.append(engine)
    return engine


def pending_count(engine: icmp.IcmpEngine) -> int:
    return sum(
        len(icmp_socket.pending) for sockets in engine._sockets.values() for icmp_socket in sockets
    )


def test_checksum_matches_rfc1071_example() -> None:
    assert icmp.icmp_checksum(bytes.fromhex("0001f203f4f5f6f7")) == 0x220D
    assert icmp.icmp_checksum(b"\x01") == 0xFEFF


def test_echo_request_layout_carries_token() -> None:
    token = bytes(range(16))
    packet = icmp.echo_request(ipv6=False, ident=0x1234, sequence=7, token=token)

    assert len(packet) == 64
    request_type, code, _checksum, ident, sequence = ECHO_HEADER.unpack_from(packet)
    assert (request_type, code, ident, sequence) == (8, 0, 0x1234, 7)
    assert icmp.icmp_checksum(packet) == 0
    assert packet[8:24] == token

    packet_v6 = icmp.echo_request(ipv6=True, ident=1, sequence=2, token=token)
    assert packet_v6[0] == 128
    assert packet_v6[2:4] == b"\x00\x00"
    assert packet_v6[8:24] == token


def test_packed_address_normalises_scope_and_ipv4_mapped_forms() -> None:
    assert icmp.packed_address("fe80::1%eth0") == icmp.packed_address("fe80:0:0:0:0:0:0:1")
    assert icmp.packed_address("::ffff:127.0.0.1") == icmp.packed_address("127.0.0.1")
    assert icmp.packed_address("2001:DB8::1") == icmp.packed_address("2001:db8::1")


async def test_linux_echo_reply_is_matched(engines: list[icmp.IcmpEngine]) -> None:
    factory = FakeSocketFactory(responder=linux_echo())
    engine = make_engine(engines, factory)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK
    assert result.latency_ms is not None and 0 <= result.latency_ms < 1000
    sock = factory.sockets[0]
    assert sock.options[(socket.IPPROTO_IP, IP_RECVERR)] == 1
    assert sock.sent[0][1] == ("127.0.0.1", 0)
    assert pending_count(engine) == 0


@pytest.mark.parametrize(
    "responder",
    [
        pytest.param(linux_echo(source="127.0.0.2"), id="other-source"),
        pytest.param(linux_echo(token=b"\xff" * 16), id="other-token"),
    ],
)
async def test_reply_that_does_not_match_the_probe_is_ignored(
    engines: list[icmp.IcmpEngine], responder: Responder
) -> None:
    engine = make_engine(engines, FakeSocketFactory(responder=responder))

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=0.05)

    assert result.status == SampleStatus.TIMEOUT
    assert pending_count(engine) == 0


async def test_macos_reply_strips_ip_header_and_requires_identifier(
    engines: list[icmp.IcmpEngine],
) -> None:
    replies: list[str] = []

    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        ident = ECHO_HEADER.unpack_from(data)[3]
        header = ipv4_header(address[0], "127.0.0.1", ihl=6)
        if not replies:
            # Another process's probe with the same sequence: every macOS ICMP
            # socket receives it, and only the identifier tells it apart.
            sock.deliver(header + echo_reply_for(data, ident=(ident + 1) & 0xFFFF), address)
            replies.append("foreign")
        else:
            sock.deliver(header + echo_reply_for(data), address)
            replies.append("own")

    engine = make_engine(engines, FakeSocketFactory(responder=respond), linux=False)

    foreign = await engine.echo("127.0.0.1", ipv6=False, timeout_s=0.05)
    own = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert foreign.status == SampleStatus.TIMEOUT
    assert own.status == SampleStatus.OK
    assert replies == ["foreign", "own"]


async def test_macos_ipv6_reply_has_no_ip_header(engines: list[icmp.IcmpEngine]) -> None:
    factory = FakeSocketFactory(responder=linux_echo())
    engine = make_engine(engines, factory, linux=False)

    result = await engine.echo("::1", ipv6=True, timeout_s=1.0)

    assert result.status == SampleStatus.OK
    assert factory.sockets[0].family == socket.AF_INET6
    assert (socket.IPPROTO_IPV6, IPV6_RECVERR) not in factory.sockets[0].options


async def test_linux_error_queue_reports_unreachable_with_offender(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        ancdata = [
            (
                socket.IPPROTO_IP,
                IP_RECVERR,
                extended_error(
                    ee_errno=errno.EHOSTUNREACH,
                    origin=2,
                    icmp_type=3,
                    code=1,
                    offender="192.0.2.1",
                ),
            )
        ]
        # The kernel quotes the probe's own request, cut short by the router.
        sock.queue_error(data[:16], address, ancdata)

    engine = make_engine(engines, FakeSocketFactory(responder=respond))

    started = time.monotonic()
    result = await engine.echo("198.51.100.7", ipv6=False, timeout_s=5.0)

    assert result.status == SampleStatus.UNREACHABLE
    assert result.error == "Destination host unreachable (from 192.0.2.1)"
    assert time.monotonic() - started < 1.0


async def test_linux_error_queue_reports_ipv6_time_exceeded(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        ancdata = [
            (
                socket.IPPROTO_IPV6,
                IPV6_RECVERR,
                extended_error(
                    ee_errno=errno.EHOSTUNREACH,
                    origin=3,
                    icmp_type=3,
                    code=0,
                    offender="2001:db8::fe",
                ),
            )
        ]
        sock.queue_error(data, address, ancdata)

    factory = FakeSocketFactory(responder=respond)
    engine = make_engine(engines, factory)

    result = await engine.echo("2001:db8::7", ipv6=True, timeout_s=5.0)

    assert result.status == SampleStatus.UNREACHABLE
    assert result.error == "Hop limit exceeded in transit (from 2001:db8::fe)"
    assert factory.sockets[0].options[(socket.IPPROTO_IPV6, IPV6_RECVERR)] == 1


async def test_linux_local_error_is_classified_by_errno(engines: list[icmp.IcmpEngine]) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        ancdata = [
            (
                socket.IPPROTO_IP,
                IP_RECVERR,
                extended_error(
                    ee_errno=errno.EHOSTUNREACH, origin=1, icmp_type=0, code=0, offender=None
                ),
            )
        ]
        sock.queue_error(data, address, ancdata)

    engine = make_engine(engines, FakeSocketFactory(responder=respond))

    result = await engine.echo("198.51.100.7", ipv6=False, timeout_s=5.0)

    assert result.status == SampleStatus.UNREACHABLE
    assert result.error == "No route to host"


@pytest.mark.parametrize(
    ("destination", "origin", "icmp_type", "token"),
    [
        pytest.param("198.51.100.8", 2, 3, None, id="other-destination"),
        pytest.param("198.51.100.7", 2, 4, None, id="source-quench"),
        pytest.param("198.51.100.7", 2, 3, b"\xff" * 8, id="other-token"),
        pytest.param("198.51.100.7", 4, 3, None, id="unknown-origin"),
    ],
)
async def test_linux_error_queue_ignores_errors_for_other_probes(
    engines: list[icmp.IcmpEngine],
    destination: str,
    origin: int,
    icmp_type: int,
    token: bytes | None,
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, _address: Any) -> None:
        quoted = data[:8] + (token if token is not None else data[8:16])
        ancdata = [
            (
                socket.IPPROTO_IP,
                IP_RECVERR,
                extended_error(
                    ee_errno=errno.EHOSTUNREACH,
                    origin=origin,
                    icmp_type=icmp_type,
                    code=1,
                    offender="192.0.2.1",
                ),
            )
        ]
        sock.queue_error(quoted, (destination, 0), ancdata)

    engine = make_engine(engines, FakeSocketFactory(responder=respond))

    result = await engine.echo("198.51.100.7", ipv6=False, timeout_s=0.05)

    assert result.status == SampleStatus.TIMEOUT


async def test_macos_icmp_error_datagram_reports_unreachable(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        quoted = ipv4_header("10.0.0.2", address[0]) + data[:16]
        error = struct.pack("!BBHI", 3, 13, 0, 0) + quoted
        sock.deliver(ipv4_header("192.0.2.1", "10.0.0.2") + error, ("192.0.2.1", 0))

    engine = make_engine(engines, FakeSocketFactory(responder=respond), linux=False)

    result = await engine.echo("198.51.100.7", ipv6=False, timeout_s=5.0)

    assert result.status == SampleStatus.UNREACHABLE
    assert result.error == "Communication administratively prohibited (from 192.0.2.1)"
    assert "permission" not in (result.error or "")


async def test_macos_ipv6_error_datagram_reports_unreachable(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        quoted = ipv6_header("2001:db8::2", address[0]) + data
        sock.deliver(struct.pack("!BBHI", 1, 3, 0, 0) + quoted, ("2001:db8::fe", 0, 0, 0))

    engine = make_engine(engines, FakeSocketFactory(responder=respond), linux=False)

    result = await engine.echo("2001:db8::7", ipv6=True, timeout_s=5.0)

    assert result.status == SampleStatus.UNREACHABLE
    assert result.error == "Address unreachable (from 2001:db8::fe)"


@pytest.mark.parametrize(
    "make_error",
    [
        pytest.param(
            lambda request, destination: (
                struct.pack("!BBHI", 3, 1, 0, 0)
                + ipv4_header("10.0.0.2", destination)
                + request[:4]
                + bytes(byte ^ 0xFF for byte in request[4:6])
                + request[6:16]
            ),
            id="other-identifier",
        ),
        pytest.param(
            lambda request, destination: (
                struct.pack("!BBHI", 3, 1, 0, 0)
                + ipv4_header("10.0.0.2", destination, proto=17)
                + request[:16]
            ),
            id="not-icmp",
        ),
        pytest.param(
            lambda request, _destination: (
                struct.pack("!BBHI", 3, 1, 0, 0)
                + ipv4_header("10.0.0.2", "198.51.100.99")
                + request[:16]
            ),
            id="other-destination",
        ),
        pytest.param(
            lambda request, destination: (
                struct.pack("!BBHI", 5, 1, 0, 0)
                + ipv4_header("10.0.0.2", destination)
                + request[:16]
            ),
            id="redirect",
        ),
    ],
)
async def test_macos_icmp_error_for_other_probe_is_ignored(
    engines: list[icmp.IcmpEngine], make_error: Callable[[bytes, str], bytes]
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        error = make_error(data, address[0])
        sock.deliver(ipv4_header("192.0.2.1", "10.0.0.2") + error, ("192.0.2.1", 0))

    engine = make_engine(engines, FakeSocketFactory(responder=respond), linux=False)

    result = await engine.echo("198.51.100.7", ipv6=False, timeout_s=0.05)

    assert result.status == SampleStatus.TIMEOUT


def test_sequence_numbers_are_random_and_skip_in_flight_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sock = FakeIcmpSocket(socket.AF_INET, socket.SOCK_DGRAM, 1)
    try:
        icmp_socket = icmp._IcmpSocket(sock, ipv6=False, capacity=4)
        # A target sees its own probes' sequence numbers; counting up would let
        # it predict those of the probes in flight to other targets.
        drawn = {icmp_socket.next_sequence() for _ in range(64)}
        assert len(drawn) > 32
        assert drawn != set(range(min(drawn), min(drawn) + len(drawn)))

        # Draws that land on in-flight probes are skipped.
        draws = iter([b"\x00\x05", b"\x00\x07", b"\x00\x09"])
        monkeypatch.setattr(icmp.os, "urandom", lambda _size: next(draws))
        icmp_socket.pending = dict.fromkeys((5, 7), object())  # type: ignore[arg-type]
        assert icmp_socket.next_sequence() == 9

        # When every draw is taken, a scan from a random start finds a free one.
        monkeypatch.setattr(icmp.os, "urandom", lambda _size: b"\xff\xfe")
        icmp_socket.pending = dict.fromkeys((0xFFFE, 0xFFFF, 0), object())  # type: ignore[arg-type]
        assert icmp_socket.next_sequence() == 1

        icmp_socket.pending = dict.fromkeys(range(0x10000), object())  # type: ignore[arg-type]
        with pytest.raises(OSError, match="no free ICMP sequence number"):
            icmp_socket.next_sequence()
    finally:
        sock.close()


async def test_probes_spread_over_sockets_without_sequence_reuse(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory, probes_per_socket=2)

    results = await asyncio.gather(
        *(engine.echo(f"127.0.0.{index}", ipv6=False, timeout_s=0.05) for index in range(1, 6))
    )

    assert {result.status for result in results} == {SampleStatus.TIMEOUT}
    assert len(factory.sockets) == 3
    for sock in factory.sockets:
        sequences = [ECHO_HEADER.unpack_from(data)[4] for data, _address in sock.sent]
        assert len(sequences) == len(set(sequences))
    assert pending_count(engine) == 0


async def test_macos_uses_one_socket_per_family(engines: list[icmp.IcmpEngine]) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory, linux=False, probes_per_socket=2)

    await asyncio.gather(
        *(engine.echo(f"127.0.0.{index}", ipv6=False, timeout_s=0.05) for index in range(1, 6))
    )

    assert len(factory.sockets) == 1
    sequences = [ECHO_HEADER.unpack_from(data)[4] for data, _address in factory.sockets[0].sent]
    assert len(sequences) == len(set(sequences)) == 5


async def test_extra_socket_failure_falls_back_to_existing_socket(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory, probes_per_socket=1)
    first = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=0.2))
    await asyncio.sleep(0)
    factory.error = OSError(errno.EMFILE, "Too many open files")

    second = await engine.echo("127.0.0.2", ipv6=False, timeout_s=0.05)

    assert second.status == SampleStatus.TIMEOUT
    assert len(factory.sockets) == 1
    assert len(factory.sockets[0].sent) == 2
    await first


async def test_cancellation_removes_pending_probe(engines: list[icmp.IcmpEngine]) -> None:
    engine = make_engine(engines, FakeSocketFactory())
    task = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=5.0))
    await asyncio.sleep(0)
    assert pending_count(engine) == 1
    entry = next(iter(engine._sockets[False][0].pending.values()))

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pending_count(engine) == 0
    assert entry.timer is None


async def test_timeout_fires_on_loop_timer(engines: list[icmp.IcmpEngine]) -> None:
    engine = make_engine(engines, FakeSocketFactory())
    loop = asyncio.get_running_loop()

    started = loop.time()
    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=0.05)

    assert result == icmp.EchoResult(SampleStatus.TIMEOUT)
    assert 0.04 <= loop.time() - started < 1.0
    assert pending_count(engine) == 0


async def test_expiry_reads_a_reply_that_is_already_queued(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory)
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=5.0))
    await asyncio.sleep(0)
    sock = factory.sockets[0]
    entry = next(iter(engine._sockets[False][0].pending.values()))

    # The reply is queued but the timer runs before the reader gets to it.
    sock.deliver(echo_reply_for(sock.sent[0][0]), ("127.0.0.1", 0))
    loop.remove_reader(sock.fileno())
    engine._expire(entry)

    assert (await task).status == SampleStatus.OK


async def test_late_reply_without_kernel_timestamp_is_a_timeout(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory(responder=linux_echo())
    engine = make_engine(engines, factory)
    task = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=0.02))
    await asyncio.sleep(0)

    time.sleep(0.05)  # the loop is busy past the deadline before reading

    assert (await task).status == SampleStatus.TIMEOUT


async def test_kernel_timestamp_excludes_event_loop_delay(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        sock.deliver(echo_reply_for(data), address, [timestamp_cmsg(time.time_ns())])

    factory = FakeSocketFactory(responder=respond)
    engine = make_engine(engines, factory, kernel_timestamps=True)
    task = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0))
    await asyncio.sleep(0)

    time.sleep(0.05)  # the loop is busy elsewhere before it reads the reply

    result = await task
    assert result.status == SampleStatus.OK
    assert result.latency_ms is not None and result.latency_ms < 25
    assert factory.sockets[0].options[(socket.SOL_SOCKET, SO_TIMESTAMPNS)] == 1


@pytest.mark.parametrize("step_ns", [-2_000_000_000, 2_000_000_000], ids=["backward", "forward"])
async def test_wall_clock_step_falls_back_to_monotonic_rtt(
    engines: list[icmp.IcmpEngine], step_ns: int
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        # The wall clock steps between the send and the kernel's receive stamp.
        sock.deliver(echo_reply_for(data), address, [timestamp_cmsg(time.time_ns() + step_ns)])

    engine = make_engine(engines, FakeSocketFactory(responder=respond), kernel_timestamps=True)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK
    assert result.latency_ms is not None and 0 <= result.latency_ms < 1000


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(
            OSError(errno.EPERM, "Operation not permitted"),
            "permission denied: ICMP send blocked by local policy (EPERM, e.g. a firewall rule)",
            id="eperm",
        ),
        pytest.param(
            OSError(errno.EACCES, "Permission denied"),
            "permission denied: ICMP send blocked by local policy "
            "(EACCES, e.g. a broadcast address or a prohibit route)",
            id="eacces",
        ),
    ],
)
async def test_send_blocked_by_local_policy_reports_permission_denied(
    engines: list[icmp.IcmpEngine], error: OSError, expected: str
) -> None:
    engine = make_engine(engines, FakeSocketFactory(send_errors=[error, error]))

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result == icmp.EchoResult(SampleStatus.ERROR, error=expected)
    target = TargetRun(target="127.0.0.1")
    target.apply_sample(as_sample(result), fail_threshold=3, jitter_threshold_ms=50.0)
    assert target.status == TargetStatus.PERMISSION_DENIED


async def test_send_to_limited_broadcast_names_broadcast(engines: list[icmp.IcmpEngine]) -> None:
    error = OSError(errno.EACCES, "Permission denied")
    engine = make_engine(engines, FakeSocketFactory(send_errors=[error, error]))

    result = await engine.echo("255.255.255.255", ipv6=False, timeout_s=1.0)

    assert result.error == (
        "permission denied: ICMP send blocked by local policy (EACCES, broadcast address)"
    )


async def test_send_retries_once_after_queued_icmp_error(engines: list[icmp.IcmpEngine]) -> None:
    factory = FakeSocketFactory(
        responder=linux_echo(), send_errors=[OSError(errno.EHOSTUNREACH, "No route to host")]
    )
    engine = make_engine(engines, factory)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK
    assert len(factory.sockets[0].sent) == 1


def queue_error_for_another_probe(sock: FakeIcmpSocket) -> None:
    # Linux queues an ICMP error for any probe on the socket and fails the
    # socket's next send with its errno; this one quotes a request to a host
    # that nobody here probes.
    ancdata = [
        (
            socket.IPPROTO_IP,
            IP_RECVERR,
            extended_error(
                ee_errno=errno.EHOSTUNREACH, origin=2, icmp_type=3, code=1, offender="192.0.2.1"
            ),
        )
    ]
    request = ECHO_HEADER.pack(icmp.ICMP_ECHO_REQUEST, 0, 0, 0, 0) + bytes(16)
    sock.queue_error(request, ("192.0.2.99", 0), ancdata)


async def test_send_retries_while_icmp_errors_for_other_probes_arrive(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory(responder=linux_echo())
    engine = make_engine(engines, factory)
    assert (await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)).status == SampleStatus.OK
    sock = factory.sockets[0]
    # A new error lands before every retry, so each attempt fails once more.
    sock.on_send_error = queue_error_for_another_probe
    sock.send_errors = [OSError(errno.EHOSTUNREACH, "No route to host")] * 3

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK
    assert len(sock.sent) == 2
    assert not sock.send_errors
    assert not sock.errors


async def test_send_failure_is_reported_when_icmp_errors_never_stop(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory(responder=linux_echo())
    engine = make_engine(engines, factory)
    assert (await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)).status == SampleStatus.OK
    sock = factory.sockets[0]
    error = OSError(errno.EHOSTUNREACH, "No route to host")
    sock.on_send_error = queue_error_for_another_probe
    sock.send_errors = [error] * 100

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result == icmp.EchoResult(SampleStatus.UNREACHABLE, error=str(error))
    assert len(sock.send_errors) == 100 - icmp._SEND_ATTEMPTS
    assert pending_count(engine) == 0


@pytest.mark.parametrize(
    ("error", "status"),
    [
        pytest.param(OSError(errno.ENETUNREACH, "Network is unreachable"), "unreachable"),
        pytest.param(OSError(errno.EINVAL, "Invalid argument"), "error"),
    ],
)
async def test_send_failure_is_classified(
    engines: list[icmp.IcmpEngine], error: OSError, status: str
) -> None:
    factory = FakeSocketFactory(send_errors=[error] * 3)
    engine = make_engine(engines, factory)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus(status)
    assert result.error == str(error)
    assert pending_count(engine) == 0
    # The failure repeated with nothing queued, so it is the send's own.
    assert len(factory.sockets[0].send_errors) == 1


async def test_send_backs_off_while_the_send_buffer_is_full(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory(
        responder=linux_echo(),
        send_errors=[OSError(errno.ENOBUFS, "No buffer space available")] * 2,
    )
    engine = make_engine(engines, factory)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)
    sock = factory.sockets[0]

    assert result.status == SampleStatus.OK
    assert len(sock.sent) == 1

    sock.send_errors = [BlockingIOError(errno.EAGAIN, "busy")] * 1000
    stuck = await engine.echo("127.0.0.1", ipv6=False, timeout_s=0.02)

    assert stuck.status == SampleStatus.ERROR
    assert (stuck.error or "").startswith("ICMP send failed:")
    assert pending_count(engine) == 0


async def test_socket_permission_error_keeps_permission_denied_text(
    engines: list[icmp.IcmpEngine],
) -> None:
    factory = FakeSocketFactory(error=PermissionError(errno.EACCES, "Permission denied"))
    engine = make_engine(engines, factory)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.ERROR
    assert (result.error or "").startswith("permission denied: unprivileged ICMP sockets")
    assert "pinghue --check" in (result.error or "")


async def test_socket_creation_failure_reports_error(engines: list[icmp.IcmpEngine]) -> None:
    factory = FakeSocketFactory(
        error=OSError(errno.EAFNOSUPPORT, "Address family not supported by protocol")
    )
    engine = make_engine(engines, factory)

    result = await engine.echo("::1", ipv6=True, timeout_s=1.0)

    assert result.status == SampleStatus.ERROR
    assert result.error == f"cannot open ICMP socket: {factory.error}"


async def test_engine_that_cannot_watch_sockets_reports_error(
    engines: list[icmp.IcmpEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory)

    def no_add_reader(*_: object) -> None:
        raise NotImplementedError("add_reader")

    monkeypatch.setattr(asyncio.get_running_loop(), "add_reader", no_add_reader)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result == icmp.EchoResult(
        SampleStatus.ERROR, error="ICMP engine unavailable: add_reader"
    )
    assert factory.sockets[0].closed


async def test_reader_survives_malformed_datagrams(engines: list[icmp.IcmpEngine]) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        sock.deliver(b"", address)
        sock.deliver(b"\x00", address)
        sock.deliver(b"\x00\x00\x00\x00\x00\x00", address)
        sock.deliver(echo_reply_for(data)[:8], address)
        sock.deliver(echo_reply_for(data), ("not-an-address", 0))
        sock.deliver(echo_reply_for(data), address)

    factory = FakeSocketFactory(responder=respond)
    engine = make_engine(engines, factory)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK


async def test_reader_survives_macos_malformed_datagrams(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        sock.deliver(b"\x4f" + bytes(10), address)
        sock.deliver(
            ipv4_header("192.0.2.1", "10.0.0.2") + struct.pack("!BBHI", 3, 1, 0, 0), address
        )
        sock.deliver(
            ipv4_header("192.0.2.1", "10.0.0.2")
            + struct.pack("!BBHI", 3, 1, 0, 0)
            + b"\x45"
            + bytes(25),
            address,
        )
        sock.deliver(ipv4_header(address[0], "10.0.0.2") + echo_reply_for(data), address)

    engine = make_engine(engines, FakeSocketFactory(responder=respond), linux=False)

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK


async def test_reader_callback_never_raises(
    engines: list[icmp.IcmpEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory)
    task = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0))
    await asyncio.sleep(0)
    sock = factory.sockets[0]
    icmp_socket = engine._sockets[False][0]

    def broken(*_: object) -> None:
        raise RuntimeError("parser bug")

    monkeypatch.setattr(engine, "_drain", broken)
    engine._on_readable(icmp_socket)
    monkeypatch.undo()

    sock.deliver(echo_reply_for(sock.sent[0][0]), ("127.0.0.1", 0))
    assert (await task).status == SampleStatus.OK


async def test_reader_continues_after_a_receive_reports_a_queued_error(
    engines: list[icmp.IcmpEngine],
) -> None:
    def respond(sock: FakeIcmpSocket, data: bytes, address: Any) -> None:
        sock.receive_errors.append(OSError(errno.EHOSTUNREACH, "No route to host"))
        sock.deliver(echo_reply_for(data), address)

    engine = make_engine(engines, FakeSocketFactory(responder=respond))

    result = await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)

    assert result.status == SampleStatus.OK


async def test_close_fails_probes_in_flight(engines: list[icmp.IcmpEngine]) -> None:
    factory = FakeSocketFactory()
    engine = make_engine(engines, factory)
    task = asyncio.create_task(engine.echo("127.0.0.1", ipv6=False, timeout_s=5.0))
    await asyncio.sleep(0)

    engine.close()

    assert (await task) == icmp.EchoResult(SampleStatus.ERROR, error="ICMP engine closed")
    assert factory.sockets[0].closed
    assert (await engine.echo("127.0.0.1", ipv6=False, timeout_s=1.0)).status == (
        SampleStatus.ERROR
    )


def test_engine_belongs_to_one_loop_and_is_released_with_it() -> None:
    async def current_engine() -> icmp.IcmpEngine:
        first = icmp.engine_for_running_loop()
        assert icmp.engine_for_running_loop() is first
        return first

    first_loop = asyncio.new_event_loop()
    try:
        first = first_loop.run_until_complete(current_engine())
    finally:
        first_loop.close()
    second_loop = asyncio.new_event_loop()
    try:
        second = second_loop.run_until_complete(current_engine())
    finally:
        second_loop.close()

    assert first is not second
    assert first.closed

    del second_loop
    gc.collect()
    assert second.closed


def as_sample(result: icmp.EchoResult) -> ProbeSample:
    return ProbeSample(
        timestamp=datetime.now(timezone.utc),
        latency_ms=result.latency_ms,
        status=result.status,
        error=result.error,
    )


# Real sockets, loopback only. These skip where unprivileged ICMP sockets are
# not permitted (Linux ping_group_range) or the address family is missing.


def _require_icmp_socket(family: int, proto: int) -> None:
    try:
        sock = socket.socket(family, socket.SOCK_DGRAM, proto)
    except OSError as exc:
        pytest.skip(f"unprivileged ICMP sockets are not available: {exc}")
    sock.close()


@pytest.fixture
def icmp_v4() -> None:
    _require_icmp_socket(socket.AF_INET, socket.IPPROTO_ICMP)


@pytest.fixture
def icmp_v6() -> None:
    if not socket.has_ipv6:
        pytest.skip("IPv6 is not available")
    _require_icmp_socket(socket.AF_INET6, getattr(socket, "IPPROTO_ICMPV6", 58))
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as probe:
            probe.bind(("::1", 0))
    except OSError as exc:
        pytest.skip(f"::1 is not configured: {exc}")


@pytest.mark.usefixtures("icmp_v4")
async def test_real_loopback_echo_ipv4() -> None:
    threads = threading.active_count()

    sample = await icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.AUTO)

    assert sample.status == SampleStatus.OK, sample.error
    assert sample.latency_ms is not None and 0 <= sample.latency_ms < 1000
    assert threading.active_count() == threads


@pytest.mark.usefixtures("icmp_v6")
async def test_real_loopback_echo_ipv6() -> None:
    sample = await icmp_probe("::1", timeout_s=2.0, address_family=AddressFamily.AUTO)

    assert sample.status == SampleStatus.OK, sample.error
    assert sample.latency_ms is not None and 0 <= sample.latency_ms < 1000


@pytest.mark.usefixtures("icmp_v4")
async def test_real_loopback_probes_share_one_socket() -> None:
    # Few enough for macOS, which rate-limits the echo replies it sends, and
    # all to 127.0.0.1, the only loopback address macOS configures by default.
    # Each probe's sequence number and token keep the replies apart.
    threads = threading.active_count()

    samples = await asyncio.gather(
        *(
            icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.IPV4)
            for _ in range(32)
        )
    )

    assert [sample.status for sample in samples] == [SampleStatus.OK] * 32
    assert icmp.engine_for_running_loop().socket_count(ipv6=False) == 1
    assert threading.active_count() == threads


@pytest.mark.usefixtures("icmp_v4")
async def test_real_clock_step_never_yields_negative_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for step_ns in (-5_000_000_000, 5_000_000_000):
        stepped_clock = SimpleNamespace(
            perf_counter_ns=time.perf_counter_ns,
            time_ns=lambda step=step_ns: time.time_ns() + step,
        )
        monkeypatch.setattr(icmp, "time", stepped_clock)

        sample = await icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.IPV4)

        assert sample.status == SampleStatus.OK, sample.error
        assert sample.latency_ms is not None and 0 <= sample.latency_ms < 1000


@pytest.mark.skipif(not icmp._KERNEL_TIMESTAMPS, reason="kernel receive timestamps are Linux-only")
@pytest.mark.usefixtures("icmp_v4")
async def test_real_kernel_timestamp_excludes_event_loop_stall() -> None:
    await icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.IPV4)
    task = asyncio.create_task(
        icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.IPV4)
    )
    await asyncio.sleep(0)

    time.sleep(0.05)  # the loop stalls after the send, before the reply is read

    sample = await task
    assert sample.status == SampleStatus.OK, sample.error
    assert sample.latency_ms is not None and sample.latency_ms < 25


def _forged_host_unreachable(ident: int, sequence: int, destination: str, quote: bytes) -> bytes:
    inner = struct.pack("!BBHHH", 8, 0, 0, ident, sequence) + quote
    header = bytearray(ipv4_header("127.0.0.1", destination))
    struct.pack_into("!H", header, 2, len(header) + len(inner))
    struct.pack_into("!H", header, 10, icmp.icmp_checksum(bytes(header)))
    body = struct.pack("!I", 0) + bytes(header) + inner
    checksum = icmp.icmp_checksum(struct.pack("!BBH", 3, 1, 0) + body)
    return struct.pack("!BBH", 3, 1, checksum) + body


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux error queue")
@pytest.mark.usefixtures("icmp_v4")
async def test_real_error_queue_reports_unreachable_and_spares_next_probe() -> None:
    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    except OSError as exc:
        pytest.skip(f"forging an ICMP error needs a raw socket: {exc}")
    loop = asyncio.get_running_loop()
    with raw:
        assert (
            await icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.IPV4)
        ).status == SampleStatus.OK
        engine = icmp.engine_for_running_loop()
        icmp_socket = engine._sockets[False][0]
        ident = icmp_socket.sock.getsockname()[1]
        # A probe whose request "left" for 127.0.254.9; a forged host
        # unreachable quoting it stands in for a router's answer.
        entry = engine._register(icmp_socket, destination="127.0.254.9", loop=loop, timeout_s=2.0)
        entry.timer = loop.call_later(2.0, engine._expire, entry)
        raw.sendto(
            _forged_host_unreachable(ident, entry.sequence, "127.0.254.9", entry.token[:8]),
            ("127.0.0.1", 0),
        )
        # The kernel queued the error and flagged the socket before this send.
        follow_up = await icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.IPV4)
        result = await asyncio.wait_for(entry.future, 2.0)

    assert follow_up.status == SampleStatus.OK, follow_up.error
    assert result == icmp.EchoResult(
        SampleStatus.UNREACHABLE, error="Destination host unreachable (from 127.0.0.1)"
    )


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="macOS rate-limits loopback echo replies"
)
@pytest.mark.usefixtures("icmp_v4")
async def test_real_loopback_burst_is_cheap() -> None:
    threads = threading.active_count()
    addresses = [f"127.0.{1 + index // 250}.{1 + index % 250}" for index in range(2000)]
    started = time.perf_counter()

    samples = await asyncio.gather(
        *(
            icmp_probe(address, timeout_s=5.0, address_family=AddressFamily.IPV4)
            for address in addresses
        )
    )

    elapsed = time.perf_counter() - started
    assert sum(sample.status == SampleStatus.OK for sample in samples) >= 1990
    assert elapsed < 5.0
    assert threading.active_count() == threads
    assert icmp.engine_for_running_loop().socket_count(ipv6=False) <= (icmp._MAX_SOCKETS_PER_FAMILY)
