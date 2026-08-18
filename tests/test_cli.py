import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from pinghue.cli import (
    CONCURRENCY_MAXIMUM,
    HOST_LABEL_MAXIMUM,
    TARGET_COUNT_MAXIMUM,
    TARGET_MAXIMUM,
    _parser,
    main,
    parse_args,
)


def test_parse_args_defaults_to_icmp_auto_family_and_tui() -> None:
    args = parse_args(["1.1.1.1"])

    assert args.targets == ["1.1.1.1"]
    assert args.port is None
    assert args.interval == 1.0
    assert args.timeout == 1.0
    assert args.address_family == "auto"
    assert args.no_tui is False
    assert args.history_style == "bar"


def test_parse_args_tcp_count_and_no_tui() -> None:
    args = parse_args(["-p", "443", "-c", "2", "--no-tui", "example.com"])

    assert args.port == 443
    assert args.count == 2
    assert args.no_tui is True


def test_help_explains_that_no_samples_emits_empty_arrays(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--help"])

    assert excinfo.value.code == 0
    help_text = capsys.readouterr().out
    assert "emit empty per-target samples arrays" in help_text
    normalized_help = re.sub(
        r"(?<=\w)-\s+(?=\w)",
        "-",
        " ".join(help_text.split()),
    )
    assert "rewrite an existing single-link regular file in place" in normalized_help


def test_help_describes_every_option_and_shows_defaults() -> None:
    parser = _parser()

    undocumented = [
        action.option_strings
        for action in parser._actions
        if action.help in (None, "")
    ]
    assert undocumented == []

    help_text = " ".join(parser.format_help().split())
    for default in (
        "(default: 1.0)",
        "default: 64)",
        "(default: 3)",
        "(default: 50.0)",
        "(default: local)",
    ):
        assert default in help_text
    assert "sparkline is an alias of bar" in help_text


def test_parse_args_rejects_too_fast_interval() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--interval", "0.01", "1.1.1.1"])


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--interval", "nan"),
        ("--interval", "inf"),
        ("--timeout", "nan"),
        ("--timeout", "-inf"),
        ("--duration", "nan"),
        ("--jitter-threshold", "inf"),
    ],
)
def test_parse_args_rejects_non_finite_float_values(option: str, value: str) -> None:
    with pytest.raises(SystemExit):
        parse_args([option, value, "1.1.1.1"])


def test_parse_args_rejects_overlong_target() -> None:
    with pytest.raises(SystemExit):
        parse_args(["a" * (TARGET_MAXIMUM + 1)])


def test_parse_args_rejects_too_many_combined_targets() -> None:
    targets = [f"host-{index}" for index in range(TARGET_COUNT_MAXIMUM + 1)]

    with pytest.raises(SystemExit):
        parse_args(targets)


def test_parse_args_rejects_overlong_check_resolve_name() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--check", "--resolve-name", "a" * (TARGET_MAXIMUM + 1)])


def test_parse_args_rejects_overlong_host_label() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--host-label", "a" * (HOST_LABEL_MAXIMUM + 1), "1.1.1.1"])


def test_parse_args_accepts_explicit_output_overwrite() -> None:
    args = parse_args(["--output", "out.json", "--overwrite", "1.1.1.1"])

    assert args.overwrite is True


def test_parse_args_leaves_output_overwrite_off_by_default() -> None:
    args = parse_args(["--output", "out.json", "1.1.1.1"])

    assert args.overwrite is False


def test_main_reports_output_write_errors_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(*_: object, **__: object) -> int:
        raise FileExistsError("output file already exists")

    import pinghue.runner as runner

    monkeypatch.setattr(runner, "run", fake_run)

    assert main(["--output", "out.json", "1.1.1.1"]) == 1
    captured = capsys.readouterr()
    assert "output file already exists" in captured.err
    assert "Traceback" not in captured.err


def test_main_warns_when_tui_starts_without_a_terminal_on_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(*_: object, **__: object) -> int:
        return 0

    import pinghue.runner as runner

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    assert main(["1.1.1.1"]) == 0
    assert "stdout is not a terminal; pass --no-tui" in capsys.readouterr().err


def test_main_does_not_warn_about_stdout_when_no_tui_is_requested(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(*_: object, **__: object) -> int:
        return 0

    import pinghue.runner as runner

    monkeypatch.setattr(runner, "run", fake_run)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    assert main(["--no-tui", "1.1.1.1"]) == 0
    assert capsys.readouterr().err == ""


def test_main_no_tui_output_dash_keeps_stdout_json_parseable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]

        exit_code = main(
            ["-p", str(port), "127.0.0.1", "-c", "1", "--no-tui", "--output", "-"]
        )

    assert exit_code == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["schema_version"] == 1
    # The per-probe line moves to stderr so stdout stays machine-parseable.
    assert "127.0.0.1" in captured.err


def test_parse_args_rejects_output_dash_without_no_tui(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--output", "-", "1.1.1.1"])

    assert excinfo.value.code == 2
    assert "--output - requires --no-tui" in capsys.readouterr().err


def test_parse_args_accepts_output_dash_with_no_tui() -> None:
    args = parse_args(["--output", "-", "--no-tui", "1.1.1.1"])

    assert str(args.output) == "-"
    assert args.no_tui is True


def test_parse_args_allows_check_with_output_dash() -> None:
    # --check ignores --output entirely; the --no-tui requirement only
    # applies to probe runs.
    args = parse_args(["--check", "--output", "-"])

    assert args.check is True


def test_main_runs_output_dash_with_no_tui_without_stderr_noise(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run(*_: object, **__: object) -> int:
        return 0

    import pinghue.runner as runner

    monkeypatch.setattr(runner, "run", fake_run)

    assert main(["--output", "-", "--no-tui", "1.1.1.1"]) == 0
    assert capsys.readouterr().err == ""


def test_parse_args_numeric_sets_family_from_ip_literal() -> None:
    args = parse_args(["--numeric", "2606:4700:4700::1111"])

    assert args.numeric is True
    assert args.address_family == "ipv6"


def test_parse_args_numeric_uses_auto_family_for_mixed_ip_literals() -> None:
    args = parse_args(["--numeric", "1.1.1.1", "2606:4700:4700::1111"])

    assert args.numeric is True
    assert args.address_family == "auto"


def test_parse_args_accepts_check_dns_override() -> None:
    args = parse_args(["--check", "--resolve-name", "internal.example"])

    assert args.check is True
    assert args.resolve_name == "internal.example"


def test_parse_args_accepts_json_host_label() -> None:
    args = parse_args(["--host-label", "maintenance-window", "1.1.1.1"])

    assert args.host_label == "maintenance-window"


def test_parse_args_accepts_fail_on_down() -> None:
    args = parse_args(["--fail-on-down", "1.1.1.1"])

    assert args.fail_on_all_down is True


def test_parse_args_accepts_explicit_failure_modes() -> None:
    any_down = parse_args(["--fail-on-any-down", "1.1.1.1"])
    all_down = parse_args(["--fail-on-all-down", "1.1.1.1"])

    assert any_down.fail_on_any_down is True
    assert any_down.fail_on_all_down is False
    assert all_down.fail_on_any_down is False
    assert all_down.fail_on_all_down is True


def test_parse_args_rejects_multiple_failure_modes() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--fail-on-any-down", "--fail-on-all-down", "1.1.1.1"])


def test_parse_args_rejects_zero_concurrency() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--concurrency", "0", "1.1.1.1"])


def test_parse_args_accepts_concurrency_at_maximum() -> None:
    args = parse_args(["--concurrency", str(CONCURRENCY_MAXIMUM), "1.1.1.1"])

    assert args.concurrency == CONCURRENCY_MAXIMUM


def test_parse_args_rejects_concurrency_above_maximum() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--concurrency", str(CONCURRENCY_MAXIMUM + 1), "1.1.1.1"])


def test_parse_args_output_mode_defaults_to_private() -> None:
    assert parse_args(["1.1.1.1"]).output_mode == "private"


def test_parse_args_accepts_output_mode_umask() -> None:
    assert parse_args(["--output-mode", "umask", "1.1.1.1"]).output_mode == "umask"


def test_parse_args_rejects_invalid_output_mode() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--output-mode", "bogus", "1.1.1.1"])


def test_parse_args_numeric_non_ip_literal_exits_cleanly() -> None:
    # H2: a non-IP --numeric target must exit via parser.error (SystemExit),
    # not propagate an uncaught argparse.ArgumentTypeError.
    with pytest.raises(SystemExit):
        parse_args(["-n", "notanip"])


def test_parse_args_numeric_error_sanitizes_control_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # H2: control bytes in a --numeric target must be escaped before reaching stderr.
    with pytest.raises(SystemExit):
        parse_args(["-n", "x\x1b]0;PWNED\x07notip"])
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "\\x1b" in captured.err


def test_parse_args_numeric_ipv4_flag_conflicts_with_ipv6_literal() -> None:
    # M3: --ipv4 with an IPv6 literal under --numeric must be rejected, not silently overridden.
    with pytest.raises(SystemExit):
        parse_args(["-n", "--ipv4", "::1"])


def test_parse_args_numeric_ipv6_flag_conflicts_with_ipv4_literal() -> None:
    # M3: --ipv6 with an IPv4 literal under --numeric must be rejected.
    with pytest.raises(SystemExit):
        parse_args(["-n", "--ipv6", "1.1.1.1"])


def test_parse_args_numeric_respects_ipv4_flag() -> None:
    args = parse_args(["-n", "--ipv4", "1.1.1.1"])
    assert args.address_family == "ipv4"


def test_parse_args_numeric_ipv4_flag_rejects_mixed_ipv6_literal() -> None:
    # M3: an explicit --ipv4 must not be silently dropped for mixed literals.
    with pytest.raises(SystemExit):
        parse_args(["--ipv4", "-n", "1.1.1.1", "::1"])


def test_parse_args_strips_target_whitespace() -> None:
    # L8: CLI targets are normalized the same way host-file targets are.
    args = parse_args(["  1.1.1.1  "])
    assert args.targets == ["1.1.1.1"]


def test_parse_args_rejects_control_characters_in_target() -> None:
    # L8: embedded control characters in a CLI target are rejected.
    with pytest.raises(SystemExit):
        parse_args(["a\nb"])


def test_parse_args_sanitizes_host_file_error_control_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # L9: host-file parse errors must not echo raw control bytes from the path.
    with pytest.raises(SystemExit):
        parse_args(["--file", "no\x1bsuchfile"])
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "\\x1b" in captured.err


def test_main_sanitizes_control_chars_in_error_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # L10: the top-level OSError handler must escape control bytes from exception text.
    async def fake_run(*_: object, **__: object) -> int:
        raise FileExistsError("output file already exists: \x1bbad")

    import pinghue.runner as runner

    monkeypatch.setattr(runner, "run", fake_run)

    assert main(["--output", "out.json", "1.1.1.1"]) == 1
    captured = capsys.readouterr()
    assert "\x1b" not in captured.err
    assert "\\x1b" in captured.err


def test_python_dash_m_entrypoint_reports_version() -> None:
    src_dir = Path(__file__).resolve().parent.parent / "src"
    env = {**os.environ, "PYTHONPATH": str(src_dir)}

    result = subprocess.run(
        [sys.executable, "-m", "pinghue", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert result.stdout.startswith("pinghue ")


@pytest.mark.parametrize(
    ("option", "message"),
    [
        ("-c", "count must be greater than 0"),
        ("--duration", "duration must be greater than 0"),
        ("--jitter-threshold", "jitter-threshold must be greater than or equal to 0"),
    ],
)
def test_parse_args_negative_values_reach_dedicated_validators(
    option: str,
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Without the -4/-6 numeric-looking flags, argparse passes "-3" through as
    # a value, so the dedicated range validators fire with a clear message.
    with pytest.raises(SystemExit):
        parse_args([option, "-3", "1.1.1.1"])
    assert message in capsys.readouterr().err


def test_parse_args_rejects_dash_prefixed_target(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Stale "-4"/"-6" flag usage parses as a numeric-looking positional; it
    # must be rejected as an invalid target, not probed or TUI-launched.
    with pytest.raises(SystemExit):
        parse_args(["-4", "1.1.1.1"])
    assert "invalid target" in capsys.readouterr().err
