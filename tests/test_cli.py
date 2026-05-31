import pytest

from pinghue.cli import CONCURRENCY_MAXIMUM, HOST_LABEL_MAXIMUM, TARGET_MAXIMUM, main, parse_args


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

    assert args.fail_on_down is True
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
    # M3: -4 with an IPv6 literal under --numeric must be rejected, not silently overridden.
    with pytest.raises(SystemExit):
        parse_args(["-n", "-4", "::1"])


def test_parse_args_numeric_ipv6_flag_conflicts_with_ipv4_literal() -> None:
    # M3: -6 with an IPv4 literal under --numeric must be rejected.
    with pytest.raises(SystemExit):
        parse_args(["-n", "-6", "1.1.1.1"])


def test_parse_args_numeric_respects_ipv4_flag() -> None:
    args = parse_args(["-n", "-4", "1.1.1.1"])
    assert args.address_family == "ipv4"


def test_parse_args_numeric_ipv4_flag_rejects_mixed_ipv6_literal() -> None:
    # M3: an explicit -4 must not be silently dropped for mixed literals.
    with pytest.raises(SystemExit):
        parse_args(["-4", "-n", "1.1.1.1", "::1"])


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
