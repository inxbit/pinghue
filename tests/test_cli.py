import pytest

from pinghue.cli import parse_args


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
