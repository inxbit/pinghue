import errno
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from pinghue.export import build_output_document, write_output_json
from pinghue.models import (
    MAX_TARGET_SAMPLES,
    AddressFamily,
    ProbeConfig,
    ProbeMode,
    ProbeSample,
    SampleStatus,
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

    with pytest.raises(FileExistsError, match="already exists"):
        _write(output_path)

    assert stat.S_ISFIFO(output_path.stat().st_mode)


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
        resolved_address=None,
        resolved_family=None,
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
