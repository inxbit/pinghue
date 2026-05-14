"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import sys
from pathlib import Path

from pinghue import __version__
from pinghue.doctor import run_check
from pinghue.hostfile import parse_host_file
from pinghue.models import AddressFamily, ProbeMode

INTERVAL_MINIMUM = 0.1
HISTORY_STYLES = ("bar", "dots", "sparkline", "none")


class ParsedArgs(argparse.Namespace):
    targets: list[str]
    file: Path | None
    port: int | None
    interval: float
    timeout: float
    count: int | None
    duration: float | None
    no_tui: bool
    output: Path | None
    no_samples: bool
    concurrency: int
    jitter_threshold: float
    fail_threshold: int
    history_style: str
    numeric: bool
    address_family: str
    check: bool
    quiet: bool


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pinghue",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=32),
        description="Colored, concurrent ICMP/TCP ping monitor for the terminal.",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help="hostnames or IP addresses to probe",
    )
    parser.add_argument("-f", "--file", type=Path, help="read targets from a plain text host file")
    parser.add_argument("-p", "--port", type=int, help="use TCP connect checks against PORT")
    parser.add_argument("-i", "--interval", type=float, default=1.0, help="seconds between probes")
    parser.add_argument("--timeout", type=float, help="probe timeout in seconds")
    parser.add_argument("-c", "--count", type=int, help="number of probes per target before exit")
    parser.add_argument("--duration", type=float, help="maximum run duration in seconds")
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="print one line per probe instead of the TUI",
    )
    parser.add_argument("--output", type=Path, help="write a JSON run summary to PATH on exit")
    parser.add_argument(
        "--no-samples",
        action="store_true",
        help="omit per-probe samples from JSON output",
    )
    parser.add_argument("--concurrency", type=int, default=64, help="maximum concurrent probes")
    parser.add_argument("--jitter-threshold", type=float, default=50.0, metavar="MS")
    parser.add_argument("--fail-threshold", type=int, default=3, metavar="COUNT")
    parser.add_argument("--history-style", choices=HISTORY_STYLES, default="bar")
    parser.add_argument(
        "-n",
        "--numeric",
        action="store_true",
        help="skip DNS and require IP literals",
    )
    family = parser.add_mutually_exclusive_group()
    family.add_argument("-4", "--ipv4", action="store_true", help="force IPv4")
    family.add_argument("-6", "--ipv6", action="store_true", help="force IPv6")
    parser.add_argument("--check", action="store_true", help="run environment diagnostics and exit")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress --check output and return only status",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def dedupe_targets(targets: list[str]) -> list[str]:
    """Return targets in first-seen order without duplicates."""
    seen: set[str] = set()
    result: list[str] = []

    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        result.append(target)

    return result


def _address_family_from_literal(targets: list[str]) -> str:
    families: set[int] = set()
    for target in targets:
        try:
            families.add(ipaddress.ip_address(target).version)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"--numeric requires IP literals: {target}") from exc

    if families == {6}:
        return AddressFamily.IPV6.value
    return AddressFamily.IPV4.value


def parse_args(argv: list[str] | None = None) -> ParsedArgs:
    parser = _parser()
    args = parser.parse_args(argv, namespace=ParsedArgs())

    if args.interval < INTERVAL_MINIMUM:
        parser.error(f"minimum interval is {INTERVAL_MINIMUM}")

    args.timeout = args.timeout if args.timeout is not None else args.interval
    if args.timeout <= 0:
        parser.error("timeout must be greater than 0")

    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("port outside of range 1-65535")

    if args.count is not None and args.count <= 0:
        parser.error("count must be greater than 0")

    if args.duration is not None and args.duration <= 0:
        parser.error("duration must be greater than 0")

    if args.concurrency <= 0:
        parser.error("concurrency must be greater than 0")

    if args.fail_threshold <= 0:
        parser.error("fail-threshold must be greater than 0")

    file_targets = parse_host_file(args.file) if args.file else []
    args.targets = dedupe_targets([*args.targets, *file_targets])

    if args.numeric:
        args.address_family = _address_family_from_literal(args.targets)
    elif args.ipv6:
        args.address_family = AddressFamily.IPV6.value
    elif args.ipv4:
        args.address_family = AddressFamily.IPV4.value
    else:
        args.address_family = AddressFamily.AUTO.value

    if not args.check and not args.targets:
        parser.error("at least one target is required unless --check is used")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.check:
        return run_check(quiet=args.quiet)

    from pinghue.runner import run

    mode = ProbeMode.TCP if args.port else ProbeMode.ICMP
    return asyncio.run(run(args, mode=mode))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
