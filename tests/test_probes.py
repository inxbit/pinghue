import asyncio
import errno
import socket
import struct
import sys
import threading
import time
from typing import Any

import pytest

import pinghue.probes as probes
from pinghue import icmp
from pinghue.models import AddressFamily, SampleStatus, TargetRun, TargetStatus
from pinghue.probes import ResolvedTarget, icmp_probe, resolve_target, tcp_probe


class FakeWriter:
    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


async def test_tcp_probe_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        return object(), FakeWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None


async def test_tcp_probe_reports_success_when_connection_reset_during_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResettingWriter(FakeWriter):
        async def wait_closed(self) -> None:
            raise ConnectionResetError("peer reset during close")

    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        return object(), ResettingWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None


async def test_tcp_probe_preserves_success_when_cancelled_during_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_started = asyncio.Event()

    class BlockingWriter(FakeWriter):
        async def wait_closed(self) -> None:
            close_started.set()
            await asyncio.Event().wait()

    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        return object(), BlockingWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    probe = asyncio.create_task(tcp_probe("127.0.0.1", 443, timeout_s=1.0))
    await close_started.wait()
    probe.cancel()
    sample = await probe

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None


class _TcpInfoSocket:
    def __init__(self, *, rtt_us: int, total_retrans: int) -> None:
        self.info = bytearray(104)
        struct.pack_into("=I", self.info, 68, rtt_us)
        struct.pack_into("=I", self.info, 100, total_retrans)

    def getsockopt(self, level: int, option: int, buflen: int) -> bytes:
        assert (level, option, buflen) == (socket.IPPROTO_TCP, 11, 104)
        return bytes(self.info)


class _TcpInfoWriter(FakeWriter):
    def __init__(self, sock: object) -> None:
        self._sock = sock

    def get_extra_info(self, name: str, default: object = None) -> object:
        return self._sock if name == "socket" else default


def _slow_connect(writer: FakeWriter, delay_s: float = 0.05) -> Any:
    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        await asyncio.sleep(delay_s)  # the connect completes but the loop is late
        return object(), writer

    return open_connection


async def test_tcp_probe_reports_kernel_handshake_rtt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    writer = _TcpInfoWriter(_TcpInfoSocket(rtt_us=1500, total_retrans=0))
    monkeypatch.setattr(asyncio, "open_connection", _slow_connect(writer))

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms == 1.5


@pytest.mark.parametrize(
    "sock",
    [
        _TcpInfoSocket(rtt_us=1500, total_retrans=1),
        _TcpInfoSocket(rtt_us=0, total_retrans=0),
        None,
    ],
    ids=["syn-retransmitted", "no-rtt-sample", "no-socket"],
)
async def test_tcp_probe_keeps_connect_time_when_kernel_rtt_would_hide_it(
    monkeypatch: pytest.MonkeyPatch, sock: object
) -> None:
    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    monkeypatch.setattr(asyncio, "open_connection", _slow_connect(_TcpInfoWriter(sock)))

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None and sample.latency_ms >= 45


async def test_tcp_probe_ignores_tcp_info_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingSocket:
        def getsockopt(self, *_: object) -> bytes:
            raise OSError(errno.ENOPROTOOPT, "Protocol not available")

    monkeypatch.setattr(probes, "_TCP_INFO", 11)
    monkeypatch.setattr(
        asyncio, "open_connection", _slow_connect(_TcpInfoWriter(FailingSocket()), 0.0)
    )

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms is not None


async def test_tcp_probe_bounds_slow_teardown(monkeypatch: pytest.MonkeyPatch) -> None:
    class StuckWriter(FakeWriter):
        async def wait_closed(self) -> None:
            await asyncio.Event().wait()

    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        return object(), StuckWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    monkeypatch.setattr(probes, "TCP_CLOSE_TIMEOUT_SECONDS", 0.05)

    probe = asyncio.ensure_future(tcp_probe("127.0.0.1", 443, timeout_s=1.0))
    done, _ = await asyncio.wait({probe}, timeout=2.0)

    assert probe in done
    sample = probe.result()
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


async def test_tcp_probe_reports_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    sample = await tcp_probe("127.0.0.1", 1, timeout_s=1.0)

    assert sample.status == SampleStatus.REFUSED
    assert sample.error is not None


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
    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        raise OSError(error_number, "unreachable")

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.UNREACHABLE


async def test_tcp_probe_reports_unknown_oserror_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def open_connection(*_: object) -> tuple[object, FakeWriter]:
        raise OSError(errno.EPERM, "not an unreachable network")

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    sample = await tcp_probe("127.0.0.1", 443, timeout_s=1.0)

    assert sample.status == SampleStatus.ERROR


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


async def test_daemon_thread_start_failure_releases_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = threading.BoundedSemaphore(1)

    class FailingThread:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread creation failed")

    monkeypatch.setattr(threading, "Thread", FailingThread)

    with pytest.raises(RuntimeError, match="thread creation failed"):
        await probes._run_daemon_thread(
            lambda: None,
            name="pinghue-test",
            slots=slots,
            unavailable_error="no worker available",
        )

    assert slots.acquire(blocking=False)
    slots.release()


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


async def test_resolve_target_bounds_abandoned_resolver_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    monkeypatch.setattr(probes, "_dns_thread_slots", threading.BoundedSemaphore(2))

    def blocking_getaddrinfo(*_: object, **__: object) -> list[object]:
        release.wait(5.0)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", blocking_getaddrinfo)

    try:
        for index in range(2):
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    resolve_target(f"stuck{index}.example", AddressFamily.AUTO),
                    timeout=0.05,
                )

        resolved = await resolve_target("stuck2.example", AddressFamily.AUTO)

        assert resolved.address is None
        assert resolved.error is not None
        assert "resolver worker limit reached" in resolved.error
    finally:
        release.set()
