import errno
import fcntl
import json
import os
import socket
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest

import pinghue.export as export_module
from pinghue.export import build_output_document, write_output_json
from pinghue.models import (
    MAX_TARGET_SAMPLES,
    AddressFamily,
    ProbeConfig,
    ProbeMode,
    ProbeSample,
    SampleStatus,
    SampleWindow,
    TargetRun,
    TargetStatus,
)


def build_target() -> TargetRun:
    return TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
        status=TargetStatus.HEALTHY,
        error=None,
        samples=[
            ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
                latency_ms=9.2,
                status=SampleStatus.OK,
                error=None,
            )
        ],
    )


def _write(output_path: Path, **overrides: Any) -> None:
    kwargs: dict[str, Any] = dict(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[build_target()],
        include_samples=False,
    )
    kwargs.update(overrides)
    write_output_json(output_path, **kwargs)


def test_write_output_json_does_not_follow_symlink_to_device(tmp_path: Path) -> None:
    # L11: a symlink at the output path must not be followed to a device; with no
    # --overwrite the run refuses rather than writing through the link.
    link = tmp_path / "out.json"
    link.symlink_to("/dev/null")

    with pytest.raises(FileExistsError, match="already exists"):
        _write(link)

    assert link.is_symlink()


def test_write_output_json_refuses_fifo_without_hanging(tmp_path: Path) -> None:
    output_path = tmp_path / "out.fifo"
    os.mkfifo(output_path)

    with pytest.raises(OSError):
        _write(output_path)

    assert stat.S_ISFIFO(output_path.stat().st_mode)


def test_write_output_json_does_not_replace_fifo_when_overwrite_is_enabled(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "out.fifo"
    os.mkfifo(output_path)

    with pytest.raises(OSError):
        _write(output_path, overwrite=True)

    assert stat.S_ISFIFO(output_path.lstat().st_mode)


def test_existing_fifo_fd_is_blocking_after_safe_nonblocking_open(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "out.fifo"
    os.mkfifo(output_path)
    reader_fd = os.open(output_path, os.O_RDONLY | os.O_NONBLOCK)
    writer_fd = -1
    try:
        writer_fd = export_module._existing_special_device_fd(output_path) or -1

        assert writer_fd >= 0
        assert fcntl.fcntl(writer_fd, fcntl.F_GETFL) & os.O_NONBLOCK == 0
    finally:
        if writer_fd >= 0:
            os.close(writer_fd)
        os.close(reader_fd)


def test_write_output_json_does_not_replace_unix_socket() -> None:
    # Darwin limits AF_UNIX paths to 104 bytes, while pytest's tmp paths can be
    # substantially longer. Keep the real socket test under the short /tmp alias.
    with tempfile.TemporaryDirectory(prefix="pinghue-", dir="/tmp") as directory:
        output_path = Path(directory) / "out.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(str(output_path))
            except PermissionError:
                pytest.skip("sandbox does not permit creating AF_UNIX sockets")

            with pytest.raises(OSError):
                _write(output_path, overwrite=True)

            assert stat.S_ISSOCK(output_path.lstat().st_mode)


def test_write_output_json_does_not_replace_symlink_with_overwrite(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "destination.json"
    destination.write_text("keep me\n", encoding="utf-8")
    output_path = tmp_path / "out.json"
    output_path.symlink_to(destination)

    with pytest.raises(FileExistsError, match="already exists"):
        _write(output_path, overwrite=True)

    assert output_path.is_symlink()
    assert destination.read_text(encoding="utf-8") == "keep me\n"


def test_write_output_json_refuses_to_overwrite_multiply_linked_file(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "out.json"
    alias_path = tmp_path / "alias.json"
    output_path.write_text("previous\n", encoding="utf-8")
    os.link(output_path, alias_path)

    with pytest.raises(FileExistsError, match="multiple hard links"):
        _write(output_path, overwrite=True)

    assert output_path.read_text(encoding="utf-8") == "previous\n"
    assert alias_path.read_text(encoding="utf-8") == "previous\n"


def test_overwrite_does_not_replace_node_swapped_to_fifo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "out.json"
    output_path.write_text("previous\n", encoding="utf-8")
    real_open = os.open
    real_replace = Path.replace
    swapped = False

    def swap_destination() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped = True
        output_path.unlink()
        os.mkfifo(output_path)

    def racing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if Path(path) == output_path and flags & os.O_WRONLY and not flags & os.O_CREAT:
            swap_destination()
        return real_open(path, flags, *args, **kwargs)  # type: ignore[arg-type]

    def racing_replace(source: Path, target: Path) -> Path:
        if Path(target) == output_path:
            swap_destination()
        return real_replace(source, target)

    monkeypatch.setattr(os, "open", racing_open)
    monkeypatch.setattr(Path, "replace", racing_replace)

    with pytest.raises(OSError):
        _write(output_path, overwrite=True)

    assert swapped is True
    assert stat.S_ISFIFO(output_path.lstat().st_mode)


def test_existing_special_device_rejects_identity_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = SimpleNamespace(st_mode=stat.S_IFIFO, st_dev=1, st_ino=10)
    after = SimpleNamespace(st_mode=stat.S_IFIFO, st_dev=1, st_ino=11)
    closed: list[int] = []

    monkeypatch.setattr(os, "lstat", lambda _path: before)
    monkeypatch.setattr(os, "open", lambda _path, _flags: 99)
    monkeypatch.setattr(os, "fstat", lambda _fd: after)
    monkeypatch.setattr(os, "close", closed.append)

    with pytest.raises(OSError, match="changed while opening"):
        export_module._existing_special_device_fd(Path("out.fifo"))

    assert closed == [99]


def test_existing_output_rejects_unsupported_socket_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_node = SimpleNamespace(st_mode=stat.S_IFSOCK, st_dev=1, st_ino=10)
    monkeypatch.setattr(os, "lstat", lambda _path: socket_node)

    with pytest.raises(FileExistsError, match="not a regular file"):
        export_module._existing_special_device_fd(Path("out.sock"))


def test_write_output_json_falls_back_when_hardlinks_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # L12: filesystems without hardlink support still get the report written.
    output_path = tmp_path / "out.json"

    def no_hardlinks(_src: object, _dst: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlinks not supported")

    monkeypatch.setattr(os, "link", no_hardlinks)

    _write(output_path)

    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert document["pinghue_version"]


def test_write_output_json_refuses_existing_file_without_hardlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "out.json"
    output_path.write_text("previous\n", encoding="utf-8")

    def no_hardlinks(_src: object, _dst: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlinks not supported")

    monkeypatch.setattr(os, "link", no_hardlinks)

    with pytest.raises(FileExistsError, match="already exists"):
        _write(output_path)

    assert output_path.read_text(encoding="utf-8") == "previous\n"


def test_write_output_json_removes_exclusively_created_fallback_after_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "out.json"

    def no_hardlinks(_src: object, _dst: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlinks not supported")

    def copy_fails(_path: Path, *_args: object, **_kwargs: object) -> str:
        raise OSError(errno.EIO, "copy failed")

    monkeypatch.setattr(os, "link", no_hardlinks)
    monkeypatch.setattr(Path, "read_text", copy_fails)

    with pytest.raises(OSError, match="copy failed"):
        _write(output_path)

    assert not output_path.exists()


def test_write_output_json_cleans_fallback_after_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "out.json"

    def no_hardlinks(_src: object, _dst: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlinks not supported")

    def copy_is_interrupted(_path: Path, *_args: object, **_kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "link", no_hardlinks)
    monkeypatch.setattr(Path, "read_text", copy_is_interrupted)

    with pytest.raises(KeyboardInterrupt):
        _write(output_path)

    assert not output_path.exists()
    assert list(tmp_path.glob(f"{output_path.name}.*.tmp")) == []


def test_write_output_json_preserves_replacement_inode_after_fallback_copy_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "out.json"
    original_read_text = Path.read_text

    def no_hardlinks(_src: object, _dst: object) -> None:
        raise OSError(errno.EOPNOTSUPP, "hardlinks not supported")

    def replace_then_fail(_path: Path, *_args: object, **_kwargs: object) -> str:
        output_path.unlink()
        output_path.write_text("replacement\n", encoding="utf-8")
        raise OSError(errno.EIO, "copy failed after replacement")

    monkeypatch.setattr(os, "link", no_hardlinks)
    monkeypatch.setattr(Path, "read_text", replace_then_fail)

    with pytest.raises(OSError, match="copy failed after replacement"):
        _write(output_path)

    assert original_read_text(output_path, encoding="utf-8") == "replacement\n"


def test_write_output_json_private_mode_is_owner_only(tmp_path: Path) -> None:
    # Default --output-mode private is 0600 regardless of a permissive umask.
    output_path = tmp_path / "out.json"
    previous_umask = os.umask(0o000)
    try:
        _write(output_path)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


def test_write_output_json_umask_mode_honors_umask(tmp_path: Path) -> None:
    # --output-mode umask follows the process umask (I2).
    output_path = tmp_path / "out.json"
    previous_umask = os.umask(0o022)
    try:
        _write(output_path, output_mode="umask")
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(output_path.stat().st_mode) == 0o644


def test_build_output_document_matches_schema() -> None:
    document = build_output_document(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[build_target()],
    )
    schema = json.loads(Path("schemas/output-v1.schema.json").read_text(encoding="utf-8"))

    jsonschema.validate(document, schema)
    assert document["schema_version"] == 1
    assert document["targets"][0]["samples"][0]["latency_ms"] == 9.2
    assert document["run"]["samples_window"] == MAX_TARGET_SAMPLES


def test_build_output_document_reports_samples_window_for_windowed_stats() -> None:
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
        status=TargetStatus.HEALTHY,
    )
    for index in range(MAX_TARGET_SAMPLES + 25):
        target.apply_sample(
            ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, index % 60, tzinfo=timezone.utc),
                latency_ms=float(index),
                status=SampleStatus.OK,
            ),
            fail_threshold=3,
            jitter_threshold_ms=10_000.0,
        )

    document = build_output_document(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="completed",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[target],
    )
    exported = document["targets"][0]

    # The window caps emitted samples while stats stay cumulative: a consumer
    # detects truncation via stats.sent > len(samples), bounded by samples_window.
    assert document["run"]["samples_window"] == MAX_TARGET_SAMPLES
    assert exported["stats"]["sent"] == MAX_TARGET_SAMPLES + 25
    assert len(exported["samples"]) == MAX_TARGET_SAMPLES
    assert exported["stats"]["sent"] > len(exported["samples"])


def test_build_output_document_reports_effective_per_target_sample_window() -> None:
    effective_window = 400
    target = TargetRun(
        target="1.1.1.1",
        resolved_address="1.1.1.1",
        resolved_family=AddressFamily.IPV4,
        status=TargetStatus.HEALTHY,
        samples=SampleWindow(maxlen=effective_window),
    )

    document = build_output_document(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="completed",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[target],
    )

    assert document["run"]["samples_window"] == effective_window


def test_write_output_json_can_omit_samples(tmp_path: Path) -> None:
    output_path = tmp_path / "out.json"

    write_output_json(
        output_path,
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[build_target()],
        include_samples=False,
    )

    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert document["targets"][0]["samples"] == []


def test_write_output_json_refuses_to_replace_existing_file_by_default(tmp_path: Path) -> None:
    output_path = tmp_path / "out.json"
    output_path.write_text("previous\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        write_output_json(
            output_path,
            started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
            host="ops-laptop-04",
            exit_reason="user_quit",
            probe=ProbeConfig(
                mode=ProbeMode.ICMP,
                port=None,
                interval_s=1.0,
                timeout_s=1.0,
                address_family=AddressFamily.AUTO,
            ),
            targets=[build_target()],
            include_samples=False,
        )

    assert output_path.read_text(encoding="utf-8") == "previous\n"


def test_write_output_json_allows_explicit_overwrite(tmp_path: Path) -> None:
    output_path = tmp_path / "out.json"
    output_path.write_text("previous\n", encoding="utf-8")

    write_output_json(
        output_path,
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[build_target()],
        include_samples=False,
        overwrite=True,
    )

    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert document["pinghue_version"]


def test_write_output_json_writes_special_device_directly() -> None:
    write_output_json(
        Path("/dev/null"),
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[build_target()],
        include_samples=False,
    )


def test_build_output_document_escapes_control_characters() -> None:
    target = TargetRun(
        target="host\x1b[31m",
        resolved_address=None,
        resolved_family=None,
        status=TargetStatus.ERROR,
        error="target\x1b[2Jerror",
        samples=[
            ProbeSample(
                timestamp=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
                latency_ms=None,
                status=SampleStatus.ERROR,
                error="sample\x1b[2Jerror",
            )
        ],
    )

    document = build_output_document(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops-laptop-04",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[target],
    )

    serialized = json.dumps(document)
    exported_target = document["targets"][0]
    assert "\x1b" not in serialized
    assert exported_target["target"] == "host\\x1b[31m"
    assert exported_target["error"] == "target\\x1b[2Jerror"
    assert exported_target["samples"][0]["error"] == "sample\\x1b[2Jerror"


def test_build_output_document_escapes_non_ascii_confusables() -> None:
    target = TargetRun(
        target="g\u03bf\u03bfgle.example",
        resolved_address="fe80::1%\u0435th0",
        resolved_family=AddressFamily.IPV6,
        status=TargetStatus.ERROR,
        error="looks\u0430like",
    )

    document = build_output_document(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
        host="ops\u2011host",
        exit_reason="user_quit",
        probe=ProbeConfig(
            mode=ProbeMode.ICMP,
            port=None,
            interval_s=1.0,
            timeout_s=1.0,
            address_family=AddressFamily.AUTO,
        ),
        targets=[target],
    )

    assert document["run"]["host"] == "ops\\u2011host"
    assert document["targets"][0]["target"] == "g\\u03bf\\u03bfgle.example"
    assert document["targets"][0]["resolved_address"] == "fe80::1%\\u0435th0"
    assert document["targets"][0]["error"] == "looks\\u0430like"


def test_write_output_json_preserves_existing_file_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "out.json"
    output_path.write_text("previous\n", encoding="utf-8")
    original_named_temp_file = tempfile.NamedTemporaryFile

    def failing_named_temp_file(*args: Any, **kwargs: Any) -> Any:
        handle = original_named_temp_file(*args, **kwargs)
        original_write = handle.write

        def failing_write(_data: str) -> int:
            original_write("{")
            raise OSError("disk full")

        handle.write = failing_write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(
        "pinghue.export.tempfile.NamedTemporaryFile",
        failing_named_temp_file,
    )

    with pytest.raises(OSError, match="disk full"):
        write_output_json(
            output_path,
            started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=timezone.utc),
            ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=timezone.utc),
            host="ops-laptop-04",
            exit_reason="user_quit",
            probe=ProbeConfig(
                mode=ProbeMode.ICMP,
                port=None,
                interval_s=1.0,
                timeout_s=1.0,
                address_family=AddressFamily.AUTO,
            ),
            targets=[build_target()],
            include_samples=False,
            overwrite=True,
        )

    assert output_path.read_text(encoding="utf-8") == "previous\n"
    # Randomized temp names match `out.json.<random>.tmp`; no leftover.
    assert list(tmp_path.glob(f"{output_path.name}.*.tmp")) == []


def test_write_output_json_dash_writes_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    _write(Path("-"))

    document = json.loads(capsys.readouterr().out)
    assert document["schema_version"] == 1
    # "-" means stdout, never a literal file named "-".
    assert not (tmp_path / "-").exists()
    assert list(tmp_path.iterdir()) == []
