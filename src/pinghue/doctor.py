"""Environment doctor for ICMP readiness."""

from __future__ import annotations

import asyncio
import os
import platform
import socket
import sys
import threading
import time
from typing import Any, NamedTuple, TextIO

from pinghue import __version__
from pinghue.display import sanitize_display
from pinghue.limits import (
    FD_REQUEST_MAXIMUM,
    FD_RESERVE,
    concurrency_capacity,
    fd_limit_label,
    read_fd_limit,
)
from pinghue.models import SampleStatus
from pinghue.probes import UNSPECIFIED_ONLY_ERROR, _usable_addresses, family_from_ip, icmp_probe

OK = "[ok]"
FAIL = "[fail]"
WARN = "[warn]"

_IPPROTO_ICMPV6 = getattr(socket, "IPPROTO_ICMPV6", 58)


class IcmpSocketCheck(NamedTuple):
    ok: bool
    detail: str


def _status(token: str, *, use_color: bool) -> str:
    if not use_color:
        return token

    colors = {
        OK: "\033[32m",
        FAIL: "\033[31m",
        WARN: "\033[33m",
    }
    return f"{colors[token]}{token}\033[0m"


def _os_label() -> str:
    system = platform.system()

    if system == "Darwin":
        mac_version = platform.mac_ver()[0] or "unknown"
        return f"macOS {mac_version} (Darwin {platform.release()}, {platform.machine()})"

    if system == "Linux":
        try:
            pretty_name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except OSError:
            pretty_name = "Linux"

        return f"Linux {platform.release()} ({pretty_name}, {platform.machine()})"

    return f"{system} {platform.release()} ({platform.machine()})"


def _uid_label() -> str:
    euid = os.geteuid() if hasattr(os, "geteuid") else os.getuid()
    kind = "root" if euid == 0 else "non-root"
    return f"{euid} ({kind})"


def _check_dgram_icmp_socket(
    *, family: int = socket.AF_INET, proto: int | None = None
) -> IcmpSocketCheck:
    proto = proto if proto is not None else socket.IPPROTO_ICMP
    family_label = "AF_INET6" if family == socket.AF_INET6 else "AF_INET"
    proto_label = "IPPROTO_ICMPV6" if family == socket.AF_INET6 else "IPPROTO_ICMP"
    descriptor = f"socket({family_label}, SOCK_DGRAM, {proto_label})"

    try:
        probe_socket = socket.socket(family, socket.SOCK_DGRAM, proto)
    except PermissionError as exc:
        return IcmpSocketCheck(False, f"{descriptor} → {type(exc).__name__}")
    except OSError as exc:
        return IcmpSocketCheck(False, f"{descriptor} → {type(exc).__name__}: {exc}")

    probe_socket.close()
    return IcmpSocketCheck(True, f"{descriptor} succeeded")


def _read_ping_group_range() -> tuple[int, int, str] | None:
    path = "/proc/sys/net/ipv4/ping_group_range"
    try:
        with open(path, encoding="utf-8") as file:
            raw = file.read().strip()
    except OSError:
        return None

    parts = raw.split()
    if len(parts) != 2:
        return None

    try:
        start, end = int(parts[0]), int(parts[1])
    except ValueError:
        return None

    return start, end, raw


DEFAULT_DNS_PROBE_NAME = "example.com"
# Matches runner.DNS_LOOKUP_TIMEOUT_SECONDS: above glibc's default 5 s
# per-nameserver timeout, so a resolver that fails over from a dead first
# nameserver is not reported as broken.
DNS_PROBE_TIMEOUT_SECONDS = 12.0


def _dns_probe(
    resolve_name: str,
    *,
    timeout_s: float = DNS_PROBE_TIMEOUT_SECONDS,
) -> tuple[str | None, float | None, str | None]:
    start = time.perf_counter()

    # getaddrinfo has no timeout parameter; run it on a daemon thread so a
    # blackholed resolver cannot hang `pinghue --check` forever.
    outcome: list[list[Any] | Exception] = []

    def worker() -> None:
        try:
            outcome.append(
                socket.getaddrinfo(resolve_name, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
            )
        except Exception as exc:  # noqa: BLE001 - doctor reports exact environment failures
            outcome.append(exc)

    thread = threading.Thread(target=worker, name="pinghue-doctor-dns", daemon=True)
    thread.start()
    thread.join(timeout_s)

    if not outcome:
        return None, None, f"getaddrinfo timed out after {timeout_s:g}s"

    result = outcome[0]
    if isinstance(result, Exception):
        return None, None, str(result)
    infos = result

    if not infos:
        return None, None, "getaddrinfo: no addresses returned"

    # Judge the answer as a run would: a DNS filter's 0.0.0.0 or :: is a
    # dns_failure there, so it must not read as working DNS here.
    usable = _usable_addresses(infos, prefer_ipv4=False)
    if not usable:
        return None, None, UNSPECIFIED_ONLY_ERROR

    elapsed_ms = float(round((time.perf_counter() - start) * 1000))
    family, *_rest, sockaddr = usable[0]
    address = sockaddr[0]
    protocol = "ipv6" if family == socket.AF_INET6 else "ipv4"
    return f"{address} ({protocol})", elapsed_ms, None


def _loopback_icmp_probe(address: str = "127.0.0.1") -> tuple[float | None, str | None]:
    """Send one echo through pinghue's own ICMP engine, as a probe run would."""
    try:
        sample = asyncio.run(
            icmp_probe(address, timeout_s=1.0, address_family=family_from_ip(address))
        )
    except Exception as exc:  # noqa: BLE001 - doctor reports exact environment failures
        return None, f"{type(exc).__name__}: {exc}"

    if sample.status == SampleStatus.OK and sample.latency_ms is not None:
        return float(sample.latency_ms), None
    if sample.status == SampleStatus.TIMEOUT:
        return None, "timeout"
    return None, sample.error or sample.status.value


def _write_header(lines: list[str]) -> None:
    lines.extend(
        [
            f"pinghue {__version__} — environment check",
            "",
            "System",
            f"  OS              {_os_label()}",
            f"  Python          {platform.python_version()} ({sys.executable})",
            f"  Effective UID   {_uid_label()}",
            "",
        ]
    )


def _write_root_warning(lines: list[str], *, use_color: bool) -> None:
    lines.append(
        f"{_status(WARN, use_color=use_color)}  Running as root. "
        "ICMP will work, but this is not recommended."
    )
    if platform.system() == "Linux":
        lines.extend(
            [
                "        Prefer one of:",
                '          sudo sysctl -w net.ipv4.ping_group_range="<gid> <gid>"',
                "          pinghue -p 443 example.com   # TCP mode, no privileges",
            ]
        )
    else:
        lines.extend(
            [
                "        Run pinghue as a regular user; ICMP does not require root here.",
                "        Or use: pinghue -p 443 example.com",
            ]
        )
    lines.append("")


def _write_linux_fix(lines: list[str], *, egid: int) -> None:
    group_range = f"{egid} {egid}"
    lines.extend(
        [
            "",
            "          Pick one fix:",
            "",
            "          A) Allow your group (preferred, no setuid, no caps):",
            f'               sudo sysctl -w net.ipv4.ping_group_range="{group_range}"',
            f"               echo 'net.ipv4.ping_group_range={group_range}' \\",
            "                 | sudo tee /etc/sysctl.d/99-pinghue.conf",
            "               # Use 0 2147483647 only if every local group should have ICMP.",
            "",
            "          B) Skip ICMP — use TCP mode instead:",
            "               pinghue -p 443 example.com",
            "",
            "          Note: setcap cap_net_raw does not help here — the pinghue",
            "          command is a Python launcher script, and Linux ignores file",
            "          capabilities on interpreter scripts.",
            "",
        ]
    )


def _write_fd_limit(lines: list[str], *, use_color: bool) -> None:
    # Only TCP mode is bounded by the open-file limit: every in-flight TCP probe
    # holds a socket, while ICMP probes share the ICMP engine's few sockets.
    # Imported here because the CLI module imports this one at startup.
    from pinghue.cli import CONCURRENCY_DEFAULT_MINIMUM, CONCURRENCY_MAXIMUM

    lines.extend(["", "Open files"])
    limit = read_fd_limit()
    if limit is None:
        lines.append(
            f"  {_status(WARN, use_color=use_color)}  "
            "Open-file limit not available on this platform"
        )
        return

    needed = CONCURRENCY_MAXIMUM + FD_RESERVE
    label = f"Open-file limit {fd_limit_label(limit.soft)} (hard {fd_limit_label(limit.hard)})"
    if limit.soft is None or limit.soft >= needed:
        lines.append(
            f"  {_status(OK, use_color=use_color)}    "
            f"{label} fits --concurrency up to {CONCURRENCY_MAXIMUM} in TCP mode"
        )
        return

    raisable = FD_REQUEST_MAXIMUM if limit.hard is None else min(limit.hard, FD_REQUEST_MAXIMUM)
    if raisable >= needed:
        lines.append(
            f"  {_status(OK, use_color=use_color)}    "
            f"{label}; raised at startup to fit --concurrency up to {CONCURRENCY_MAXIMUM} "
            "in TCP mode"
        )
        return

    capacity = concurrency_capacity(raisable)
    below_default = (
        f", below the default minimum of {CONCURRENCY_DEFAULT_MINIMUM}"
        if capacity < CONCURRENCY_DEFAULT_MINIMUM
        else ""
    )
    lines.extend(
        [
            f"  {_status(WARN, use_color=use_color)}  "
            f"{label} caps --concurrency at {capacity} in TCP mode{below_default}",
            f"          Raise the hard limit to at least {needed} to allow "
            f"--concurrency {CONCURRENCY_MAXIMUM} in TCP mode.",
            "          ICMP probes share a few sockets and are not limited by it.",
        ]
    )


def run_check(
    *,
    stream: TextIO = sys.stdout,
    quiet: bool = False,
    use_color: bool | None = None,
    resolve_name: str | None = None,
) -> int:
    """Run environment diagnostics and return a process exit code."""
    if use_color is None:
        use_color = stream.isatty()

    lines: list[str] = []
    _write_header(lines)

    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if is_root:
        _write_root_warning(lines, use_color=use_color)

    icmp = _check_dgram_icmp_socket()
    icmp_ready = icmp.ok
    lines.append("ICMP mode")

    if icmp.ok:
        loopback_ms, loopback_error = _loopback_icmp_probe()
        icmp_ready = loopback_error is None
        if is_root:
            lines.append(
                f"  {_status(OK, use_color=use_color)}    ICMP datagram sockets available (root)"
            )
        else:
            lines.append(
                f"  {_status(OK, use_color=use_color)}  Unprivileged ICMP sockets available"
            )
        if loopback_error:
            lines.append(
                f"  {_status(FAIL, use_color=use_color)}  "
                f"DGRAM ICMP probe to 127.0.0.1 failed ({loopback_error})"
            )
        else:
            lines.append(
                f"  {_status(OK, use_color=use_color)}    "
                f"DGRAM ICMP probe to 127.0.0.1 succeeded ({loopback_ms:.2f} ms)"
            )
    else:
        lines.append(
            f"  {_status(FAIL, use_color=use_color)}  Unprivileged ICMP sockets NOT available"
        )
        lines.append(f"          {icmp.detail}")

        if platform.system() == "Linux":
            group_range = _read_ping_group_range()
            egid = os.getegid() if hasattr(os, "getegid") else os.getgid()
            if group_range:
                start, end, raw = group_range
                empty = start > end
                if empty or not start <= egid <= end:
                    lines.append("")
                    lines.append(
                        f"          Your GID ({egid}) is outside net.ipv4.ping_group_range."
                    )
                    suffix = "  (empty range)" if empty else ""
                    lines.append(f'          Current value: "{raw}"{suffix}')
            _write_linux_fix(lines, egid=egid)

    icmp6 = _check_dgram_icmp_socket(family=socket.AF_INET6, proto=_IPPROTO_ICMPV6)
    if icmp6.ok:
        loopback6_ms, loopback6_error = _loopback_icmp_probe("::1")
        if loopback6_error:
            ipv4_suffix = "; IPv4 ICMP still works" if icmp_ready else ""
            lines.append(
                f"  {_status(WARN, use_color=use_color)}  "
                f"IPv6 ICMP probe to ::1 failed ({loopback6_error}){ipv4_suffix}"
            )
        else:
            lines.append(
                f"  {_status(OK, use_color=use_color)}    "
                f"DGRAM ICMPv6 probe to ::1 succeeded ({loopback6_ms:.2f} ms)"
            )
    else:
        lines.append(
            f"  {_status(WARN, use_color=use_color)}  IPv6 ICMP not verified ({icmp6.detail})"
        )

    lines.extend(
        [
            "",
            "TCP mode",
            f"  {_status(OK, use_color=use_color)}    No special privileges required",
        ]
    )

    dns_name = resolve_name or DEFAULT_DNS_PROBE_NAME
    address, dns_ms, dns_error = _dns_probe(dns_name)
    display_dns_name = sanitize_display(dns_name)
    lines.extend(["", "DNS"])
    if dns_error:
        lines.append(
            f"  {_status(WARN, use_color=use_color)}  "
            f'getaddrinfo("{display_dns_name}") failed: {sanitize_display(dns_error)}'
        )
    else:
        lines.append(
            f"  {_status(OK, use_color=use_color)}    "
            f'getaddrinfo("{display_dns_name}") → '
            f"{sanitize_display(address or '')} ({dns_ms:.0f} ms)"
        )

    _write_fd_limit(lines, use_color=use_color)

    if icmp_ready and is_root:
        exit_code = 0
        lines.extend(["", "Ready, but consider running unprivileged."])
    elif icmp_ready:
        exit_code = 0
        lines.extend(["", "Ready. Try:  pinghue 1.1.1.1 example.com"])
    else:
        exit_code = 1
        lines.extend(["", "Not ready for ICMP. TCP mode works. See fixes above."])

    if not quiet:
        stream.write("\n".join(lines))
        stream.write("\n")

    return exit_code
