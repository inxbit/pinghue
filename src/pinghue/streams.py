"""Writing to standard streams whose reader may have gone away."""

from __future__ import annotations

import errno
import os
from typing import TextIO


def reader_is_gone(error: BaseException) -> bool:
    """Return whether a write failed because nothing reads the stream any more."""
    if isinstance(error, (BrokenPipeError, ConnectionResetError)):
        return True
    # A hung-up terminal fails writes with EIO.
    return isinstance(error, OSError) and error.errno == errno.EIO


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
        if not reader_is_gone(exc):
            raise
        discard_output(stream)
