"""Writing to standard streams whose reader may have gone away."""

from __future__ import annotations

import errno
import os
import stat
from typing import TextIO


def _is_regular_file(stream: TextIO) -> bool:
    try:
        return stat.S_ISREG(os.fstat(stream.fileno()).st_mode)
    except (AttributeError, OSError, ValueError):
        return False


def reader_is_gone(error: BaseException, stream: TextIO | None = None) -> bool:
    """Return whether a write to ``stream`` failed because nothing reads it any more."""
    if isinstance(error, (BrokenPipeError, ConnectionResetError)):
        return True
    # A hung-up terminal fails writes with EIO. So does failing storage under a
    # redirect to a file, where no reader went away and the error is reported.
    if not isinstance(error, OSError) or error.errno != errno.EIO:
        return False
    return stream is None or not _is_regular_file(stream)


def discard_output(stream: TextIO) -> None:
    """Point ``stream``'s descriptor at the null device once its reader is gone.

    Python flushes standard streams at exit; without this, the unwritten tail
    raises BrokenPipeError again there and the exit status becomes 120.
    """
    try:
        descriptor = stream.fileno()
        devnull = os.open(os.devnull, os.O_WRONLY)
    except (AttributeError, OSError, ValueError):
        return
    try:
        os.dup2(devnull, descriptor)
    except OSError:
        pass
    finally:
        os.close(devnull)


def flush_or_discard_output(stream: TextIO | None) -> None:
    """Flush ``stream``, or discard what it still holds when writing fails.

    Restoring the terminal after a hangup leaves bytes that can never be
    written; the flush at exit would then fail and turn the exit status into 120.
    """
    if stream is None:
        return
    try:
        stream.flush()
    except (OSError, ValueError):
        discard_output(stream)


def write_text(stream: TextIO, text: str) -> None:
    """Write and flush ``text``, dropping it quietly when the reader is gone.

    ``pinghue ... --output - | head -0`` must stop like line output does, not
    print a BrokenPipeError and exit 120. Any other write error still raises.
    """
    try:
        stream.write(text)
        stream.flush()
    except OSError as exc:
        if not reader_is_gone(exc, stream):
            raise
        discard_output(stream)
