"""End-to-end process tests: signals and output delivery of the real CLI.

Each test runs ``python -m pinghue`` from this checkout against a closed TCP
port on 127.0.0.1, so probes stay on loopback and finish at once as refused.
"""

from __future__ import annotations

import json
import os
import re
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals and ptys")

SRC = Path(__file__).resolve().parents[1] / "src"
STARTUP_TIMEOUT = 20.0
EXIT_TIMEOUT = 20.0


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


def _cli_command(*args: str) -> list[str]:
    port = str(_closed_loopback_port())
    return [sys.executable, "-m", "pinghue", "-n", "-p", port, *args, "127.0.0.1"]


def _cli_env(**extra: str) -> dict[str, str]:
    env = dict(os.environ)
    # CI and dev shells often set this, which hides block-buffered pipe output.
    env.pop("PYTHONUNBUFFERED", None)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC), env.get("PYTHONPATH")]))
    env.update(extra)
    return env


def _read_until(fd: int, marker: bytes, *, timeout: float) -> bytes:
    """Read from ``fd`` until ``marker`` appears, EOF, or ``timeout``."""
    received = b""
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while marker not in received:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            received += chunk
    return received


def _stop(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=EXIT_TIMEOUT)
    # Pipes left open warn as unclosed files when collected.
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


def _exit_reason(report: Path) -> str:
    document = json.loads(report.read_text())
    reason: str = document["run"]["exit_reason"]
    return reason


def test_no_tui_lines_reach_a_pipe_while_the_run_is_still_going(tmp_path: Path) -> None:
    # With stdout on a pipe, lines used to sit in an 8 KiB block buffer,
    # which at one line per second takes more than a minute to fill.
    report = tmp_path / "report.json"
    proc = subprocess.Popen(
        _cli_command("--no-tui", "-i", "1", "--duration", "120", "--output", str(report)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(),
    )
    try:
        assert proc.stdout is not None
        first_lines = _read_until(proc.stdout.fileno(), b"\n", timeout=STARTUP_TIMEOUT)
        still_running = proc.poll() is None
        proc.send_signal(signal.SIGTERM)
        returncode = proc.wait(timeout=EXIT_TIMEOUT)
    finally:
        _stop(proc)

    assert b" 127.0.0.1 refused latency=-" in first_lines
    assert still_running
    assert returncode == 0
    assert _exit_reason(report) == "interrupted"


def test_no_tui_stops_cleanly_when_the_pipe_reader_exits(tmp_path: Path) -> None:
    # `pinghue --no-tui ... | head -1` must stop the run and still write
    # the report, without a traceback or "Exception ignored" at exit.
    report = tmp_path / "report.json"
    proc = subprocess.Popen(
        _cli_command("--no-tui", "-i", "0.1", "--duration", "60", "--output", str(report)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_cli_env(),
    )
    try:
        assert proc.stdout is not None and proc.stderr is not None
        first_lines = _read_until(proc.stdout.fileno(), b"\n", timeout=STARTUP_TIMEOUT)
        proc.stdout.close()
        returncode = proc.wait(timeout=EXIT_TIMEOUT)
        stderr = proc.stderr.read()
    finally:
        _stop(proc)

    assert b"refused" in first_lines
    assert returncode == 0, stderr
    assert b"Traceback" not in stderr
    assert b"Exception ignored" not in stderr
    assert _exit_reason(report) == "interrupted"


@pytest.mark.parametrize("signal_name", ["SIGTERM", "SIGHUP"])
def test_tui_restores_the_terminal_and_writes_output_on_signal(
    tmp_path: Path, signal_name: str
) -> None:
    # SIGTERM/SIGHUP used to kill the TUI in the alternate screen with raw
    # mode left on and without writing --output.
    pty = pytest.importorskip("pty")
    termios = pytest.importorskip("termios")
    fcntl = pytest.importorskip("fcntl")
    signal_number = getattr(signal, signal_name)
    if signal.getsignal(signal_number) is signal.SIG_IGN:
        pytest.skip(f"{signal_name} is ignored by the test runner and its children")

    report = tmp_path / "report.json"
    controller, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, bytes([30, 0, 120, 0, 0, 0, 0, 0]))
    proc = subprocess.Popen(
        _cli_command("-i", "0.2", "--output", str(report)),
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        env=_cli_env(TERM="xterm-256color"),
        start_new_session=True,
    )
    os.close(terminal)
    screen = bytearray()

    def drain() -> None:
        while True:
            try:
                chunk = os.read(controller, 65536)
            except OSError:
                return
            if not chunk:
                return
            screen.extend(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while b"127.0.0.1" not in screen and time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert b"\x1b[?1049h" in screen, "the TUI never entered the alternate screen"
        assert b"127.0.0.1" in screen, "the TUI never rendered its target row"

        proc.send_signal(signal_number)
        returncode = proc.wait(timeout=EXIT_TIMEOUT)
        reader.join(timeout=5.0)
    finally:
        _stop(proc)
        os.close(controller)

    assert returncode == 0
    assert b"\x1b[?1049l" in screen, "the TUI did not leave the alternate screen"
    assert _exit_reason(report) == "interrupted"


def test_tui_exits_at_once_when_stderr_is_closed(tmp_path: Path) -> None:
    # The TUI draws on stderr. With descriptor 2 closed, Textual's output
    # thread died, the app hung before probing and ignored SIGTERM and SIGHUP.
    report = tmp_path / "report.json"
    result = subprocess.run(
        _cli_command("--tui", "-c", "1", "-i", "0.2", "--output", str(report)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        env=_cli_env(),
        preexec_fn=lambda: os.close(2),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == b""
    assert not report.exists()


def test_a_run_without_a_terminal_prints_lines_instead_of_drawing_the_tui(
    tmp_path: Path,
) -> None:
    # Under cron or CI neither stdout nor stderr is a terminal. Before 7.0.0
    # the TUI started anyway: stdout stayed empty and stderr got a warning
    # and screens of escape sequences.
    report = tmp_path / "report.json"
    result = subprocess.run(
        _cli_command("-c", "1", "--output", str(report)),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=_cli_env(),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == b""
    assert b"127.0.0.1 refused" in result.stdout
    assert b"\x1b" not in result.stdout
    assert json.loads(report.read_text())["run"]["exit_reason"] == "completed"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="closing a pty's controller hangs up its session reliably only on Linux",
)
def test_tui_exits_cleanly_when_its_terminal_hangs_up(tmp_path: Path) -> None:
    # A closed terminal window or dropped SSH session hangs up the TUI's
    # controlling tty. The terminal-restore output can then never be written,
    # and the flush at exit used to fail and turn the exit status into 120.
    pty = pytest.importorskip("pty")
    termios = pytest.importorskip("termios")
    fcntl = pytest.importorskip("fcntl")
    if signal.getsignal(signal.SIGHUP) is signal.SIG_IGN:
        pytest.skip("SIGHUP is ignored by the test runner and its children")

    report = tmp_path / "report.json"
    controller, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, bytes([30, 0, 120, 0, 0, 0, 0, 0]))

    def take_controlling_terminal() -> None:
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    proc = subprocess.Popen(
        _cli_command("-i", "0.2", "--output", str(report)),
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        env=_cli_env(TERM="xterm-256color"),
        preexec_fn=take_controlling_terminal,
    )
    os.close(terminal)
    try:
        screen = _read_until(controller, b"127.0.0.1", timeout=STARTUP_TIMEOUT)
        assert b"127.0.0.1" in screen, "the TUI never rendered its target row"
        os.close(controller)
        controller = -1
        returncode = proc.wait(timeout=EXIT_TIMEOUT)
    finally:
        _stop(proc)
        if controller >= 0:
            os.close(controller)

    assert returncode == 0
    assert _exit_reason(report) == "interrupted"


def test_the_mouse_wheel_scrolls_the_tui_at_startup_in_iterm2() -> None:
    # iTerm2 answers that it supports in-band resize reports (mode 2048) but
    # sends none until its window is resized. Textual then switched the mouse
    # to pixel coordinates and read them as cells, so every wheel turn, click,
    # and scrollbar drag landed off screen until the first resize. Textual
    # skips this only when TERM_PROGRAM or LC_TERMINAL names iTerm2.
    pty = pytest.importorskip("pty")
    termios = pytest.importorskip("termios")
    fcntl = pytest.importorskip("fcntl")
    cell_width, cell_height = 8, 17

    controller, terminal = pty.openpty()
    fcntl.ioctl(terminal, termios.TIOCSWINSZ, bytes([30, 0, 120, 0, 0, 0, 0, 0]))
    env = _cli_env(TERM="xterm-256color")
    for name in ("TERM_PROGRAM", "LC_TERMINAL", "TEXTUAL_SMOOTH_SCROLL"):
        env.pop(name, None)
    proc = subprocess.Popen(
        _cli_command("-i", "0.2", *(f"127.0.0.{n}" for n in range(2, 101))),
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        env=env,
        start_new_session=True,
    )
    os.close(terminal)
    screen = bytearray()

    def answer_like_iterm2() -> None:
        answered = False
        while True:
            try:
                chunk = os.read(controller, 65536)
            except OSError:
                return
            if not chunk:
                return
            screen.extend(chunk)
            if not answered and b"\x1b[?2048$p" in screen:
                os.write(controller, b"\x1b[?2048;2$y")
                answered = True

    def addresses_drawn_since(mark: int) -> set[int]:
        drawn = re.findall(rb"127\.0\.0\.(\d+)[ \x1b]", bytes(screen[mark:]))
        return {int(last_octet) for last_octet in drawn}

    reader = threading.Thread(target=answer_like_iterm2, daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while b"127.0.0.2 " not in screen and time.monotonic() < deadline:
            time.sleep(0.05)
        assert b"127.0.0.2 " in screen, "the TUI never rendered its first row"
        # Give the terminal handshake time to switch the mouse mode, if it does.
        deadline = time.monotonic() + 2.0
        while b"\x1b[?1016h" not in screen and time.monotonic() < deadline:
            time.sleep(0.05)
        column, row = 10, 15
        if b"\x1b[?1016h" in screen:
            column, row = column * cell_width, row * cell_height
        first_screen_ends_at = max(addresses_drawn_since(0))
        mark = len(screen)
        for _ in range(10):
            os.write(controller, f"\x1b[<65;{column};{row}M".encode())
            time.sleep(0.05)
        wanted = first_screen_ends_at + 10
        deadline = time.monotonic() + 5.0
        while max(addresses_drawn_since(mark), default=0) < wanted and time.monotonic() < deadline:
            time.sleep(0.05)
        scrolled_to = max(addresses_drawn_since(mark), default=0)
    finally:
        _stop(proc)
        os.close(controller)

    assert scrolled_to >= wanted, (
        f"the first screen ended at 127.0.0.{first_screen_ends_at}, "
        f"ten wheel turns drew rows only up to 127.0.0.{scrolled_to}"
    )


def _close_stderr_under_a_low_open_file_limit() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_NOFILE, (200, 200))
    os.close(2)


def test_output_dash_stays_json_when_stderr_is_closed_and_a_warning_is_due() -> None:
    # With descriptor 2 closed, sys.stderr is None and print(file=None) writes
    # to stdout: the open-file warning used to land in front of the document.
    result = subprocess.run(
        _cli_command("--no-tui", "--output", "-", "--count", "1", "--concurrency", "1024"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        env=_cli_env(),
        preexec_fn=_close_stderr_under_a_low_open_file_limit,
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    document = json.loads(result.stdout)
    assert document["run"]["exit_reason"] == "completed"
    assert result.returncode == 0


def test_a_refused_output_path_is_not_reported_on_stdout_when_stderr_is_closed(
    tmp_path: Path,
) -> None:
    # The error is dropped with stderr closed, not printed into the line output.
    output_path = tmp_path / "report.json"
    output_path.write_text("previous\n", encoding="utf-8")

    result = subprocess.run(
        _cli_command("--no-tui", "--output", str(output_path), "--count", "1"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        env=_cli_env(),
        preexec_fn=lambda: os.close(2),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == b""


@pytest.mark.parametrize(
    "arguments",
    [("--no-tui", "--output", "-", "--interval", "0"), ("--tui", "--output", "-", "--count", "1")],
    ids=["invalid-value", "output-dash-with-tui"],
)
def test_a_usage_error_prints_nothing_on_stdout_when_stderr_is_closed(
    arguments: tuple[str, ...],
) -> None:
    # argparse printed its usage synopsis on stdout, so a pipeline into a JSON
    # reader got usage text.
    result = subprocess.run(
        _cli_command(*arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        env=_cli_env(),
        preexec_fn=lambda: os.close(2),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == b""


def _run_into_a_closed_pipe(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run ``command`` with stdout on a pipe whose reader has already gone."""
    reader, writer = os.pipe()
    os.close(reader)
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=writer,
            stderr=subprocess.PIPE,
            env=_cli_env(),
            timeout=EXIT_TIMEOUT,
            check=False,
        )
    finally:
        os.close(writer)


def test_output_dash_into_a_closed_pipe_stops_quietly() -> None:
    # `pinghue ... --output - | head -0` printed "Exception ignored ...
    # BrokenPipeError" and exited 120, or "error: [Errno 32] Broken pipe" and
    # 1 for a larger report, where line output already stopped cleanly.
    result = _run_into_a_closed_pipe(_cli_command("-c", "1", "--output", "-"))

    assert result.returncode == 0
    assert b"Broken pipe" not in result.stderr
    assert b"Exception ignored" not in result.stderr


def test_check_into_a_closed_pipe_keeps_its_exit_status() -> None:
    # `pinghue --check | head -0` exited 120 with a traceback on stderr.
    command = [sys.executable, "-m", "pinghue", "--check"]
    baseline = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_cli_env(),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    result = _run_into_a_closed_pipe(command)

    assert result.returncode == baseline.returncode
    assert b"Broken pipe" not in result.stderr
    assert b"Exception ignored" not in result.stderr
