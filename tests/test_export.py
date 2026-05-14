import json
from datetime import UTC, datetime
from pathlib import Path

import jsonschema

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
                timestamp=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=UTC),
                latency_ms=9.2,
                status=SampleStatus.OK,
                error=None,
            )
        ],
    )


def test_build_output_document_matches_schema() -> None:
    document = build_output_document(
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=UTC),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=UTC),
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
        started_at=datetime(2026, 5, 14, 18, 32, 11, 420000, tzinfo=UTC),
        ended_at=datetime(2026, 5, 14, 18, 35, 11, 890000, tzinfo=UTC),
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
