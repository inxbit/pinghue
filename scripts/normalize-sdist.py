#!/usr/bin/env python3
"""Rewrite a built source distribution into a deterministic, safe archive."""

from __future__ import annotations

import argparse
import binascii
import contextlib
import io
import os
import stat
import struct
import tarfile
import tempfile
from pathlib import Path, PurePosixPath, PureWindowsPath

MAX_MEMBERS = 10_000
MAX_TOTAL_SIZE = 64 * 1024 * 1024
MAX_GZIP_MTIME = (1 << 32) - 1
STORED_BLOCK_SIZE = (1 << 16) - 1


class NormalizationError(ValueError):
    """Raised when an archive cannot be normalized safely."""


def _source_date_epoch() -> int:
    raw_value = os.environ.get("SOURCE_DATE_EPOCH", "0")
    try:
        epoch = int(raw_value, 10)
    except ValueError as exc:
        raise NormalizationError("SOURCE_DATE_EPOCH must be an integer") from exc
    if not 0 <= epoch <= MAX_GZIP_MTIME:
        raise NormalizationError(
            f"SOURCE_DATE_EPOCH must be between 0 and {MAX_GZIP_MTIME}"
        )
    return epoch


def _canonical_member_name(member: tarfile.TarInfo) -> tuple[str, tuple[str, ...]]:
    name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
    ):
        raise NormalizationError(f"unsafe archive member path: {member.name!r}")

    raw_parts = tuple(name.split("/"))
    if any(part in {"", ".", ".."} for part in raw_parts):
        raise NormalizationError(f"unsafe archive member path: {member.name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or PureWindowsPath(name).drive
        or tuple(path.parts) != raw_parts
    ):
        raise NormalizationError(f"unsafe archive member path: {member.name!r}")
    return name, raw_parts


def _normalized_members(
    archive_path: Path,
    *,
    epoch: int,
) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    normalized: list[tuple[tarfile.TarInfo, bytes | None]] = []
    names: set[str] = set()
    directory_by_name: dict[str, bool] = {}
    roots: set[str] = set()
    root_directories: set[str] = set()
    total_size = 0

    with tarfile.open(archive_path, mode="r:gz") as source:
        member_count = 0
        for member_count, member in enumerate(source, start=1):
            if member_count > MAX_MEMBERS:
                raise NormalizationError(
                    f"source distribution has more than {MAX_MEMBERS} members"
                )
            name, parts = _canonical_member_name(member)
            if name in names:
                raise NormalizationError(f"duplicate archive member: {name}")
            names.add(name)
            directory_by_name[name] = member.isdir()
            roots.add(parts[0])

            payload: bytes | None
            if member.isdir():
                payload = None
                if len(parts) == 1:
                    root_directories.add(parts[0])
            elif member.isfile():
                if member.size < 0:
                    raise NormalizationError(f"negative archive member size: {name}")
                total_size += member.size
                if total_size > MAX_TOTAL_SIZE:
                    raise NormalizationError(
                        "source distribution expands beyond the 64 MiB safety limit"
                    )
                extracted = source.extractfile(member)
                if extracted is None:
                    raise NormalizationError(f"cannot read archive member: {name}")
                with extracted:
                    payload = extracted.read()
                if len(payload) != member.size:
                    raise NormalizationError(f"truncated archive member: {name}")
            else:
                raise NormalizationError(
                    f"unsupported archive member type for {name!r}"
                )

            output = tarfile.TarInfo(name)
            output.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
            output.size = 0 if payload is None else len(payload)
            output.mode = (
                0o755
                if member.isdir() or bool(member.mode & 0o111)
                else 0o644
            )
            output.uid = 0
            output.gid = 0
            output.uname = "root"
            output.gname = "root"
            output.mtime = epoch
            output.pax_headers = {}
            normalized.append((output, payload))

        if member_count == 0:
            raise NormalizationError("source distribution is empty")

    for name in sorted(directory_by_name):
        name_parts = name.split("/")
        for depth in range(1, len(name_parts)):
            parent = "/".join(name_parts[:depth])
            if directory_by_name.get(parent) is False:
                raise NormalizationError(
                    f"archive member has non-directory parent {parent!r}: {name!r}"
                )

    if len(roots) != 1:
        raise NormalizationError(
            "source distribution must contain exactly one top-level directory"
        )
    root = next(iter(roots))
    if root_directories != {root}:
        raise NormalizationError(
            "source distribution must contain its top-level directory entry"
        )
    return sorted(normalized, key=lambda item: item[0].name)


def _write_canonical_tar(
    path: Path,
    members: list[tuple[tarfile.TarInfo, bytes | None]],
) -> None:
    with tarfile.open(path, mode="w", format=tarfile.PAX_FORMAT) as destination:
        for member, payload in members:
            if payload is None:
                destination.addfile(member)
                continue
            destination.addfile(member, io.BytesIO(payload))


def _write_stored_gzip(source_path: Path, destination_path: Path, *, epoch: int) -> None:
    """Write a deterministic gzip stream using uncompressed DEFLATE blocks."""
    crc = 0
    total_size = 0
    header = b"\x1f\x8b\x08\x00" + struct.pack("<I", epoch) + b"\x00\xff"

    with source_path.open("rb") as source, destination_path.open("wb") as destination:
        destination.write(header)
        chunk = source.read(STORED_BLOCK_SIZE)
        if not chunk:
            destination.write(b"\x01\x00\x00\xff\xff")
        while chunk:
            next_chunk = source.read(STORED_BLOCK_SIZE)
            final = not next_chunk
            length = len(chunk)
            destination.write(b"\x01" if final else b"\x00")
            destination.write(struct.pack("<HH", length, length ^ 0xFFFF))
            destination.write(chunk)
            crc = binascii.crc32(chunk, crc)
            total_size = (total_size + length) & 0xFFFFFFFF
            chunk = next_chunk
        destination.write(struct.pack("<II", crc & 0xFFFFFFFF, total_size))
        destination.flush()
        os.fsync(destination.fileno())


def normalize_sdist(archive_path: Path, *, epoch: int) -> None:
    if not archive_path.name.endswith(".tar.gz"):
        raise NormalizationError("source distribution path must end in .tar.gz")
    observed = archive_path.lstat()
    if not stat.S_ISREG(observed.st_mode):
        raise NormalizationError("source distribution must be a regular file")

    members = _normalized_members(archive_path, epoch=epoch)
    temporary_paths: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(
            dir=archive_path.parent,
            prefix=f".{archive_path.name}.",
            suffix=".tar.tmp",
            delete=False,
        ) as tar_file:
            tar_path = Path(tar_file.name)
        temporary_paths.append(tar_path)
        _write_canonical_tar(tar_path, members)

        with tempfile.NamedTemporaryFile(
            dir=archive_path.parent,
            prefix=f".{archive_path.name}.",
            suffix=".gz.tmp",
            delete=False,
        ) as gzip_file:
            gzip_path = Path(gzip_file.name)
        temporary_paths.append(gzip_path)
        _write_stored_gzip(tar_path, gzip_path, epoch=epoch)
        os.chmod(gzip_path, stat.S_IMODE(observed.st_mode))

        current = archive_path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != (observed.st_dev, observed.st_ino)
        ):
            raise NormalizationError(
                "source distribution path changed during normalization"
            )
        os.replace(gzip_path, archive_path)
        temporary_paths.remove(gzip_path)

        with contextlib.suppress(OSError):
            directory_fd = os.open(archive_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize one .tar.gz source distribution in place."
    )
    parser.add_argument("archive", type=Path)
    arguments = parser.parse_args()
    try:
        normalize_sdist(arguments.archive, epoch=_source_date_epoch())
    except (NormalizationError, OSError, tarfile.TarError) as exc:
        parser.exit(1, f"normalize-sdist: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
