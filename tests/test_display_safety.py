import unicodedata
from datetime import UTC, datetime
from typing import Any

import pytest

from pinghue.display import sanitize_display
from pinghue.models import ProbeSample, SampleStatus, TargetRun, TargetStatus
from pinghue.runner import print_sample
from pinghue.ui import COLUMN_KEYS, TargetTableRenderer, compute_table_layout


def test_sanitize_display_escapes_terminal_control_characters() -> None:
    assert sanitize_display("safe-host") == "safe-host"
    assert sanitize_display("bad\x1b[31mhost\nnext") == "bad\\x1b[31mhost\\x0anext"
    assert sanitize_display("bad\x85host") == "bad\\x85host"
    assert sanitize_display("bad\u202ehost") == "bad\\u202ehost"
    assert sanitize_display("bad\u200bhost") == "bad\\u200bhost"
    assert sanitize_display("g\u03bf\u03bfgle.example") == "g\\u03bf\\u03bfgle.example"


def test_no_tui_output_escapes_target_and_error_controls(capsys) -> None:
    sample = ProbeSample(
        timestamp=datetime(2026, 5, 14, tzinfo=UTC),
        latency_ms=None,
        status=SampleStatus.ERROR,
        error="bad\x1b[2Jerror",
    )
    target = TargetRun("host\x1b[31m")

    print_sample(target, sample)

    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "host\\x1b[31m" in output
    assert "bad\\x1b[2Jerror" in output


class RecordingTable:
    """Stands in for the DataTable: keeps the latest plain text of every cell."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, str]] = {}

    def add_row(self, *cells: Any, key: str) -> str:
        self.rows[key] = {
            column: cell.plain for column, cell in zip(COLUMN_KEYS, cells, strict=True)
        }
        return key

    def update_cell(self, row_key: Any, column_key: Any, value: Any, *, update_width: bool) -> None:
        assert update_width is False
        row = str(getattr(row_key, "value", row_key))
        self.rows[row][str(getattr(column_key, "value", column_key))] = value.plain


def _is_control(character: str) -> bool:
    # Cc/Cf/Cs/Co/Cn: C0/C1 controls, bidi and other format characters, and
    # unassigned code points; Zl/Zp: line and paragraph separators.
    return unicodedata.category(character)[0] == "C" or unicodedata.category(character) in {
        "Zl",
        "Zp",
    }


HOSTILE_NAMES = [
    "evil\x1b[31m-01.example.net",
    "evil\x1b[31m-02.example.net",
    "line\nbreak.example.net",
    "rtl\u202eten.example.net",
    "nel\x85sep\u2028.example.net",
]


@pytest.mark.parametrize("show_address", [False, True])
def test_tui_table_cells_escape_control_characters(show_address: bool) -> None:
    sample = ProbeSample(
        timestamp=datetime(2026, 5, 14, tzinfo=UTC),
        latency_ms=None,
        status=SampleStatus.ERROR,
        error="bad\x1b[2Jerror",
    )
    targets = [
        TargetRun(
            name,
            resolved_address="127.0.253.1\x1b[2J",
            status=TargetStatus.DOWN,
            samples=[sample],
        )
        for name in HOSTILE_NAMES
    ]
    table = RecordingTable()
    renderer = TargetTableRenderer(
        mode="icmp",
        history_style="bar",
        jitter_threshold_ms=50.0,
        slow_latency_ms=300.0,
    )
    widths = compute_table_layout(width=200, targets=targets, show_address=show_address)

    # Whole names first, then the same rows updated in place with names
    # shortened to fit: both paths go through the renderer's host labels.
    for host_width in (64, 14):
        renderer.set_layout(show_address=show_address, widths=dict(widths, host=host_width))
        renderer.sync(table, targets)

        rows = list(table.rows.values())
        assert len(rows) == len(HOSTILE_NAMES)
        for row in rows:
            for column, text in row.items():
                assert not any(_is_control(character) for character in text), (column, text)
        hosts = [row["host"] for row in rows]
        assert all(len(host) <= host_width for host in hosts)
        assert len(set(hosts)) == len(hosts)
        if host_width == 64:
            assert hosts == [sanitize_display(name) for name in HOSTILE_NAMES]
            assert hosts[0] == "evil\\x1b[31m-01.example.net"
        expected_address = "127.0.253.1\\x1b[2J" if show_address else ""
        assert {row["address"] for row in rows} == {expected_address}
