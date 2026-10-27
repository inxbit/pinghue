"""Host-file parsing."""

from __future__ import annotations

import errno
import os
import re
import stat
from pathlib import Path

MAX_HOST_FILE_BYTES = 1024 * 1024
TARGET_COUNT_MAXIMUM = 5_000
MAX_HOST_FILE_LINES = TARGET_COUNT_MAXIMUM
TARGET_MAXIMUM = 253
LINE_BREAK_PATTERN = re.compile(r"\r\n|\r|\n")


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        # Without O_NONBLOCK a read-only open() on a FIFO blocks until a writer
        # appears, so the fstat() regular-file check below would never run.
        flags |= os.O_NONBLOCK

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ENOTDIR}:
            raise ValueError(f"host file not found: {path}") from exc
        if exc.errno == errno.ELOOP:
            raise ValueError(f"host file must be a regular file, not a symlink: {path}") from exc
        raise ValueError(f"cannot read host file {path}: {exc.strerror}") from exc

    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"host file must be a regular file: {path}")

        if file_stat.st_size > MAX_HOST_FILE_BYTES:
            raise ValueError(f"host file too large; maximum is {MAX_HOST_FILE_BYTES} bytes: {path}")

        with os.fdopen(fd, "rb") as file:
            fd = -1
            data = file.read(MAX_HOST_FILE_BYTES + 1)

        if len(data) > MAX_HOST_FILE_BYTES:
            raise ValueError(f"host file too large; maximum is {MAX_HOST_FILE_BYTES} bytes: {path}")

        return data
    finally:
        if fd >= 0:
            os.close(fd)


def parse_host_file(path: str | Path) -> list[str]:
    """Return targets from a plain text host file."""
    host_path = Path(path)
    try:
        # utf-8-sig strips a leading BOM, common in Windows-authored files.
        content = _read_regular_file(host_path).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"host file is not valid UTF-8: {host_path}") from exc

    targets: list[str] = []

    # Only "\n", "\r\n" and a lone "\r" end a line. str.splitlines() also splits
    # on \v, \f, \x1c-\x1e, \x85, U+2028 and U+2029, which would turn one line
    # into several targets and skew line numbers and the line limit; such
    # characters stay inside the value for target validation instead.
    lines = LINE_BREAK_PATTERN.split(content)
    if lines[-1] == "":
        lines.pop()

    for line_number, line in enumerate(lines, start=1):
        if line_number > MAX_HOST_FILE_LINES:
            raise ValueError(
                f"host file has too many lines; maximum is {MAX_HOST_FILE_LINES}: {host_path}"
            )

        value = line.split("#", 1)[0].strip()

        if not value:
            continue

        if len(value) > TARGET_MAXIMUM:
            raise ValueError(
                f"target too long; maximum is {TARGET_MAXIMUM} characters: "
                f"{host_path} line {line_number}"
            )

        targets.append(value)

    return targets
