import asyncio

import icmplib
import pytest

from pinghue.models import AddressFamily, SampleStatus
from pinghue.probes import icmp_probe, resolve_target, tcp_probe


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


async def test_resolve_target_numeric_rejects_hostname() -> None:
    resolved = await resolve_target("example.com", AddressFamily.AUTO, numeric=True)

    assert resolved.address is None
    assert resolved.error == "--numeric requires an IP literal"


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
