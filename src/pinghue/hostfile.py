"""Host-file parsing."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

MAX_HOST_FILE_BYTES = 1024 * 1024
MAX_HOST_FILE_LINES = 5_000
TARGET_MAXIMUM = 253


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOENT, errno.ENOTDIR}:
            raise ValueError(f"host file must be a regular file: {path}") from exc
        raise

    try:
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"host file must be a regular file: {path}")

        if file_stat.st_size > MAX_HOST_FILE_BYTES:
            raise ValueError(
                f"host file too large; maximum is {MAX_HOST_FILE_BYTES} bytes: {path}"
            )

        with os.fdopen(fd, "rb") as file:
            fd = -1
            data = file.read(MAX_HOST_FILE_BYTES + 1)

        if len(data) > MAX_HOST_FILE_BYTES:
            raise ValueError(
                f"host file too large; maximum is {MAX_HOST_FILE_BYTES} bytes: {path}"
            )

        return data
    finally:
        if fd >= 0:
            os.close(fd)


def parse_host_file(path: str | Path) -> list[str]:
    """Return targets from a plain text host file."""
    host_path = Path(path)
    content = _read_regular_file(host_path).decode("utf-8")

    targets: list[str] = []

    for line_number, line in enumerate(content.splitlines(), start=1):
        if line_number > MAX_HOST_FILE_LINES:
            raise ValueError(
                f"host file has too many lines; maximum is {MAX_HOST_FILE_LINES}: {host_path}"
            )

        value = line.split("#", 1)[0].strip()

        if not value:
            continue

        if len(value) > TARGET_MAXIMUM:
            raise ValueError(
                f"target too long; maximum is {TARGET_MAXIMUM} characters: line {line_number}"
            )

        targets.append(value)

    return targets
