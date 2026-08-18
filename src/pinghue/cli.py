"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import math
import sys
import unicodedata
from pathlib import Path

from pinghue import __version__
from pinghue.config import RunConfig
from pinghue.display import sanitize_display
from pinghue.doctor import run_check
from pinghue.hostfile import TARGET_COUNT_MAXIMUM, TARGET_MAXIMUM, parse_host_file
from pinghue.models import AddressFamily, ProbeMode

INTERVAL_MINIMUM = 0.1
CONCURRENCY_MAXIMUM = 1024
HOST_LABEL_MAXIMUM = 128
HISTORY_STYLES = ("bar", "dots", "sparkline", "none")


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
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=1.0,
        help="seconds between probes (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="probe timeout in seconds (default: the interval)",
    )
    parser.add_argument("-c", "--count", type=int, help="number of probes per target before exit")
    parser.add_argument("--duration", type=float, help="maximum run duration in seconds")
    parser.add_argument(
        "--no-tui",
        action="store_true",
        help="print one line per probe instead of the TUI",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write a JSON run summary to PATH on exit ('-' for stdout)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="allow --output to rewrite an existing single-link regular file in place",
    )
    parser.add_argument(
        "--output-mode",
        choices=("private", "umask"),
        default="private",
        help=(
            "permissions for the --output file: 'private' (0600, owner only; default) "
            "or 'umask' (honor the process umask)"
        ),
    )
    parser.add_argument(
        "--no-samples",
        action="store_true",
        help="emit empty per-target samples arrays in JSON output",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=64,
        help=(
            "maximum concurrent probes, 1-"
            f"{CONCURRENCY_MAXIMUM} "
            "(ICMP daemon workers are bounded by this limit; default: %(default)s)"
        ),
    )
    parser.add_argument(
        "--jitter-threshold",
        type=float,
        default=50.0,
        metavar="MS",
        help="mark a target intermittent when its jitter exceeds MS (default: %(default)s)",
    )
    parser.add_argument(
        "--fail-threshold",
        type=int,
        default=3,
        metavar="COUNT",
        help="consecutive failed probes before a target is down (default: %(default)s)",
    )
    failure_mode = parser.add_mutually_exclusive_group()
    failure_mode.add_argument(
        "--fail-on-any-down",
        action="store_true",
        help="return a non-zero exit code when any target finishes down",
    )
    failure_mode.add_argument(
        "--fail-on-all-down",
        action="store_true",
        help="return a non-zero exit code only when all targets finish down",
    )
    failure_mode.add_argument(
        "--fail-on-down",
        dest="fail_on_all_down",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--history-style",
        choices=HISTORY_STYLES,
        default="bar",
        help=("history cell glyphs: bar (default), dots, or none; sparkline is an alias of bar"),
    )
    parser.add_argument(
        "-n",
        "--numeric",
        action="store_true",
        help="skip DNS and require IP literals",
    )
    family = parser.add_mutually_exclusive_group()
    # No -4/-6 short aliases: options that look like negative numbers make
    # argparse treat values like "-c -3" as flags, breaking validation errors.
    family.add_argument("--ipv4", action="store_true", help="force IPv4")
    family.add_argument("--ipv6", action="store_true", help="force IPv6")
    parser.add_argument("--check", action="store_true", help="run environment diagnostics and exit")
    parser.add_argument(
        "--resolve-name",
        metavar="HOST",
        help="hostname used by --check for DNS diagnostics",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress --check output and return only status",
    )
    parser.add_argument(
        "--host-label",
        default="local",
        metavar="LABEL",
        help="operator-controlled host label written to JSON output (default: %(default)s)",
    )
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def dedupe_targets(targets: list[str]) -> list[str]:
    """Return targets in first-seen order without duplicates."""
    return list(dict.fromkeys(targets))


def _validate_finite(parser: argparse.ArgumentParser, name: str, value: float) -> None:
    if not math.isfinite(value):
        parser.error(f"{name} must be a finite number")


def _validate_text_length(
    parser: argparse.ArgumentParser,
    name: str,
    value: str,
    *,
    maximum: int,
) -> None:
    if not value.strip():
        parser.error(f"{name} must not be empty")
    if len(value) > maximum:
        parser.error(f"{name} must not exceed {maximum} characters")


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(char)[0] == "C" for char in value)


def _numeric_address_family(
    parser: argparse.ArgumentParser,
    targets: list[str],
    *,
    force_ipv4: bool,
    force_ipv6: bool,
) -> str:
    families: set[int] = set()
    for target in targets:
        try:
            families.add(ipaddress.ip_address(target).version)
        except ValueError:
            parser.error(f"--numeric requires IP literals: {sanitize_display(target)}")

    if force_ipv4:
        if 6 in families:
            parser.error("--ipv4 conflicts with an IPv6 literal target under --numeric")
        return AddressFamily.IPV4.value
    if force_ipv6:
        if 4 in families:
            parser.error("--ipv6 conflicts with an IPv4 literal target under --numeric")
        return AddressFamily.IPV6.value
    if families == {6}:
        return AddressFamily.IPV6.value
    if families == {4, 6}:
        return AddressFamily.AUTO.value
    return AddressFamily.IPV4.value


def parse_args(argv: list[str] | None = None) -> RunConfig:
    parser = _parser()
    args = parser.parse_args(argv, namespace=RunConfig())

    _validate_finite(parser, "interval", args.interval)
    if args.interval < INTERVAL_MINIMUM:
        parser.error(f"minimum interval is {INTERVAL_MINIMUM}")

    args.timeout = args.timeout if args.timeout is not None else args.interval
    _validate_finite(parser, "timeout", args.timeout)
    if args.timeout <= 0:
        parser.error("timeout must be greater than 0")

    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("port outside of range 1-65535")

    if args.count is not None and args.count <= 0:
        parser.error("count must be greater than 0")

    if args.duration is not None and args.duration <= 0:
        parser.error("duration must be greater than 0")
    if args.duration is not None:
        _validate_finite(parser, "duration", args.duration)

    if args.concurrency <= 0:
        parser.error("concurrency must be greater than 0")

    if args.concurrency > CONCURRENCY_MAXIMUM:
        parser.error(f"concurrency must not exceed {CONCURRENCY_MAXIMUM}")

    if args.fail_threshold <= 0:
        parser.error("fail-threshold must be greater than 0")

    _validate_finite(parser, "jitter-threshold", args.jitter_threshold)
    if args.jitter_threshold < 0:
        parser.error("jitter-threshold must be greater than or equal to 0")

    try:
        file_targets = parse_host_file(args.file) if args.file else []
    except (OSError, ValueError) as exc:
        parser.error(sanitize_display(str(exc)))

    args.targets = dedupe_targets([target.strip() for target in (*args.targets, *file_targets)])
    if len(args.targets) > TARGET_COUNT_MAXIMUM:
        parser.error(f"target count must not exceed {TARGET_COUNT_MAXIMUM}")
    for target in args.targets:
        _validate_text_length(parser, "target", target, maximum=TARGET_MAXIMUM)
        if target.startswith("-"):
            # Hostnames and IPs never start with "-"; catches stale flag usage
            # like "-4" (argparse passes numeric-looking tokens as positionals).
            parser.error(f"invalid target: {sanitize_display(target)}")
        if _contains_control_characters(target):
            parser.error(f"target contains control characters: {sanitize_display(target)}")

    if args.resolve_name is not None:
        _validate_text_length(
            parser,
            "resolve-name",
            args.resolve_name,
            maximum=TARGET_MAXIMUM,
        )
    _validate_text_length(
        parser,
        "host-label",
        args.host_label,
        maximum=HOST_LABEL_MAXIMUM,
    )

    if args.numeric:
        args.address_family = _numeric_address_family(
            parser, args.targets, force_ipv4=args.ipv4, force_ipv6=args.ipv6
        )
    elif args.ipv6:
        args.address_family = AddressFamily.IPV6.value
    elif args.ipv4:
        args.address_family = AddressFamily.IPV4.value
    else:
        args.address_family = AddressFamily.AUTO.value

    if not args.check and not args.targets:
        parser.error("at least one target is required unless --check is used")

    if not args.check and not args.no_tui and args.output is not None and str(args.output) == "-":
        parser.error("--output - requires --no-tui; the TUI owns stdout while it runs")

    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.check:
        resolve_name = args.resolve_name or (args.targets[0] if args.targets else None)
        return run_check(quiet=args.quiet, resolve_name=resolve_name)

    from pinghue.runner import run

    if not args.no_tui and not sys.stdout.isatty():
        # The TUI draws on stderr and leaves stdout empty; scripts and cron
        # jobs almost always want the per-probe line output instead.
        print(
            "pinghue: warning: stdout is not a terminal; pass --no-tui for line output "
            "(a future major release will make this the default)",
            file=sys.stderr,
        )

    mode = ProbeMode.TCP if args.port else ProbeMode.ICMP
    try:
        return asyncio.run(run(args, mode=mode))
    except OSError as exc:
        print(f"pinghue: error: {sanitize_display(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
