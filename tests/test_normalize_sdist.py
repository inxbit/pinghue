import gzip
import io
import os
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path("scripts/normalize-sdist.py")


def test_normalizer_enforces_member_limit_while_iterating() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert ".getmembers()" not in source
    assert "for member_count, member in enumerate(source, start=1):" in source


def _write_archive(
    path: Path,
    members: list[tuple[tarfile.TarInfo, bytes | None]],
    *,
    gzip_mtime: int = 1,
) -> None:
    with (
        path.open("wb") as raw,
        gzip.GzipFile(
            fileobj=raw,
            mode="wb",
            filename=path.name,
            mtime=gzip_mtime,
        ) as zipped,
        tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for member, payload in members:
            archive.addfile(member, io.BytesIO(payload) if payload is not None else None)


def _member(
    name: str,
    *,
    payload: bytes | None = None,
    mode: int = 0o644,
    mtime: int = 1,
    type_: bytes | None = None,
) -> tuple[tarfile.TarInfo, bytes | None]:
    info = tarfile.TarInfo(name)
    info.type = type_ or (tarfile.DIRTYPE if payload is None else tarfile.REGTYPE)
    info.size = len(payload) if payload is not None else 0
    info.mode = mode
    info.mtime = mtime
    info.uid = mtime
    info.gid = mtime + 1
    info.uname = f"user-{mtime}"
    info.gname = f"group-{mtime}"
    info.pax_headers = {"comment": f"metadata-{mtime}"}
    return info, payload


def _run(path: Path, *, epoch: str = "123") -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = epoch
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def test_normalize_sdist_produces_identical_safe_archives(tmp_path: Path) -> None:
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    first_members = [
        _member("pinghue-1.2.3", mode=0o700, mtime=11),
        _member("pinghue-1.2.3/README.md", payload=b"hello\n", mode=0o600, mtime=12),
        _member("pinghue-1.2.3/bin/run", payload=b"#!/bin/sh\n", mode=0o700, mtime=13),
    ]
    second_members = [
        _member("pinghue-1.2.3/bin/run", payload=b"#!/bin/sh\n", mode=0o777, mtime=93),
        _member("pinghue-1.2.3/README.md", payload=b"hello\n", mode=0o666, mtime=92),
        _member("pinghue-1.2.3", mode=0o755, mtime=91),
    ]
    _write_archive(first, first_members, gzip_mtime=10)
    _write_archive(second, second_members, gzip_mtime=90)

    first_result = _run(first)
    second_result = _run(second)

    assert first_result.returncode == 0, first_result.stderr
    assert second_result.returncode == 0, second_result.stderr
    assert first.read_bytes() == second.read_bytes()
    assert int.from_bytes(first.read_bytes()[4:8], "little") == 123

    with tarfile.open(first, mode="r:gz") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == [
        "pinghue-1.2.3",
        "pinghue-1.2.3/README.md",
        "pinghue-1.2.3/bin/run",
    ]
    assert [member.mode for member in members] == [0o755, 0o644, 0o755]
    assert all(member.mtime == 123 for member in members)
    assert all(member.uid == 0 and member.gid == 0 for member in members)
    assert all(member.uname == "root" and member.gname == "root" for member in members)
    assert all(not member.pax_headers for member in members)


@pytest.mark.parametrize(
    "members",
    [
        [_member("/absolute/file", payload=b"x")],
        [
            _member("C:"),
            _member("C:/file", payload=b"x"),
        ],
        [
            _member("root"),
            _member("root/../escape", payload=b"x"),
        ],
        [
            _member("root"),
            _member("other/file", payload=b"x"),
        ],
        [
            _member("root"),
            _member("root/link", type_=tarfile.SYMTYPE),
        ],
        [
            _member("root"),
            _member("root/file", payload=b"one"),
            _member("root/file", payload=b"two"),
        ],
        [
            _member("root"),
            _member("root/device", type_=tarfile.CHRTYPE),
        ],
        [
            _member("root"),
            _member("root/node", payload=b"file"),
            _member("root/node/child", payload=b"nested"),
        ],
    ],
    ids=(
        "absolute",
        "windows-drive",
        "traversal",
        "multiple-roots",
        "link",
        "duplicate",
        "device",
        "non-directory-parent",
    ),
)
def test_normalize_sdist_rejects_unsafe_members_without_replacing_input(
    tmp_path: Path,
    members: list[tuple[tarfile.TarInfo, bytes | None]],
) -> None:
    archive = tmp_path / "unsafe.tar.gz"
    _write_archive(archive, members)
    original = archive.read_bytes()

    result = _run(archive)

    assert result.returncode != 0
    assert archive.read_bytes() == original
    assert list(tmp_path.glob(f".{archive.name}.*.tmp")) == []


def test_normalize_sdist_rejects_invalid_epoch_without_replacing_input(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive.tar.gz"
    _write_archive(
        archive,
        [
            _member("root"),
            _member("root/file", payload=b"payload"),
        ],
    )
    original = archive.read_bytes()

    result = _run(archive, epoch="-1")

    assert result.returncode != 0
    assert "SOURCE_DATE_EPOCH" in result.stderr
    assert archive.read_bytes() == original


def test_normalize_sdist_preserves_archive_file_mode(tmp_path: Path) -> None:
    archive = tmp_path / "archive.tar.gz"
    _write_archive(
        archive,
        [
            _member("root"),
            _member("root/file", payload=b"payload"),
        ],
    )
    archive.chmod(0o640)

    result = _run(archive)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(archive.stat().st_mode) == 0o640
