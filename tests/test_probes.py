import asyncio
import errno
import socket
import threading
from typing import Any

import pytest
from icmplib.exceptions import (
    DestinationUnreachable,
    ICMPLibError,
    ICMPv4DestinationUnreachable,
    SocketPermissionError,
    TimeExceeded,
    TimeoutExceeded,
)

import pinghue.probes as probes
from pinghue.models import AddressFamily, SampleStatus
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


class _FakeReply:
    def __init__(
        self, *, time: float = 1000.0042, code: int = 1, unreachable: bool = False
    ) -> None:
        self.time = time
        self.code = code
        self._unreachable = unreachable

    def raise_for_status(self) -> None:
        if self._unreachable:
            raise ICMPv4DestinationUnreachable(self)


class _FakeRequest:
    def __init__(self, *, destination: str, id: int, sequence: int) -> None:
        self.destination = destination
        self.id = id
        self.sequence = sequence
        self.time = 1000.0


class _FakeSocket:
    def __init__(self, *, reply: object = None, receive_error: BaseException | None = None) -> None:
        self._reply = reply
        self._receive_error = receive_error

    def __enter__(self) -> "_FakeSocket":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def send(self, request: object) -> None:
        self.sent = request

    def receive(self, _request: object, _timeout: float) -> object:
        if self._receive_error is not None:
            raise self._receive_error
        return self._reply


def _make_backend(*, v4_factory: Any, v6_factory: Any) -> probes._IcmpBackend:
    return probes._IcmpBackend(
        socket_v4=v4_factory,
        socket_v6=v6_factory,
        request=_FakeRequest,
        unique_identifier=lambda: 1,
        timeout_error=TimeoutExceeded,
        permission_error=SocketPermissionError,
        unreachable_errors=(DestinationUnreachable, TimeExceeded),
        library_error=ICMPLibError,
    )


class _FakeLoop:
    def __init__(self) -> None:
        self.executor: object = None

    def run_in_executor(self, executor: object, function: Any) -> "asyncio.Future[object]":
        self.executor = executor
        future: asyncio.Future[object] = asyncio.Future()
        try:
            future.set_result(function())
        except BaseException as exc:  # noqa: BLE001 - mirror executor propagation
            future.set_exception(exc)
        return future


def _install_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend: probes._IcmpBackend,
    *,
    install_executor_loop: bool = False,
) -> _FakeLoop:
    loop = _FakeLoop()
    monkeypatch.setattr(probes, "_icmp_backend", backend, raising=False)
    monkeypatch.setattr(probes, "_icmp_import_error", None, raising=False)
    if install_executor_loop:
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: loop)
    return loop


async def test_icmp_probe_reports_success_via_supplied_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = object()
    backend = _make_backend(
        v4_factory=lambda *_: _FakeSocket(reply=_FakeReply(time=1000.0042)),
        v6_factory=lambda *_: pytest.fail("IPv4 address must use the IPv4 socket"),
    )
    loop = _install_backend(monkeypatch, backend, install_executor_loop=True)

    sample = await icmp_probe(
        "1.1.1.1",
        timeout_s=1.0,
        address_family=AddressFamily.IPV4,
        executor=executor,
    )

    assert sample.status == SampleStatus.OK
    assert sample.latency_ms == 4.2
    assert loop.executor is executor


async def test_icmp_probe_runs_cancellable_blocking_echo_on_daemon_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend(
        v4_factory=lambda *_: pytest.fail("blocking echo is replaced below"),
        v6_factory=lambda *_: pytest.fail("blocking echo is replaced below"),
    )
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    worker_is_daemon: list[bool] = []

    def blocking_echo(*_: object, **__: object) -> float:
        worker_is_daemon.append(threading.current_thread().daemon)
        started.set()
        try:
            release.wait(timeout=1.0)
            return 1.0
        finally:
            finished.set()

    monkeypatch.setattr(probes, "_get_icmp_backend", lambda: backend)
    monkeypatch.setattr(probes, "_run_icmp_echo", blocking_echo)

    task = asyncio.create_task(
        icmp_probe("1.1.1.1", timeout_s=1.0, address_family=AddressFamily.IPV4)
    )
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()

    assert finished.wait(timeout=1.0)
    assert worker_is_daemon == [True]


async def test_daemon_worker_pool_reuses_an_idle_thread() -> None:
    pool = probes._DaemonWorkerPool(max_workers=2, name="pinghue-test")
    worker_ids: list[int | None] = []

    def record_worker() -> None:
        worker_ids.append(threading.current_thread().ident)

    await pool.run(record_worker)
    await pool.run(record_worker)

    assert len(set(worker_ids)) == 1


async def test_daemon_worker_pool_runs_concurrent_submissions_concurrently() -> None:
    pool = probes._DaemonWorkerPool(max_workers=2, name="pinghue-test")
    lock = threading.Lock()
    both_started = threading.Event()
    started = 0

    def wait_for_peer() -> bool:
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                both_started.set()
        return both_started.wait(timeout=0.25)

    results = await asyncio.gather(pool.run(wait_for_peer), pool.run(wait_for_peer))

    assert results == [True, True]


async def test_daemon_worker_pool_recovers_capacity_after_thread_start_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = probes._DaemonWorkerPool(max_workers=1, name="pinghue-test")
    real_thread = threading.Thread

    class FailingThread:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread creation failed")

    monkeypatch.setattr(threading, "Thread", FailingThread)
    with pytest.raises(RuntimeError, match="thread creation failed"):
        await pool.run(lambda: 1)

    monkeypatch.setattr(threading, "Thread", real_thread)
    assert await pool.run(lambda: 2) == 2


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


async def test_icmp_probe_reports_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _make_backend(
        v4_factory=lambda *_: _FakeSocket(receive_error=TimeoutExceeded(1.0)),
        v6_factory=lambda *_: _FakeSocket(receive_error=TimeoutExceeded(1.0)),
    )
    _install_backend(monkeypatch, backend)

    sample = await icmp_probe("1.1.1.1", timeout_s=1.0, address_family=AddressFamily.IPV4)

    assert sample.status == SampleStatus.TIMEOUT


async def test_icmp_probe_distinguishes_unreachable_from_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # M2: a destination-unreachable reply must classify UNREACHABLE, not TIMEOUT.
    backend = _make_backend(
        v4_factory=lambda *_: _FakeSocket(reply=_FakeReply(unreachable=True, code=1)),
        v6_factory=lambda *_: _FakeSocket(reply=_FakeReply(unreachable=True, code=1)),
    )
    _install_backend(monkeypatch, backend)

    sample = await icmp_probe("192.0.2.1", timeout_s=1.0, address_family=AddressFamily.IPV4)

    assert sample.status == SampleStatus.UNREACHABLE
    assert sample.error


async def test_icmp_probe_selects_socket_by_address_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    used: list[str] = []

    def v4(*_: object) -> _FakeSocket:
        used.append("v4")
        return _FakeSocket(reply=_FakeReply())

    def v6(*_: object) -> _FakeSocket:
        used.append("v6")
        return _FakeSocket(reply=_FakeReply())

    backend = _make_backend(v4_factory=v4, v6_factory=v6)
    _install_backend(monkeypatch, backend)

    await icmp_probe("2606:4700:4700::1111", timeout_s=1.0, address_family=AddressFamily.IPV6)

    assert used == ["v6"]


async def test_icmp_probe_reports_error_when_icmplib_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(probes, "_icmp_backend", None, raising=False)
    monkeypatch.setattr(probes, "_icmp_import_error", ImportError("no icmplib"), raising=False)

    sample = await icmp_probe("1.1.1.1", timeout_s=1.0, address_family=AddressFamily.IPV4)

    assert sample.status == SampleStatus.ERROR
    assert "no icmplib" in (sample.error or "")


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
        await asyncio.wait_for(
            resolve_target("stuck.example", AddressFamily.AUTO), timeout=0.05
        )
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
