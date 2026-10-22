"""End-to-end process tests: signals and output delivery of the real CLI.

Each test runs ``python -m pinghue`` from this checkout against a closed TCP
port on 127.0.0.1, so probes stay on loopback and finish at once as refused.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipes and signals")

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
