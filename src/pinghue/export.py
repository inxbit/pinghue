"""JSON export support."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import stat
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pinghue import __version__
from pinghue.display import sanitize_display
from pinghue.models import MAX_TARGET_SAMPLES, ProbeConfig, ProbeSample, TargetRun

SCHEMA_VERSION = 1


def format_timestamp(value: datetime) -> str:
    """Return UTC RFC 3339 timestamp with millisecond precision."""
    utc_value = value.astimezone(timezone.utc)
    return utc_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sample_to_json(sample: ProbeSample) -> dict[str, Any]:
    return {
        "timestamp": format_timestamp(sample.timestamp),
        "latency_ms": sample.latency_ms,
        "status": sample.status.value,
        "error": sanitize_display(sample.error) if sample.error else None,
    }


def _target_to_json(target: TargetRun, *, include_samples: bool) -> dict[str, Any]:
    stats = asdict(target.stats)
    return {
        "target": sanitize_display(target.target),
        "resolved_address": (
            sanitize_display(target.resolved_address)
            if target.resolved_address is not None
            else None
        ),
        "resolved_family": target.resolved_family.value if target.resolved_family else None,
        "status": target.status.value,
        "error": sanitize_display(target.error) if target.error else None,
        "stats": stats,
        "samples": (
            [_sample_to_json(sample) for sample in target.samples] if include_samples else []
        ),
    }


def build_output_document(
    *,
    started_at: datetime,
    ended_at: datetime,
    host: str,
    exit_reason: str,
    probe: ProbeConfig,
    targets: list[TargetRun],
    include_samples: bool = True,
) -> dict[str, Any]:
    """Build a schema-versioned export document."""
    samples_window = max(
        (target.samples.maxlen for target in targets),
        default=MAX_TARGET_SAMPLES,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "pinghue_version": __version__,
        "run": {
            "started_at": format_timestamp(started_at),
            "ended_at": format_timestamp(ended_at),
            "host": sanitize_display(host),
            "exit_reason": exit_reason,
            "samples_window": samples_window,
            "probe": {
                "mode": probe.mode.value,
                "port": probe.port,
                "interval_s": probe.interval_s,
                "timeout_s": probe.timeout_s,
                "address_family": probe.address_family.value,
            },
        },
        "targets": [_target_to_json(target, include_samples=include_samples) for target in targets],
    }


def _output_file_mode(output_mode: str) -> int:
    """Return the permission bits for a created report file.

    "private" (default) is owner read/write only (0600); "umask" honors the
    process umask (0666 & ~umask), matching a typical CLI-created file.
    """
    if output_mode == "umask":
        current = os.umask(0)
        os.umask(current)
        return 0o666 & ~current
    return 0o600


def _output_changed_error(output_path: Path) -> OSError:
    return OSError(
        getattr(errno, "ESTALE", errno.EIO),
        f"output path changed while opening: {output_path}",
    )


def _multiple_hard_links_error(output_path: Path) -> FileExistsError:
    return FileExistsError(
        f"refusing to overwrite regular file with multiple hard links: {output_path}"
    )


def _existing_special_device_fd(output_path: Path) -> int | None:
    """Return a write fd if output_path is an existing char device or FIFO.

    Symlinks and unsupported special nodes are rejected. An opened character
    device or FIFO must match the identity observed by lstat, closing the
    lstat/open replacement race.
    """
    try:
        pre = os.lstat(output_path)
    except FileNotFoundError:
        return None
    if stat.S_ISREG(pre.st_mode):
        return None
    if stat.S_ISLNK(pre.st_mode) or not (stat.S_ISCHR(pre.st_mode) or stat.S_ISFIFO(pre.st_mode)):
        raise FileExistsError(
            f"output path already exists and is not a regular file: {output_path}"
        )

    flags = os.O_WRONLY
    if stat.S_ISFIFO(pre.st_mode) and hasattr(os, "O_NONBLOCK"):
        # Opening a FIFO write-only can block forever when no reader is present.
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output_path, flags)

    try:
        post = os.fstat(fd)
        if (post.st_dev, post.st_ino) != (pre.st_dev, pre.st_ino) or not (
            stat.S_ISCHR(post.st_mode) or stat.S_ISFIFO(post.st_mode)
        ):
            raise _output_changed_error(output_path)
        if stat.S_ISFIFO(post.st_mode) and hasattr(os, "O_NONBLOCK"):
            # The nonblocking flag is needed only for the race-safe open. Once a
            # reader is present, restore normal FIFO semantics so a large report
            # cannot stop at pipe capacity and leave truncated JSON behind.
            current_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, current_flags & ~os.O_NONBLOCK)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _write_temp_to_created_fd(tmp_path: Path, output_path: Path, fd: int) -> None:
    created = os.fstat(fd)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            destination.write(tmp_path.read_text(encoding="utf-8"))
    except BaseException:
        try:
            current = os.lstat(output_path)
        except OSError:
            pass
        else:
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                with contextlib.suppress(OSError):
                    output_path.unlink()
        raise

    try:
        current = os.lstat(output_path)
    except OSError as exc:
        raise _output_changed_error(output_path) from exc
    if (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):
        raise _output_changed_error(output_path)


def _overwrite_existing_regular_file(
    tmp_path: Path,
    output_path: Path,
    observed: os.stat_result,
    *,
    mode: int,
) -> None:
    output_text = tmp_path.read_text(encoding="utf-8")
    flags = os.O_WRONLY
    for optional_flag in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, optional_flag, 0)

    fd = os.open(output_path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
            observed.st_dev,
            observed.st_ino,
        ):
            raise _output_changed_error(output_path)
        if opened.st_nlink != 1:
            raise _multiple_hard_links_error(output_path)

        os.fchmod(fd, mode)
        before_write = os.fstat(fd)
        if before_write.st_nlink != 1:
            raise _multiple_hard_links_error(output_path)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        destination = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with destination:
            destination.write(output_text)
    finally:
        if fd >= 0:
            os.close(fd)

    try:
        current = os.lstat(output_path)
    except OSError as exc:
        raise _output_changed_error(output_path) from exc
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (
        observed.st_dev,
        observed.st_ino,
    ):
        raise _output_changed_error(output_path)
    if current.st_nlink != 1:
        raise _multiple_hard_links_error(output_path)


def _overwrite_output_file(tmp_path: Path, output_path: Path, *, mode: int) -> None:
    for _ in range(3):
        try:
            observed = os.lstat(output_path)
        except FileNotFoundError:
            try:
                os.link(tmp_path, output_path)
                return
            except FileExistsError:
                continue
            except OSError:
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                try:
                    fd = os.open(output_path, flags, mode)
                except FileExistsError:
                    continue
                _write_temp_to_created_fd(tmp_path, output_path, fd)
                return

        if not stat.S_ISREG(observed.st_mode):
            raise FileExistsError(
                f"output path already exists and is not a regular file: {output_path}"
            )
        if observed.st_nlink != 1:
            raise _multiple_hard_links_error(output_path)
        try:
            _overwrite_existing_regular_file(
                tmp_path,
                output_path,
                observed,
                mode=mode,
            )
        except FileNotFoundError:
            continue
        return

    raise OSError(errno.EAGAIN, f"output path changed repeatedly: {output_path}")


def _install_output_file(tmp_path: Path, output_path: Path, *, overwrite: bool, mode: int) -> None:
    if overwrite:
        _overwrite_output_file(tmp_path, output_path, mode=mode)
        return

    friendly = f"output file already exists; use --overwrite to replace: {output_path}"
    try:
        os.link(tmp_path, output_path)
        return
    except FileExistsError as exc:
        raise FileExistsError(friendly) from exc
    except OSError:
        # Filesystem does not support hardlinks (e.g. exFAT, some FUSE/overlay
        # mounts). Fall back to an exclusive create that still refuses to clobber.
        pass

    try:
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise FileExistsError(friendly) from exc
    _write_temp_to_created_fd(tmp_path, output_path, fd)


def write_output_json(
    path: str | Path,
    *,
    overwrite: bool = False,
    output_mode: str = "private",
    **kwargs: Any,
) -> None:
    """Write a JSON export document to disk (or stdout when path is "-")."""
    document = build_output_document(**kwargs)
    output_text = json.dumps(document, indent=2, sort_keys=False) + "\n"

    if str(path) == "-":
        sys.stdout.write(output_text)
        return

    output_path = Path(path)

    device_fd = _existing_special_device_fd(output_path)
    if device_fd is not None:
        with os.fdopen(device_fd, "w", encoding="utf-8") as device_file:
            device_file.write(output_text)
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = _output_file_mode(output_mode)

    # Randomized temp name in the same directory prevents pre-placed-symlink
    # attacks on a predictable temp path; NamedTemporaryFile uses O_EXCL
    # and creates the file with mode 0600 on Unix.
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=output_path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(output_text)
            os.fchmod(tmp_file.fileno(), mode)
        _install_output_file(tmp_path, output_path, overwrite=overwrite, mode=mode)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    else:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
