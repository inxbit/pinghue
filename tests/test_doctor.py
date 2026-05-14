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
    monkeypatch.setattr(doctor, "_dns_probe", lambda: ("93.184.215.14", 8.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(stream=output, quiet=False, use_color=False)

    text = output.getvalue()
    assert exit_code == 1
    assert "[fail]  Unprivileged ICMP sockets NOT available" in text
    assert 'Current value: "1   0"  (empty range)' in text
    assert 'sudo sysctl -w net.ipv4.ping_group_range="0 2147483647"' in text
    assert "Not ready for ICMP. TCP mode works. See fixes above." in text


def test_doctor_quiet_suppresses_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor.socket, "socket", lambda *_: FakeSocket())
    monkeypatch.setattr(doctor.os, "geteuid", lambda: 501)
    monkeypatch.setattr(doctor, "_loopback_icmp_probe", lambda: (0.12, None))
    monkeypatch.setattr(doctor, "_dns_probe", lambda: ("93.184.215.14", 1.0, None))
    output = io.StringIO()

    exit_code = doctor.run_check(stream=output, quiet=True, use_color=False)

    assert exit_code == 0
    assert output.getvalue() == ""
