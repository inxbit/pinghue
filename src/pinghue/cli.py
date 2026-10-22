"""Command-line interface."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import math
import sys
import unicodedata
from pathlib import Path
from typing import NoReturn

from pinghue import __version__
from pinghue.config import RunConfig
from pinghue.display import sanitize_display
from pinghue.doctor import run_check
from pinghue.export import check_output_path
from pinghue.hostfile import TARGET_COUNT_MAXIMUM, TARGET_MAXIMUM, parse_host_file
from pinghue.models import AddressFamily, ProbeMode
from pinghue.runner import MIN_OVERRUN_SLEEP

INTERVAL_MINIMUM = 0.1
CONCURRENCY_DEFAULT_MINIMUM = 64
CONCURRENCY_MAXIMUM = 1024
# Spare slots above the all-silent estimate, so queueing jitter near saturation
# does not stretch the probe cadence.
CONCURRENCY_HEADROOM = 1.25
HOST_LABEL_MAXIMUM = 128
HISTORY_STYLES = ("bar", "dots", "sparkline", "none")


def _output_path(value: str) -> Path:
    path = Path(value)
    if value == "-" or str(path) != "-":
        return path
    # pathlib collapses "./-" to "-", the stdout marker; an explicit relative
    # spelling names a file called "-" in the current directory instead.
    try:
        return Path.cwd() / path
    except OSError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # A stderr closed at startup is None, and argparse would print the usage
        # synopsis to stdout (print_usage(None) means stdout), into the line
        # output or a pipeline; on Python 3.10 it then fails writing the error
        # line and exits 1. Exit with the usage status and print nothing.
        if sys.stderr is None:
            self.exit(2)
        super().error(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
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
        type=_output_path,
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
            "permissions for the --output file: 'private' (0600, owner only; default; "
            "also refuses an existing FIFO that other users can open) "
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
        help=(
            f"maximum concurrent probes, 1-{CONCURRENCY_MAXIMUM} (default: automatic, "
            "sized from the target count, timeout and interval: "
            f"{CONCURRENCY_DEFAULT_MINIMUM}-{CONCURRENCY_MAXIMUM} for TCP, "
            f"{CONCURRENCY_DEFAULT_MINIMUM}-{TARGET_COUNT_MAXIMUM} for ICMP)"
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


def default_concurrency_ceiling(mode: ProbeMode) -> int:
    """Return the most probe slots the automatic default may use in ``mode``.

    Every in-flight TCP probe holds its own socket, so TCP stays within the explicit
    ``--concurrency`` maximum (and, at startup, the open-file limit). An in-flight
    ICMP probe holds no thread and no socket of its own, only an entry on the loop's
    shared ICMP engine, so the ICMP default may give every target a slot.
    """
    if mode == ProbeMode.TCP:
        return CONCURRENCY_MAXIMUM
    return TARGET_COUNT_MAXIMUM


def default_concurrency(
    target_count: int,
    *,
    interval: float,
    timeout: float,
    mode: ProbeMode,
) -> int:
    """Return enough probe slots to keep every target on cadence even when all are silent.

    A silent target holds its slot for the whole timeout, then sleeps out the rest of
    its cycle: the interval, or the timeout plus the minimum overrun sleep when the
    timeout fills the interval. Fewer slots than targets x timeout / cycle make every
    target, healthy ones included, queue for a slot and fall behind the interval. The
    result stays within ``default_concurrency_ceiling`` for the probe mode.
    """
    cycle = max(interval, timeout + MIN_OVERRUN_SLEEP)
    needed = math.ceil(target_count * (timeout / cycle) * CONCURRENCY_HEADROOM)
    ceiling = default_concurrency_ceiling(mode)
    return max(CONCURRENCY_DEFAULT_MINIMUM, min(ceiling, needed))


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

    if args.concurrency is not None and args.concurrency <= 0:
        parser.error("concurrency must be greater than 0")

    if args.concurrency is not None and args.concurrency > CONCURRENCY_MAXIMUM:
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

    if args.concurrency is None:
        args.concurrency = default_concurrency(
            len(args.targets),
            interval=args.interval,
            timeout=args.timeout,
            mode=ProbeMode.TCP if args.port else ProbeMode.ICMP,
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


def _print_stderr(message: str) -> None:
    # A stderr closed at startup is None, and print(file=None) would write to
    # stdout, into the line output or a `--output -` JSON document: drop it.
    if sys.stderr is not None:
        print(message, file=sys.stderr)


def _report_error(exc: OSError) -> int:
    _print_stderr(f"pinghue: error: {sanitize_display(str(exc))}")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.check:
        resolve_name = args.resolve_name or (args.targets[0] if args.targets else None)
        return run_check(quiet=args.quiet, resolve_name=resolve_name)

    if not args.no_tui and sys.__stderr__ is None:
        # The TUI draws on the stderr the process started with. With descriptor
        # 2 closed there is nowhere to draw: the app would hang, ignoring stop
        # signals, before probing anything. Nor is there anywhere to report it,
        # and stdout may feed a pipeline, so exit without printing.
        return 1

    if args.output is not None:
        # Refuse an unusable report path before the run starts, not after it:
        # before the TUI warning below, the open-file limit change and probing.
        # The check only inspects the path and holds no descriptor.
        try:
            check_output_path(args.output, overwrite=args.overwrite, output_mode=args.output_mode)
        except OSError as exc:
            return _report_error(exc)

    from pinghue.runner import run

    if not args.no_tui and not sys.stdout.isatty():
        # The TUI draws on stderr and leaves stdout empty; scripts and cron
        # jobs almost always want the per-probe line output instead.
        _print_stderr(
            "pinghue: warning: stdout is not a terminal; pass --no-tui for line output "
            "(a future major release will make this the default)"
        )

    mode = ProbeMode.TCP if args.port else ProbeMode.ICMP
    try:
        return asyncio.run(run(args, mode=mode))
    except OSError as exc:
        return _report_error(exc)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
