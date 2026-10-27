import os
import re
import signal
from pathlib import Path

import pytest

from pinghue.hostfile import MAX_HOST_FILE_LINES, TARGET_MAXIMUM, parse_host_file


def test_parse_host_file_ignores_blank_lines_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text(
        """
        # core services
        1.1.1.1

        example.com
          internal-db.corp
        # trailing comment
        """,
        encoding="utf-8",
    )

    assert parse_host_file(path) == ["1.1.1.1", "example.com", "internal-db.corp"]


def test_parse_host_file_strips_inline_comments(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text(
        """
        1.1.1.1 # DNS resolver
        my-server.internal   # staging
        """,
        encoding="utf-8",
    )

    assert parse_host_file(path) == ["1.1.1.1", "my-server.internal"]


def test_parse_host_file_rejects_non_regular_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="regular file"):
        parse_host_file(tmp_path)


def test_parse_host_file_rejects_symlink(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text("1.1.1.1\n", encoding="utf-8")
    link = tmp_path / "hosts-link.txt"
    link.symlink_to(path)

    with pytest.raises(ValueError, match="regular file"):
        parse_host_file(link)


def test_parse_host_file_reports_a_missing_file_as_not_found(tmp_path: Path) -> None:
    # A mistyped path used to read "host file must be a regular file".
    missing = tmp_path / "hosts.txt"

    with pytest.raises(ValueError, match=f"^host file not found: {re.escape(str(missing))}$"):
        parse_host_file(missing)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads files whatever their mode")
def test_parse_host_file_names_the_file_it_cannot_read(tmp_path: Path) -> None:
    # An unreadable file used to surface as a bare "[Errno 13] Permission denied".
    path = tmp_path / "hosts.txt"
    path.write_text("1.1.1.1\n", encoding="utf-8")
    path.chmod(0)

    expected = f"^cannot read host file {re.escape(str(path))}: Permission denied$"
    with pytest.raises(ValueError, match=expected):
        parse_host_file(path)


def test_parse_host_file_names_the_file_with_a_target_that_is_too_long(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text("a" * (TARGET_MAXIMUM + 1) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match=f"characters: {re.escape(str(path))} line 1$"):
        parse_host_file(path)


def test_parse_host_file_rejects_large_file(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text("a" * (1024 * 1024 + 1), encoding="utf-8")

    with pytest.raises(ValueError, match="too large"):
        parse_host_file(path)


def test_parse_host_file_rejects_too_many_lines(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text("\n".join(f"host-{index}" for index in range(5001)), encoding="utf-8")

    with pytest.raises(ValueError, match="too many lines"):
        parse_host_file(path)


def test_parse_host_file_rejects_overlong_target(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text("a" * (TARGET_MAXIMUM + 1), encoding="utf-8")

    with pytest.raises(ValueError, match="target too long"):
        parse_host_file(path)


def test_parse_host_file_rejects_fifo_without_hanging(tmp_path: Path) -> None:
    # M4: a FIFO must be rejected as a non-regular file, not block forever on open().
    fifo = tmp_path / "hosts.fifo"
    os.mkfifo(fifo)

    def _timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError("parse_host_file blocked opening a FIFO")

    previous = signal.signal(signal.SIGALRM, _timeout)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        with pytest.raises(ValueError, match="regular file"):
            parse_host_file(fifo)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def test_parse_host_file_strips_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_bytes(b"\xef\xbb\xbf1.1.1.1\nexample.com\n")

    assert parse_host_file(path) == ["1.1.1.1", "example.com"]


def test_parse_host_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_bytes(b"\xff\xfe1.1.1.1\n")

    with pytest.raises(ValueError, match="not valid UTF-8"):
        parse_host_file(path)


@pytest.mark.parametrize(
    "separator",
    ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"],
)
def test_parse_host_file_keeps_other_line_breaks_inside_the_line(
    tmp_path: Path, separator: str
) -> None:
    # str.splitlines() also breaks on these characters; one host-file line must
    # stay one target so target validation sees the character.
    path = tmp_path / "hosts.txt"
    path.write_text(f"127.0.0.1{separator}127.0.0.2\n127.0.0.3\n", encoding="utf-8")

    assert parse_host_file(path) == [f"127.0.0.1{separator}127.0.0.2", "127.0.0.3"]


def test_parse_host_file_accepts_crlf_and_lone_carriage_return(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_bytes(b"127.0.0.1\r\n# comment\r\n127.0.0.2 # inline\r\n127.0.0.3\r127.0.0.4\r\n")

    assert parse_host_file(path) == ["127.0.0.1", "127.0.0.2", "127.0.0.3", "127.0.0.4"]


def test_parse_host_file_line_numbers_ignore_unicode_separators(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_text(
        "one\u2028two\x0cthree\n" + "a" * (TARGET_MAXIMUM + 1) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="line 2$"):
        parse_host_file(path)


def test_parse_host_file_line_limit_counts_only_real_line_ends(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    lines = [f"host-{index}" for index in range(MAX_HOST_FILE_LINES)]
    lines[0] = "host-a\u2028host-b\x85host-c"

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert len(parse_host_file(path)) == MAX_HOST_FILE_LINES

    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    assert len(parse_host_file(path)) == MAX_HOST_FILE_LINES

    path.write_text("\n".join([*lines, "extra"]), encoding="utf-8")
    with pytest.raises(ValueError, match="too many lines"):
        parse_host_file(path)


def test_parse_host_file_accepts_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "hosts.txt"
    path.write_bytes(b"")

    assert parse_host_file(path) == []
