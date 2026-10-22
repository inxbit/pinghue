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

_LINUX = sys.platform.startswith("linux")

# NamedTemporaryFile names the staged report <prefix><8 random characters>.tmp.
_TEMP_NAME_EXTRA_BYTES = 8 + len(".tmp")
_DEFAULT_NAME_MAX = 255

# Read or write access for anyone but the owner: who else can open a FIFO.
_SHARED_ACCESS_BITS = stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH

# The directory whose entries name the process's own open descriptors.
_DESCRIPTOR_DIRECTORY = "/dev/fd"


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


def _not_regular_file_error(output_path: Path) -> FileExistsError:
    return FileExistsError(f"output path already exists and is not a regular file: {output_path}")


def _existing_output_error(output_path: Path) -> FileExistsError:
    return FileExistsError(f"output file already exists; use --overwrite to replace: {output_path}")


def _require_fifo_owner(output_path: Path, observed: os.stat_result) -> None:
    # A FIFO pre-created by another local user (for example in a shared sticky
    # directory such as /tmp) would hand the report to that user's reader.
    # Character devices stay allowed whoever owns them: the only one an
    # unprivileged user can create is the inert whiteout device refused below.
    if stat.S_ISFIFO(observed.st_mode) and observed.st_uid != os.geteuid():
        raise PermissionError(f"refusing to write to a FIFO owned by another user: {output_path}")


def _is_descriptor_path(output_path: Path) -> bool:
    # On macOS /dev/fd/N is not a symlink: it stats as the run's own open
    # descriptor N, with mode bits that describe the descriptor rather than a
    # node (a pipe reports 0660), and no other user can open that descriptor
    # through the path. Process substitution hands over a pipe this way. On
    # Linux the entries are symlinks, which are refused before this is asked.
    try:
        return os.path.samestat(os.stat(output_path.parent), os.stat(_DESCRIPTOR_DIRECTORY))
    except OSError:
        return False


def _require_private_fifo(output_path: Path, observed: os.stat_result, output_mode: str) -> None:
    # In private mode only the owner may read the report. A FIFO's own
    # permission bits decide who else can open it (mkfifo under the usual
    # umask 022 makes it 0644, readable by every local user), so a FIFO that
    # grants group or other users read or write access is refused instead of
    # being handed the report. --output-mode umask leaves that to the operator.
    if output_mode != "private" or not stat.S_ISFIFO(observed.st_mode):
        return
    permissions = stat.S_IMODE(observed.st_mode)
    if permissions & _SHARED_ACCESS_BITS and not _is_descriptor_path(output_path):
        raise PermissionError(
            f"refusing to write to a FIFO that other users can open (mode {permissions:04o}); "
            f"chmod 600 it or use --output-mode umask: {output_path}"
        )


def _refuse_whiteout_device(output_path: Path, observed: os.stat_result) -> None:
    # Since Linux 5.8 any user may create the 0:0 "whiteout" character device
    # that overlay filesystems use. It has no driver, so a report written to it
    # is lost. On macOS 0:0 is the console device, so the check is Linux-only.
    if _LINUX and stat.S_ISCHR(observed.st_mode) and observed.st_rdev == 0:
        raise FileExistsError(
            f"output path is a whiteout character device (0, 0) with no driver: {output_path}"
        )


def _require_regular_file_owner(output_path: Path, observed: os.stat_result) -> None:
    # --overwrite rewrites the inode in place, so the file's owner could read
    # the report regardless of the mode PingHue applies.
    if observed.st_uid != os.geteuid():
        raise PermissionError(f"refusing to overwrite a file owned by another user: {output_path}")


def _existing_special_device_fd(output_path: Path, *, output_mode: str = "private") -> int | None:
    """Return a write fd if output_path is an existing char device or FIFO.

    Symlinks, unsupported special nodes, whiteout devices, FIFOs owned by
    another user, and in private mode FIFOs that other users can open are
    rejected. An opened character device or FIFO must match the identity
    observed by lstat, closing the lstat/open replacement race.
    """
    try:
        pre = os.lstat(output_path)
    except FileNotFoundError:
        return None
    if stat.S_ISREG(pre.st_mode):
        return None
    if stat.S_ISLNK(pre.st_mode) or not (stat.S_ISCHR(pre.st_mode) or stat.S_ISFIFO(pre.st_mode)):
        raise _not_regular_file_error(output_path)
    _require_fifo_owner(output_path, pre)
    _require_private_fifo(output_path, pre, output_mode)
    _refuse_whiteout_device(output_path, pre)

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
        _require_fifo_owner(output_path, post)
        _require_private_fifo(output_path, post, output_mode)
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
        _require_regular_file_owner(output_path, opened)

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
            raise _not_regular_file_error(output_path)
        if observed.st_nlink != 1:
            raise _multiple_hard_links_error(output_path)
        _require_regular_file_owner(output_path, observed)
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

    try:
        os.link(tmp_path, output_path)
        return
    except FileExistsError as exc:
        raise _existing_output_error(output_path) from exc
    except OSError:
        # Filesystem does not support hardlinks (e.g. exFAT, some FUSE/overlay
        # mounts). Fall back to an exclusive create that still refuses to clobber.
        pass

    try:
        fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError as exc:
        raise _existing_output_error(output_path) from exc
    _write_temp_to_created_fd(tmp_path, output_path, fd)


def _require_writable_directory(output_path: Path) -> None:
    # The report is staged in a temporary file beside output_path and missing
    # parents are created at write time, so check the nearest existing one.
    # lexists stops at a dangling symlink, which mkdir cannot create through.
    for directory in (output_path.parent, *output_path.parent.parents):
        if not os.path.lexists(directory):
            continue
        if not os.path.isdir(directory):
            raise NotADirectoryError(f"output directory is not a directory: {directory}")
        if not os.access(directory, os.W_OK | os.X_OK):
            raise PermissionError(f"output directory is not writable: {directory}")
        return


def _require_writable_node(output_path: Path) -> None:
    if not os.access(output_path, os.W_OK):
        raise PermissionError(f"output path is not writable: {output_path}")


def _temp_prefix(output_path: Path) -> str:
    # Keep the staged name within the directory's file-name limit even when the
    # report name itself is close to it; the report keeps its full name.
    try:
        name_max = os.pathconf(output_path.parent, "PC_NAME_MAX")
    except (OSError, ValueError):
        name_max = _DEFAULT_NAME_MAX
    if name_max <= 0:
        name_max = _DEFAULT_NAME_MAX
    prefix = output_path.name + "."
    while len(prefix) > 1 and len(os.fsencode(prefix)) + _TEMP_NAME_EXTRA_BYTES > name_max:
        prefix = prefix[:-2] + "."
    return prefix


def check_output_path(
    path: str | Path, *, overwrite: bool = False, output_mode: str = "private"
) -> None:
    """Raise OSError early when write_output_json would refuse path.

    This lets a run fail before probing instead of losing its report at exit.
    It is advisory: write_output_json repeats every check against the opened
    node when it writes, so a path changed during the run is still refused.
    """
    if str(path) == "-":
        return

    output_path = Path(path)
    try:
        observed = os.lstat(output_path)
    except FileNotFoundError:
        _require_writable_directory(output_path)
        return

    if stat.S_ISCHR(observed.st_mode) or stat.S_ISFIFO(observed.st_mode):
        _require_fifo_owner(output_path, observed)
        _require_private_fifo(output_path, observed, output_mode)
        _refuse_whiteout_device(output_path, observed)
        _require_writable_node(output_path)
        return
    if not stat.S_ISREG(observed.st_mode):
        raise _not_regular_file_error(output_path)
    if not overwrite:
        raise _existing_output_error(output_path)
    if observed.st_nlink != 1:
        raise _multiple_hard_links_error(output_path)
    _require_regular_file_owner(output_path, observed)
    _require_writable_node(output_path)
    _require_writable_directory(output_path)


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

    device_fd = _existing_special_device_fd(output_path, output_mode=output_mode)
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
            prefix=_temp_prefix(output_path),
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
