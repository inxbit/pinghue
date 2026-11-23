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


# Runs the CLI in a child that gets a stop signal while its report is being
# built: the moment an operator presses Ctrl-C a second time, or a service
# manager repeats its signal, after the run has already stopped.
_SIGNAL_DURING_REPORT = """
import os
import signal
import sys

import pinghue.export as export
from pinghue.cli import main

build = export.build_output_document


def build_while_signalled(**kwargs):
    os.kill(os.getpid(), getattr(signal, sys.argv[1]))
    return build(**kwargs)


export.build_output_document = build_while_signalled
sys.exit(main(sys.argv[2:]))
"""


@pytest.mark.parametrize("signal_name", ["SIGINT", "SIGTERM", "SIGHUP"])
def test_a_stop_signal_while_the_report_is_written_does_not_lose_it(
    tmp_path: Path, signal_name: str
) -> None:
    # The stop-signal handlers are gone once probing has ended. A signal that
    # arrived while the report was built then ended the process: a traceback
    # (or a silent death) and no report, although an interrupted run is
    # documented to write it.
    if signal.getsignal(getattr(signal, signal_name)) is signal.SIG_IGN:
        pytest.skip(f"{signal_name} is ignored by the test runner and its children")
    report = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _SIGNAL_DURING_REPORT,
            signal_name,
            "-n",
            "-p",
            str(_closed_loopback_port()),
            "--no-tui",
            "-c",
            "1",
            "--output",
            str(report),
            "127.0.0.1",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=_cli_env(),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == b""
    assert _exit_reason(report) == "completed"


# Runs the CLI in a child that says on stderr when it starts on its report,
# with the grace period for repeated stop signals given as the first argument.
_ANNOUNCED_REPORT = """
import sys

import pinghue.export as export
import pinghue.runner as runner
from pinghue.cli import main

build = export.build_output_document


def announced_build(**kwargs):
    sys.stderr.write("building the report\\n")
    sys.stderr.flush()
    return build(**kwargs)


export.build_output_document = announced_build
runner.REPORT_SIGNAL_GRACE_SECONDS = float(sys.argv[1])
sys.exit(main(sys.argv[2:]))
"""


def test_a_report_write_that_never_ends_can_be_interrupted_after_the_grace_period(
    tmp_path: Path,
) -> None:
    # A reader that holds the FIFO open and never reads holds the write for
    # good. A repeated Ctrl-C is held back at first, like for any report, but
    # not for ever: after the grace period it ends the process. The report is
    # larger than any pipe buffer.
    grace = 5.0
    fifo = tmp_path / "report.fifo"
    os.mkfifo(fifo, 0o600)
    hosts = tmp_path / "hosts.txt"
    hosts.write_text("".join(f"127.0.{index // 250}.{index % 250 + 1}\n" for index in range(600)))
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _ANNOUNCED_REPORT,
            str(grace),
            "-n",
            "-p",
            str(_closed_loopback_port()),
            "--no-tui",
            "-c",
            "1",
            "-i",
            "0.1",
            "--timeout",
            "0.2",
            "-f",
            str(hosts),
            "--output",
            str(fifo),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=_cli_env(),
    )
    try:
        assert proc.stderr is not None
        announced = _read_until(proc.stderr.fileno(), b"building the report\n", timeout=60.0)
        started = time.monotonic()
        time.sleep(0.3)
        proc.send_signal(signal.SIGINT)
        time.sleep(0.7)
        alive_after_the_held_signal = proc.poll() is None
        time.sleep(max(0.0, started + grace + 0.5 - time.monotonic()))
        proc.send_signal(signal.SIGINT)
        try:
            returncode: int | None = proc.wait(timeout=EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            returncode = None
    finally:
        _stop(proc)
        os.close(reader)

    assert b"building the report\n" in announced
    assert alive_after_the_held_signal, "the first repeated signal ended the write"
    assert returncode is not None, "SIGINT did not end a write that never ends"
    assert returncode != 0


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


def _run_with_stdout_closed(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    """Run ``command`` as a shell's ``>&-`` does: descriptor 1 closed at startup."""
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=_cli_env(),
        preexec_fn=lambda: os.close(1),
        timeout=EXIT_TIMEOUT,
        check=False,
    )


def _check_command(*arguments: str) -> list[str]:
    # A local name keeps the DNS part of the check off the network.
    return [sys.executable, "-m", "pinghue", "--check", "--resolve-name", "localhost", *arguments]


def _check_exit_status(*arguments: str) -> int:
    return subprocess.run(
        _check_command(*arguments),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=_cli_env(),
        timeout=EXIT_TIMEOUT,
        check=False,
    ).returncode


@pytest.mark.parametrize("arguments", [(), ("--quiet",)], ids=["check", "check-quiet"])
def test_check_with_stdout_closed_keeps_its_exit_status(arguments: tuple[str, ...]) -> None:
    # `pinghue --check --quiet >&-` died on `None.isatty()`: a traceback and
    # status 1 on a host that is ready.
    result = _run_with_stdout_closed(_check_command(*arguments))

    assert result.returncode == _check_exit_status(*arguments)
    assert b"Traceback" not in result.stderr


def test_output_dash_with_stdout_closed_is_refused_before_probing() -> None:
    # The run probed, then died writing the report to a stdout of None. With
    # `--output -` the probe lines go to stderr, so none must show up there.
    result = _run_with_stdout_closed(_cli_command("--no-tui", "--output", "-", "-c", "1"))

    assert result.returncode == 1
    assert b"Traceback" not in result.stderr
    assert result.stderr.startswith(b"pinghue: error: ")
    assert b"stdout is closed" in result.stderr
    assert b"127.0.0.1" not in result.stderr


def test_check_prints_on_a_stdout_that_cannot_encode_its_symbols() -> None:
    # The report's dash and arrows are not ASCII; on a stdout with a legacy
    # encoding `--check` died with UnicodeEncodeError.
    result = subprocess.run(
        _check_command(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=_cli_env(PYTHONIOENCODING="ascii"),
        timeout=EXIT_TIMEOUT,
        check=False,
    )

    assert result.returncode == _check_exit_status()
    assert b"Traceback" not in result.stderr
    text = result.stdout.decode("ascii")
    assert "- environment check" in text
    assert 'getaddrinfo("localhost")' in text


@pytest.mark.parametrize("flag", ["--help", "--version"])
def test_help_and_version_into_a_closed_pipe_stop_quietly(flag: str) -> None:
    # `pinghue --help | head -0` printed "Exception ignored ... BrokenPipeError"
    # and exited 120, like `--check` and `--output -` before 7.1.1.
    result = _run_into_a_closed_pipe([sys.executable, "-m", "pinghue", flag])

    assert result.returncode == 0
    assert b"Broken pipe" not in result.stderr
    assert b"Exception ignored" not in result.stderr


def _cap_file_size_at_one_kibibyte() -> None:
    import resource

    resource.setrlimit(resource.RLIMIT_FSIZE, (1024, 1024))


def test_output_dash_write_failure_exits_with_the_runtime_error_status(tmp_path: Path) -> None:
    # `pinghue ... --output - > report.json` on storage that refuses the write
    # (here a file size limit; a full disk fails the same way) reported the
    # error, then failed again in the flush at exit: "Exception ignored" and
    # status 120 instead of 1.
    with (tmp_path / "report.json").open("wb") as report:
        result = subprocess.run(
            _cli_command("--no-tui", "--output", "-", "-c", "8", "-i", "0.1"),
            stdin=subprocess.DEVNULL,
            stdout=report,
            stderr=subprocess.PIPE,
            env=_cli_env(),
            preexec_fn=_cap_file_size_at_one_kibibyte,
            timeout=EXIT_TIMEOUT,
            check=False,
        )

    assert b"pinghue: error: " in result.stderr
    assert b"Exception ignored" not in result.stderr
    assert result.returncode == 1


def test_help_into_a_file_that_refuses_the_write_exits_with_the_runtime_error_status(
    tmp_path: Path,
) -> None:
    # The help text is longer than the file size limit, so the flush fails
    # with a real write error, not a reader that went away: that must not
    # pass as success, nor fail again in the flush at exit (status 120).
    with (tmp_path / "help.txt").open("wb") as help_file:
        result = subprocess.run(
            [sys.executable, "-m", "pinghue", "--help"],
            stdin=subprocess.DEVNULL,
            stdout=help_file,
            stderr=subprocess.PIPE,
            env=_cli_env(),
            preexec_fn=_cap_file_size_at_one_kibibyte,
            timeout=EXIT_TIMEOUT,
            check=False,
        )

    assert b"pinghue: error: " in result.stderr
    assert b"Exception ignored" not in result.stderr
    assert result.returncode == 1
