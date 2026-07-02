import os
import signal
from pathlib import Path

import pytest

from pinghue.hostfile import TARGET_MAXIMUM, parse_host_file


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
