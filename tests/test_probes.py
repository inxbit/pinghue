import asyncio
import errno
import socket
import threading
from collections.abc import Callable, Iterator
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


def _install_backend(monkeypatch: pytest.MonkeyPatch, backend: probes._IcmpBackend) -> None:
    monkeypatch.setattr(probes, "_icmp_backend", backend, raising=False)
    monkeypatch.setattr(probes, "_icmp_import_error", None, raising=False)


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
    # A thread limit (pids cap, RLIMIT_NPROC) is local capacity, not a DNS verdict.
    with pytest.raises(probes.ResolverBusyError, match="can't start new thread") as raised:
        await resolve_target("start-failure.example", AddressFamily.AUTO)

    assert isinstance(raised.value.__cause__, RuntimeError)
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
