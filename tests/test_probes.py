import asyncio
import errno
import socket
import struct
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

import pytest

import pinghue.probes as probes
from pinghue import icmp
from pinghue.models import AddressFamily, SampleStatus, TargetRun, TargetStatus
from pinghue.probes import ResolvedTarget, icmp_probe, resolve_target, tcp_probe


class FakeTcpSocket:
    """Stands in for tcp_probe's socket; a fake connect never touches it."""

    def __init__(
        self, *, tcp_info: bytes | None = None, peer: tuple[str, int] | None = None
    ) -> None:
        self.tcp_info = tcp_info
        self.peer = peer
        self.closed = False

    def getpeername(self) -> tuple[str, int]:
        if self.peer is None:
            raise OSError(errno.ENOTCONN, "Transport endpoint is not connected")
        return self.peer

    def getsockopt(self, level: int, option: int, buflen: int) -> bytes:
        assert (level, option, buflen) == (socket.IPPROTO_TCP, 11, 104)
        if self.tcp_info is None:
            raise OSError(errno.ENOPROTOOPT, "Protocol not available")
        return self.tcp_info

    def close(self) -> None:
        self.closed = True


PEER = ("127.0.0.1", 443)


def _tcp_info(*, rtt_us: int, total_retrans: int) -> bytes:
    info = bytearray(104)
    struct.pack_into("=I", info, 68, rtt_us)
    struct.pack_into("=I", info, 100, total_retrans)
    return bytes(info)


ConnectFn = Callable[[object, tuple[str, int]], Awaitable[None]]


def _install_tcp_connect(
    monkeypatch: pytest.MonkeyPatch, sock: FakeTcpSocket, connect: ConnectFn
) -> None:
    monkeypatch.setattr(probes, "_open_tcp_socket", lambda _address: sock)
    monkeypatch.setattr(asyncio.get_running_loop(), "sock_connect", connect)


async def _connect_at_once(_sock: object, _address: tuple[str, int]) -> None:
    return None


def _slow_connect(delay_s: float = 0.05) -> ConnectFn:
    async def connect(_sock: object, _address: tuple[str, int]) -> None:
        await asyncio.sleep(delay_s)  # the connect completes but the loop is late

    return connect


def _failing_connect(error: OSError) -> ConnectFn:
    async def connect(_sock: object, _address: tuple[str, int]) -> None:
        raise error

    return connect


async def test_tcp_probe_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = FakeTcpSocket()
    addresses: list[tuple[str, int]] = []

    async def connect(_sock: object, address: tuple[str, int]) -> None:
        addresses.append(address)

    _install_tcp_connect(monkeypatch, sock, connect)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None
    assert addresses == [("127.0.0.1", 443)]
    assert sock.closed


class _RecordingSocket:
    opened: list[tuple[int, int]] = []

    def __init__(self, family: int, kind: int) -> None:
        self.opened.append((family, kind))
        self.blocking: bool | None = None
        self.closed = False

    def setblocking(self, flag: bool) -> None:
        self.blocking = flag

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize(
    ("address", "family"),
    [
        ("192.0.2.1", socket.AF_INET),
        ("2001:db8::1", socket.AF_INET6),
        ("fe80::1%eth0", socket.AF_INET6),
    ],
    ids=["ipv4", "ipv6", "ipv6-scoped"],
)
def test_open_tcp_socket_uses_the_address_family(
    monkeypatch: pytest.MonkeyPatch, address: str, family: int
) -> None:
    monkeypatch.setattr(_RecordingSocket, "opened", [])
    monkeypatch.setattr(probes.socket, "socket", _RecordingSocket)

    sock = probes._open_tcp_socket(address)

    assert _RecordingSocket.opened == [(family, socket.SOCK_STREAM)]
    assert isinstance(sock, _RecordingSocket)
    assert sock.blocking is False


def test_open_tcp_socket_closes_a_socket_it_cannot_make_non_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made: list[_RecordingSocket] = []

    class StuckSocket(_RecordingSocket):
        def setblocking(self, _flag: bool) -> None:
            made.append(self)
            raise OSError(errno.EBADF, "Bad file descriptor")

    monkeypatch.setattr(probes.socket, "socket", StuckSocket)

    with pytest.raises(OSError, match="Bad file descriptor"):
        probes._open_tcp_socket("192.0.2.1")
    assert len(made) == 1 and made[0].closed


@pytest.mark.parametrize(
    "connect",
    [
        _failing_connect(ConnectionRefusedError("connection refused")),
        _failing_connect(OSError(errno.EHOSTUNREACH, "unreachable")),
        _slow_connect(1.0),
    ],
    ids=["refused", "unreachable", "timeout"],
)
async def test_tcp_probe_closes_its_socket_when_the_connect_fails(
    monkeypatch: pytest.MonkeyPatch, connect: ConnectFn
) -> None:
    sock = FakeTcpSocket()
    _install_tcp_connect(monkeypatch, sock, connect)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=0.05)

    assert sample.status != SampleStatus.OK
    assert sock.closed


async def test_tcp_probe_cancelled_during_connect_closes_its_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sock = FakeTcpSocket()
    connect_started = asyncio.Event()

    async def hanging_connect(_sock: object, _address: tuple[str, int]) -> None:
        connect_started.set()
        await asyncio.Event().wait()

    _install_tcp_connect(monkeypatch, sock, hanging_connect)

    probe = asyncio.create_task(tcp_probe("127.0.0.1", 443, timeout_s=10.0))
    await connect_started.wait()
    probe.cancel()

    with pytest.raises(asyncio.CancelledError):
        await probe
    assert sock.closed


async def test_tcp_probe_reports_kernel_handshake_rtt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    sock = FakeTcpSocket(tcp_info=_tcp_info(rtt_us=1500, total_retrans=0))
    _install_tcp_connect(monkeypatch, sock, _slow_connect())

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms == 1.5


@pytest.mark.parametrize(
    "tcp_info",
    [
        _tcp_info(rtt_us=1500, total_retrans=1),
        _tcp_info(rtt_us=0, total_retrans=0),
        b"",
    ],
    ids=["syn-retransmitted", "no-rtt-sample", "short-tcp-info"],
)
async def test_tcp_probe_keeps_connect_time_when_kernel_rtt_would_hide_it(
    monkeypatch: pytest.MonkeyPatch, tcp_info: bytes
) -> None:
    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    _install_tcp_connect(monkeypatch, FakeTcpSocket(tcp_info=tcp_info), _slow_connect())

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None and sample.latency_ms >= 45


async def test_tcp_probe_ignores_tcp_info_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    _install_tcp_connect(monkeypatch, FakeTcpSocket(tcp_info=None), _connect_at_once)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None


async def _accept_and_close(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.close()


async def test_tcp_probe_real_loopback_connect() -> None:
    server = await asyncio.start_server(_accept_and_close, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        sample = await tcp_probe("127.0.0.1", port, timeout_s=2.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None and 0 <= sample.latency_ms < 1000


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="TCP_INFO is read on Linux")
async def test_tcp_probe_real_loopback_excludes_event_loop_stall() -> None:
    server = await asyncio.start_server(_accept_and_close, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        probe = asyncio.create_task(tcp_probe("127.0.0.1", port, timeout_s=2.0))
        await asyncio.sleep(0)
        time.sleep(0.05)  # the loop stalls while the connect is in flight
        sample = await probe

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None and sample.latency_ms < 25


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="TCP_INFO is read on Linux")
async def test_tcp_probe_real_loopback_keeps_a_connect_finished_before_a_stalled_deadline() -> None:
    server = await asyncio.start_server(_accept_and_close, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        probe = asyncio.create_task(tcp_probe("127.0.0.1", port, timeout_s=0.05))
        await asyncio.sleep(0)
        # The handshake finishes at once, but the loop stalls past the probe's
        # deadline before it can see that.
        time.sleep(0.2)
        sample = await probe

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None and sample.latency_ms < 25


@pytest.mark.parametrize(
    ("tcp_info_option", "sock"),
    [
        (11, FakeTcpSocket(tcp_info=_tcp_info(rtt_us=1500, total_retrans=0))),
        (11, FakeTcpSocket(tcp_info=_tcp_info(rtt_us=60_000, total_retrans=0), peer=PEER)),
        (11, FakeTcpSocket(tcp_info=_tcp_info(rtt_us=1500, total_retrans=1), peer=PEER)),
        (None, FakeTcpSocket(tcp_info=_tcp_info(rtt_us=1500, total_retrans=0), peer=PEER)),
    ],
    ids=["still-connecting", "answered-after-timeout", "syn-retransmitted", "no-tcp-info"],
)
async def test_tcp_probe_keeps_the_timeout_unless_the_kernel_connected_in_time(
    monkeypatch: pytest.MonkeyPatch, tcp_info_option: int | None, sock: FakeTcpSocket
) -> None:
    monkeypatch.setattr(probes, "_TCP_INFO", tcp_info_option)
    _install_tcp_connect(monkeypatch, sock, _slow_connect(1.0))

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=0.05)

    assert sample.status == SampleStatus.TIMEOUT
    assert sample.latency_ms is None
    assert sock.closed


async def test_tcp_probe_reports_a_connect_the_kernel_finished_in_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The deadline won over a connect that the kernel had finished in time.
    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    sock = FakeTcpSocket(tcp_info=_tcp_info(rtt_us=1500, total_retrans=0), peer=PEER)
    _install_tcp_connect(monkeypatch, sock, _slow_connect(1.0))

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=0.05)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms == 1.5
    assert sock.closed


async def test_tcp_probe_reports_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_tcp_connect(
        monkeypatch, FakeTcpSocket(), _failing_connect(ConnectionRefusedError("connection refused"))
    )

    sample = await tcp_probe("127.0.0.1", 1, timeout_s=1.0)

    assert sample.status == SampleStatus.REFUSED
    assert sample.error is not None


async def test_tcp_probe_real_loopback_reports_refused() -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()  # nothing listens on the port now

    sample = await tcp_probe("127.0.0.1", port, timeout_s=2.0)

    assert sample.status == SampleStatus.REFUSED
    assert sample.error


@pytest.mark.parametrize(
    "error_number",
    [
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EHOSTDOWN,
        errno.ENETDOWN,
    ],
)
async def test_tcp_probe_reports_platform_unreachable_errno(
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    _install_tcp_connect(
        monkeypatch, FakeTcpSocket(), _failing_connect(OSError(error_number, "unreachable"))
    )

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.UNREACHABLE


async def test_tcp_probe_reports_unknown_oserror_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_tcp_connect(
        monkeypatch,
        FakeTcpSocket(),
        _failing_connect(OSError(errno.EPERM, "not an unreachable network")),
    )

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.ERROR


async def test_tcp_probe_reports_a_socket_that_cannot_open_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_socket(_address: str) -> FakeTcpSocket:
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(probes, "_open_tcp_socket", no_socket)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.ERROR
    assert sample.error is not None and "Too many open files" in sample.error


async def test_resolve_target_numeric_rejects_hostname() -> None:
    resolved = await resolve_target("example.com", AddressFamily.AUTO, numeric=True)

    assert resolved.address is None
    assert resolved.error == "--numeric requires an IP literal"


async def test_resolve_target_returns_dns_error_when_no_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_, **__: [])

    resolved = await resolve_target("example.com", AddressFamily.AUTO)

    assert resolved.address is None
    assert resolved.error == "getaddrinfo: no addresses returned"


async def test_resolve_target_preserves_all_getaddrinfo_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [
            (socket.AF_INET6, 0, 0, "", ("2001:db8::1", 0)),
            (socket.AF_INET, 0, 0, "", ("192.0.2.10", 0)),
            (socket.AF_INET, 0, 0, "", ("192.0.2.10", 0)),
        ],
    )

    resolved = await resolve_target("service.example", AddressFamily.AUTO)

    assert isinstance(resolved, ResolvedTarget)
    assert resolved.address == "192.0.2.10"
    assert resolved.addresses == ("192.0.2.10", "2001:db8::1")
    assert resolved.family == AddressFamily.IPV4


class _StubEngine:
    def __init__(self, result: icmp.EchoResult) -> None:
        self.result = result
        self.calls: list[tuple[str, bool, float]] = []

    async def echo(self, destination: str, *, ipv6: bool, timeout_s: float) -> icmp.EchoResult:
        self.calls.append((destination, ipv6, timeout_s))
        return self.result


def _install_engine(monkeypatch: pytest.MonkeyPatch, engine: object) -> None:
    monkeypatch.setattr(probes, "engine_for_running_loop", lambda: engine)


@pytest.mark.parametrize(
    "result",
    [
        icmp.EchoResult(SampleStatus.OK, 0.42),
        icmp.EchoResult(SampleStatus.TIMEOUT),
        icmp.EchoResult(SampleStatus.UNREACHABLE, error="Destination host unreachable"),
    ],
    ids=["ok", "timeout", "unreachable"],
)
async def test_icmp_probe_reports_engine_outcome(
    monkeypatch: pytest.MonkeyPatch, result: icmp.EchoResult
) -> None:
    _install_engine(monkeypatch, _StubEngine(result))

    sample = await icmp_probe("192.0.2.1", timeout_s=1.0, address_family=AddressFamily.IPV4)

    assert (sample.status, sample.latency_ms, sample.error) == result


@pytest.mark.parametrize(
    ("address", "sent_to", "ipv6"),
    [
        ("192.0.2.1", "192.0.2.1", False),
        ("2606:4700:4700::1111", "2606:4700:4700::1111", True),
        ("fe80::1%eth0", "fe80::1%eth0", True),
        ("::ffff:192.0.2.1", "192.0.2.1", False),
    ],
)
async def test_icmp_probe_selects_socket_family_from_the_address(
    monkeypatch: pytest.MonkeyPatch, address: str, sent_to: str, ipv6: bool
) -> None:
    engine = _StubEngine(icmp.EchoResult(SampleStatus.OK, 1.0))
    _install_engine(monkeypatch, engine)

    await icmp_probe(address, timeout_s=2.5, address_family=AddressFamily.AUTO)

    assert engine.calls == [(sent_to, ipv6, 2.5)]


async def test_icmp_probe_rejects_a_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = _StubEngine(icmp.EchoResult(SampleStatus.OK, 1.0))
    _install_engine(monkeypatch, engine)

    sample = await icmp_probe("example.com", timeout_s=1.0, address_family=AddressFamily.AUTO)

    assert sample.status == SampleStatus.ERROR
    assert sample.error == "ICMP probes need an IP address, not 'example.com'"
    assert engine.calls == []


async def test_icmp_probe_reports_error_when_engine_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_engine() -> None:
        raise RuntimeError("no engine")

    monkeypatch.setattr(probes, "engine_for_running_loop", broken_engine)

    sample = await icmp_probe("192.0.2.1", timeout_s=1.0, address_family=AddressFamily.IPV4)

    assert sample.status == SampleStatus.ERROR
    assert sample.error == "ICMP engine unavailable: no engine"


async def test_icmp_probe_socket_permission_error_marks_target_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def blocked_socket(*_: object) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    engine = icmp.IcmpEngine(asyncio.get_running_loop(), socket_factory=blocked_socket)
    _install_engine(monkeypatch, engine)

    sample = await icmp_probe("192.0.2.1", timeout_s=1.0, address_family=AddressFamily.IPV4)
    target = TargetRun(target="192.0.2.1")
    target.apply_sample(sample, fail_threshold=3, jitter_threshold_ms=50.0)

    assert sample.status == SampleStatus.ERROR
    assert (sample.error or "").startswith("permission denied: ")
    assert target.status == TargetStatus.PERMISSION_DENIED


async def test_icmp_probe_real_loopback_reports_success() -> None:
    try:
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP).close()
    except OSError as exc:
        pytest.skip(f"unprivileged ICMP sockets are not available: {exc}")

    sample = await icmp_probe("127.0.0.1", timeout_s=2.0, address_family=AddressFamily.AUTO)

    assert sample.status == SampleStatus.OK, sample.error
    assert sample.latency_ms is not None and sample.latency_ms >= 0


async def test_resolve_target_caps_failover_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [
            (socket.AF_INET, 0, 0, "", (f"192.0.2.{index}", 0)) for index in range(20)
        ],
    )

    resolved = await resolve_target("many.example", AddressFamily.AUTO)

    assert resolved.address == "192.0.2.0"
    assert len(resolved.addresses) == probes.MAX_FAILOVER_ADDRESSES
    assert resolved.addresses == tuple(f"192.0.2.{index}" for index in range(8))


async def test_resolve_target_stuck_resolver_is_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def blocking_getaddrinfo(*_: object, **__: object) -> list[object]:
        release.wait(5.0)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", blocking_getaddrinfo)

    # The daemon-thread lookup must be abandonable: wait_for times out even
    # though the resolver call is still blocked in its thread.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(resolve_target("stuck.example", AddressFamily.AUTO), timeout=0.05)
    release.set()


def _answer(address: str, family: int = socket.AF_INET) -> tuple[Any, ...]:
    return (family, socket.SOCK_STREAM, 6, "", (address, 0))


class _HangingResolver:
    """getaddrinfo stand-in: names starting with "hang" block until released."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.release = threading.Event()

    def __call__(self, host: str, *_: object, **__: object) -> list[Any]:
        self.calls.append(host)
        if host.startswith("hang") and not self.release.wait(5.0):
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return [_answer("192.0.2.99" if host.startswith("hang") else "192.0.2.1")]


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        assert loop.time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


async def test_resolver_thread_start_failure_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(probes, "_dns_thread_slots", slots)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_, **__: [_answer("192.0.2.30")])
    real_thread = threading.Thread

    class FailingThread:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading, "Thread", FailingThread)
    monkeypatch.setattr(probes, "DNS_THREAD_WAIT_SECONDS", 0.2)
    loop = asyncio.get_running_loop()
    started = loop.time()
    # A thread limit (pids cap, RLIMIT_NPROC) is local capacity, not a DNS verdict.
    with pytest.raises(probes.ResolverBusyError, match="can't start new thread") as raised:
        await resolve_target("start-failure.example", AddressFamily.AUTO)

    # Reported only after the same wait as a lookup that found every thread
    # busy, so callers that retry at once cannot flood thread starts.
    assert loop.time() - started >= 0.15
    assert isinstance(raised.value.__cause__, RuntimeError)
    # The slot is free during that wait, not just after it.
    assert slots.acquire(blocking=False)
    slots.release()

    # The failed lookup also left the registry, so a retry starts a fresh one
    # instead of joining a call that never ran.
    monkeypatch.setattr(threading, "Thread", real_thread)
    resolved = await asyncio.wait_for(
        resolve_target("start-failure.example", AddressFamily.AUTO), timeout=2.0
    )
    assert resolved.address == "192.0.2.30"


async def test_resolver_error_reaches_every_caller_joined_to_the_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def failing_getaddrinfo(host: str, *_: object, **__: object) -> list[Any]:
        calls.append(host)
        started.set()
        release.wait(5.0)
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    slots = threading.BoundedSemaphore(2)
    monkeypatch.setattr(probes, "_dns_thread_slots", slots)
    monkeypatch.setattr(socket, "getaddrinfo", failing_getaddrinfo)

    try:
        first = asyncio.ensure_future(resolve_target("fail-join.example", AddressFamily.AUTO))
        await _wait_until(started.is_set)
        second = asyncio.ensure_future(resolve_target("fail-join.example", AddressFamily.AUTO))
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second), timeout=2.0)
    finally:
        release.set()

    assert [result.error for result in results] == [
        "getaddrinfo: Temporary failure in name resolution"
    ] * 2
    assert all(result.address is None for result in results)
    assert calls == ["fail-join.example"]
    assert ("fail-join.example", socket.AF_UNSPEC) not in probes._dns_lookups
    for _ in range(2):
        assert slots.acquire(blocking=False)


async def test_late_answers_stay_bounded_and_evict_the_oldest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"late-{index}.bounded.example" for index in range(5)]
    releases = {name: threading.Event() for name in names}

    def slow_getaddrinfo(host: str, *_: object, **__: object) -> list[Any]:
        releases[host].wait(5.0)
        return [_answer("192.0.2.40")]

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    monkeypatch.setattr(probes, "MAX_DNS_DAEMON_THREADS", 3)
    monkeypatch.setattr(probes, "_dns_late_answers", {})
    keys = [(name, socket.AF_UNSPEC) for name in names]

    try:
        for name in names:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(resolve_target(name, AddressFamily.AUTO), timeout=0.05)
        # Every caller gave up; answers then land one at a time, oldest first.
        for key in keys:
            releases[key[0]].set()
            await _wait_until(lambda key=key: key in probes._dns_late_answers)
            assert len(probes._dns_late_answers) <= 3
    finally:
        for release in releases.values():
            release.set()

    assert list(probes._dns_late_answers) == keys[2:]


async def test_resolve_target_joins_running_lookup_of_the_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _HangingResolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    try:
        for _ in range(3):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    resolve_target("hang-join.example", AddressFamily.AUTO), timeout=0.05
                )
        # Retries waited on the abandoned call instead of each starting a thread.
        assert resolver.calls == ["hang-join.example"]

        retry = asyncio.ensure_future(resolve_target("hang-join.example", AddressFamily.AUTO))
        await asyncio.sleep(0.05)
        resolver.release.set()
        resolved = await asyncio.wait_for(retry, timeout=2.0)
    finally:
        resolver.release.set()

    assert resolved.address == "192.0.2.99"
    assert resolver.calls == ["hang-join.example"]


async def test_resolve_target_uses_late_answer_of_abandoned_lookup_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _HangingResolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    key = ("hang-late.example", socket.AF_UNSPEC)

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                resolve_target("hang-late.example", AddressFamily.AUTO), timeout=0.05
            )
        resolver.release.set()
        await _wait_until(lambda: key in probes._dns_late_answers)

        late = await resolve_target("hang-late.example", AddressFamily.AUTO)
        assert late.address == "192.0.2.99"
        assert resolver.calls == ["hang-late.example"]

        fresh = await resolve_target("hang-late.example", AddressFamily.AUTO)
    finally:
        resolver.release.set()

    assert fresh.address == "192.0.2.99"
    assert resolver.calls == ["hang-late.example", "hang-late.example"]


async def test_resolve_target_ignores_expired_late_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _HangingResolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(probes, "_DNS_LATE_ANSWER_MAX_AGE_SECONDS", -1.0)
    key = ("hang-expired.example", socket.AF_UNSPEC)

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                resolve_target("hang-expired.example", AddressFamily.AUTO), timeout=0.05
            )
        resolver.release.set()
        await _wait_until(lambda: key in probes._dns_late_answers)

        await resolve_target("hang-expired.example", AddressFamily.AUTO)
    finally:
        resolver.release.set()

    assert resolver.calls == ["hang-expired.example", "hang-expired.example"]
    assert key not in probes._dns_late_answers


async def test_late_answer_keeps_only_the_entries_resolve_target_uses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    answers = {
        "hang-huge.example": [_answer("0.0.0.0")] * 1_000
        + [_answer(f"192.0.2.{index}") for index in range(1, 201)],
        "hang-sinkhole.example": [_answer("0.0.0.0")] * 1_000,
    }

    def slow_getaddrinfo(host: str, *_: object, **__: object) -> list[Any]:
        release.wait(5.0)
        return answers[host]

    monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)
    keys = [(name, socket.AF_UNSPEC) for name in answers]

    try:
        for name in answers:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(resolve_target(name, AddressFamily.AUTO), timeout=0.05)
        release.set()
        await _wait_until(lambda: all(key in probes._dns_late_answers for key in keys))
    finally:
        release.set()

    # A huge abandoned answer pins at most the failover set, not the whole answer.
    assert len(probes._dns_late_answers[keys[0]][1]) == probes.MAX_FAILOVER_ADDRESSES
    assert len(probes._dns_late_answers[keys[1]][1]) == 1
    huge = await resolve_target("hang-huge.example", AddressFamily.AUTO)
    sinkhole = await resolve_target("hang-sinkhole.example", AddressFamily.AUTO)

    assert huge.addresses == tuple(f"192.0.2.{index}" for index in range(1, 9))
    assert sinkhole.address is None
    assert sinkhole.error is not None and "only unspecified addresses" in sinkhole.error


async def test_resolver_capacity_exhaustion_waits_instead_of_failing_the_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _HangingResolver()
    monkeypatch.setattr(socket, "getaddrinfo", resolver)
    monkeypatch.setattr(probes, "_dns_thread_slots", threading.BoundedSemaphore(2))
    monkeypatch.setattr(probes, "DNS_THREAD_WAIT_SECONDS", 0.2)
    loop = asyncio.get_running_loop()

    try:
        for index in range(2):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    resolve_target(f"hang-capacity{index}.example", AddressFamily.AUTO),
                    timeout=0.05,
                )

        # Both threads are still stuck: the next lookup waits for one and then
        # reports local capacity, never a DNS error or a third thread.
        started = loop.time()
        with pytest.raises(probes.ResolverBusyError):
            await resolve_target("ok-capacity.example", AddressFamily.AUTO)
        assert loop.time() - started >= 0.2
        assert resolver.calls == ["hang-capacity0.example", "hang-capacity1.example"]

        monkeypatch.setattr(probes, "DNS_THREAD_WAIT_SECONDS", 2.0)
        waiting = asyncio.ensure_future(resolve_target("ok-capacity.example", AddressFamily.AUTO))
        await asyncio.sleep(0.05)
        assert not waiting.done()
        resolver.release.set()
        resolved = await asyncio.wait_for(waiting, timeout=2.0)
    finally:
        resolver.release.set()

    assert resolved.error is None
    assert resolved.address == "192.0.2.1"


async def test_resolve_target_drops_unspecified_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [
            _answer("0.0.0.0"),
            _answer("::", socket.AF_INET6),
            _answer("::ffff:0.0.0.0", socket.AF_INET6),
            _answer("2001:db8::7", socket.AF_INET6),
            _answer("192.0.2.7"),
        ],
    )

    resolved = await resolve_target("partly-filtered.example", AddressFamily.AUTO)

    assert resolved.error is None
    assert resolved.address == "192.0.2.7"
    assert resolved.family == AddressFamily.IPV4
    assert resolved.addresses == ("192.0.2.7", "2001:db8::7")


async def test_resolve_target_treats_sinkhole_answer_as_dns_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DNS filters answer blocked names with 0.0.0.0 / ::. Probing those reaches
    # the local host on Linux and would report a dead name healthy.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [_answer("0.0.0.0"), _answer("::", socket.AF_INET6)],
    )

    resolved = await resolve_target("blocked.example", AddressFamily.AUTO)

    assert resolved.address is None
    assert resolved.addresses == ()
    assert resolved.error is not None
    assert "only unspecified addresses" in resolved.error


async def test_resolve_target_keeps_explicit_unspecified_literal() -> None:
    resolved = await resolve_target("0.0.0.0", AddressFamily.AUTO)

    assert resolved.error is None
    assert resolved.address == "0.0.0.0"


async def test_resolve_target_stops_reading_a_huge_answer_at_the_failover_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumed = 0

    class CountingAnswer(list[Any]):
        def __iter__(self) -> Iterator[Any]:
            nonlocal consumed
            for item in super().__iter__():
                consumed += 1
                yield item

    answer = CountingAnswer(
        _answer(f"10.{index // 65536}.{index // 256 % 256}.{index % 256}")
        for index in range(100_000)
    )
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_, **__: answer)

    resolved = await resolve_target("huge.example", AddressFamily.AUTO)

    assert len(resolved.addresses) == probes.MAX_FAILOVER_ADDRESSES
    assert consumed == probes.MAX_FAILOVER_ADDRESSES


async def test_resolve_target_checks_each_unique_address_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = (
        [_answer("2001:db8::1", socket.AF_INET6)] * 50_000
        + [_answer("0.0.0.0")] * 50_000
        + [_answer("192.0.2.1")]
    )
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_, **__: answer)
    checked: list[str] = []
    real_is_unspecified = probes._is_unspecified

    def counting_is_unspecified(address: str) -> bool:
        checked.append(address)
        return real_is_unspecified(address)

    monkeypatch.setattr(probes, "_is_unspecified", counting_is_unspecified)

    resolved = await resolve_target("duplicates.example", AddressFamily.AUTO)

    assert resolved.addresses == ("192.0.2.1", "2001:db8::1")
    assert checked == ["2001:db8::1", "0.0.0.0", "192.0.2.1"]
