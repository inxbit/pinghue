from pathlib import Path

import pytest

from pinghue.hostfile import parse_host_file


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
