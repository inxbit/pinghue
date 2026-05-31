import io

import pytest

from pinghue import doctor


class FakeSocket:
    def close(self) -> None:
        return None


def test_doctor_linux_blocked_prints_fix_block(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_socket(*_: object) -> FakeSocket:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(doctor.platform, "system", lambda: "Linux")
    monkeypatch.setattr(doctor.platform, "release", lambda: "6.5.0-21-generic")
    monkeypatch.setattr(doctor.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(
        doctor.platform,
        "freedesktop_os_release",
        lambda: {"PRETTY_NAME": "Ubuntu 24.04"},
    )
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(doctor.os, "getegid", lambda: 1000)
    monkeypatch.setattr(doctor.sys, "executable", "/usr/bin/python3.12")
    monkeypatch.setattr(doctor.socket, "socket", fail_socket)
    monkeypatch.setattr(doctor, "_read_ping_group_range", lambda: (1, 0, "1   0"))
    monkeypatch.setattr(doctor, "_dns_probe", lambda _: ("93.184.215.14", 8.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(stream=output, quiet=False, use_color=False)

    text = output.getvalue()
    assert exit_code == 1
    assert "[fail]  Unprivileged ICMP sockets NOT available" in text
    assert 'Current value: "1   0"  (empty range)' in text
    assert 'sudo sysctl -w net.ipv4.ping_group_range="1000 1000"' in text
    assert "Use 0 2147483647 only if every local group should have ICMP." in text
    assert "Not ready for ICMP. TCP mode works. See fixes above." in text


def test_doctor_reports_ipv6_icmp_unavailable_without_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # L4: IPv6 ICMP is verified separately; its absence warns but does not flip
    # the IPv4-driven exit code.
    def socket_factory(family: int, _type: int, _proto: int) -> FakeSocket:
        if family == doctor.socket.AF_INET6:
            raise PermissionError("no ipv6 icmp")
        return FakeSocket()

    monkeypatch.setattr(doctor.socket, "socket", socket_factory)
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 501)
    monkeypatch.setattr(doctor, "_loopback_icmp_probe", lambda *_: (0.12, None))
    monkeypatch.setattr(doctor, "_dns_probe", lambda _name: ("1.1.1.1", 1.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(stream=output, quiet=False, use_color=False)

    text = output.getvalue()
    assert exit_code == 0
    assert "Unprivileged ICMP sockets available" in text
    assert "IPv6 ICMP not verified" in text


def test_doctor_verifies_ipv6_icmp_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.socket, "socket", lambda *_: FakeSocket())
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 501)
    monkeypatch.setattr(doctor, "_loopback_icmp_probe", lambda *_: (0.2, None))
    monkeypatch.setattr(doctor, "_dns_probe", lambda _name: ("1.1.1.1", 1.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(stream=output, quiet=False, use_color=False)

    text = output.getvalue()
    assert exit_code == 0
    assert "ICMPv6 probe to ::1 succeeded" in text


def test_doctor_quiet_suppresses_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.socket, "socket", lambda *_: FakeSocket())
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 501)
    monkeypatch.setattr(doctor, "_loopback_icmp_probe", lambda *_: (0.12, None))
    monkeypatch.setattr(doctor, "_dns_probe", lambda _: ("93.184.215.14", 1.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(stream=output, quiet=True, use_color=False)

    assert exit_code == 0
    assert output.getvalue() == ""


def test_doctor_dns_probe_uses_configured_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_getaddrinfo(
        host: str,
        port: object,
        family: int,
        socket_type: int,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        assert port is None
        assert family == doctor.socket.AF_UNSPEC
        assert socket_type == doctor.socket.SOCK_STREAM
        calls.append(host)
        return [(doctor.socket.AF_INET6, 0, 0, "", ("2001:db8::10", 0))]

    monkeypatch.setattr(doctor.socket, "getaddrinfo", fake_getaddrinfo)

    address, _, error = doctor._dns_probe("internal.example")

    assert calls == ["internal.example"]
    assert address == "2001:db8::10 (ipv6)"
    assert error is None


def test_doctor_dns_probe_handles_empty_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.socket, "getaddrinfo", lambda *_: [])

    address, elapsed_ms, error = doctor._dns_probe("empty.example")

    assert address is None
    assert elapsed_ms is None
    assert error == "getaddrinfo: no addresses returned"


def test_run_check_prints_configured_dns_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.socket, "socket", lambda *_: FakeSocket())
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 501)
    monkeypatch.setattr(doctor, "_loopback_icmp_probe", lambda *_: (0.12, None))
    monkeypatch.setattr(doctor, "_dns_probe", lambda _name: ("10.0.0.10", 1.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(
        stream=output,
        quiet=False,
        use_color=False,
        resolve_name="internal.example",
    )

    assert exit_code == 0
    assert 'getaddrinfo("internal.example")' in output.getvalue()


def test_run_check_sanitizes_dns_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    dns_name = "\x1b]52;c;QUJD\x07bad"
    dns_error = "\x1b[31mboom"
    monkeypatch.setattr(doctor.socket, "socket", lambda *_: FakeSocket())
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 501)
    monkeypatch.setattr(doctor, "_loopback_icmp_probe", lambda *_: (0.12, None))
    monkeypatch.setattr(doctor, "_dns_probe", lambda _name: (None, None, dns_error))
    output = io.StringIO()

    exit_code = doctor.run_check(
        stream=output,
        quiet=False,
        use_color=False,
        resolve_name=dns_name,
    )

    text = output.getvalue()
    assert exit_code == 0
    assert dns_name not in text
    assert dns_error not in text
    assert r'\x1b]52;c;QUJD\x07bad' in text
    assert r"\x1b[31mboom" in text
