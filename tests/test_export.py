import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from pinghue.export import build_output_document, write_output_json
from pinghue.models import (
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
        )

    assert output_path.read_text(encoding="utf-8") == "previous\n"
    # Randomized temp names match `out.json.<random>.tmp`; no leftover.
    assert list(tmp_path.glob(f"{output_path.name}.*.tmp")) == []
