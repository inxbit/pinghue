"""JSON export support."""

from __future__ import annotations

import json
import os
import stat
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
        "resolved_address": target.resolved_address,
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
    return {
        "schema_version": SCHEMA_VERSION,
        "pinghue_version": __version__,
        "run": {
            "started_at": format_timestamp(started_at),
            "ended_at": format_timestamp(ended_at),
            "host": sanitize_display(host),
            "exit_reason": exit_reason,
            "samples_window": MAX_TARGET_SAMPLES,
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


def _existing_special_device_fd(output_path: Path) -> int | None:
    """Return a write fd if output_path is an existing char device or FIFO.

    A symlink at the path is never followed (falls through to the atomic
    regular-file path), and the opened fd is re-checked with fstat to close the
    TOCTOU window between the lstat and the open.
    """
    try:
        pre = os.lstat(output_path)
    except OSError:
        return None
    if stat.S_ISLNK(pre.st_mode) or not (
        stat.S_ISCHR(pre.st_mode) or stat.S_ISFIFO(pre.st_mode)
    ):
        return None

    flags = os.O_WRONLY
    if stat.S_ISFIFO(pre.st_mode) and hasattr(os, "O_NONBLOCK"):
        # Opening a FIFO write-only can block forever when no reader is present.
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output_path, flags)
    except OSError:
        return None

    try:
        post = os.fstat(fd)
    except OSError:
        os.close(fd)
        return None
    if stat.S_ISCHR(post.st_mode) or stat.S_ISFIFO(post.st_mode):
        return fd
    os.close(fd)
    return None


def _install_output_file(
    tmp_path: Path, output_path: Path, *, overwrite: bool, mode: int
) -> None:
    if overwrite:
        tmp_path.replace(output_path)
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
    with os.fdopen(fd, "w", encoding="utf-8") as destination:
        destination.write(tmp_path.read_text(encoding="utf-8"))


def write_output_json(
    path: str | Path,
    *,
    overwrite: bool = False,
    output_mode: str = "private",
    **kwargs: Any,
) -> None:
    """Write a JSON export document to disk."""
    output_path = Path(path)
    document = build_output_document(**kwargs)
    output_text = json.dumps(document, indent=2, sort_keys=False) + "\n"

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
        os.chmod(tmp_path, mode)
        _install_output_file(tmp_path, output_path, overwrite=overwrite, mode=mode)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise
    else:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
