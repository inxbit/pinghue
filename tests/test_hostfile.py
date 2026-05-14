from pathlib import Path

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
