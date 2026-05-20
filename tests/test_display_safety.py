from datetime import datetime, timezone

from pinghue.display import sanitize_display
from pinghue.models import ProbeSample, SampleStatus, TargetRun
from pinghue.runner import print_sample
from pinghue.ui import target_row_cells


def test_sanitize_display_escapes_terminal_control_characters() -> None:
    assert sanitize_display("safe-host") == "safe-host"
    assert sanitize_display("bad\x1b[31mhost\nnext") == "bad\\x1b[31mhost\\x0anext"
    assert sanitize_display("bad\x85host") == "bad\\x85host"


def test_no_tui_output_escapes_target_and_error_controls(capsys) -> None:
    sample = ProbeSample(
        timestamp=datetime(2026, 5, 14, tzinfo=timezone.utc),
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


def test_tui_target_cells_escape_control_characters() -> None:
    target = TargetRun("host\x1b[31m")

    cells = target_row_cells(
        target,
        show_address=False,
        mode="icmp",
        history_width=10,
        history_style="bar",
        jitter_threshold_ms=50.0,
        slow_latency_ms=300.0,
    )

    assert cells[0].plain == "host\\x1b[31m"
