import asyncio
import errno
import socket

import icmplib
import pytest

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


async def test_resolve_target_preserves_all_getaddrinfo_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLoop:
        async def getaddrinfo(self, *_: object, **__: object) -> list[object]:
            return [
                (socket.AF_INET6, 0, 0, "", ("2001:db8::1", 0)),
                (socket.AF_INET, 0, 0, "", ("192.0.2.10", 0)),
                (socket.AF_INET, 0, 0, "", ("192.0.2.10", 0)),
            ]

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())

    resolved = await resolve_target("service.example", AddressFamily.AUTO)

    assert isinstance(resolved, ResolvedTarget)
    assert resolved.address == "192.0.2.10"
    assert resolved.addresses == ("192.0.2.10", "2001:db8::1")
    assert resolved.family == AddressFamily.IPV4


async def test_icmp_probe_runs_blocking_ping_in_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    class FakeResult:
        is_alive = True
        avg_rtt = 4.2

    def fake_ping(*_: object, **__: object) -> FakeResult:
        return FakeResult()

    async def fake_to_thread(function: object, *_: object, **__: object) -> object:
        calls.append(function)
        return fake_ping()

    monkeypatch.setattr(icmplib, "ping", fake_ping)
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    sample = await icmp_probe(
        "1.1.1.1",
        timeout_s=1.0,
        address_family=AddressFamily.IPV4,
    )

    assert calls == [fake_ping]
    assert sample.status == SampleStatus.OK
    assert sample.latency_ms == 4.2
