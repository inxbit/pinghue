"""JSON export support."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pinghue import __version__
from pinghue.display import sanitize_display
from pinghue.models import ProbeConfig, ProbeSample, TargetRun

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


def write_output_json(path: str | Path, **kwargs: Any) -> None:
    """Write a JSON export document to disk."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = build_output_document(**kwargs)
    output_text = json.dumps(document, indent=2, sort_keys=False) + "\n"
    tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    try:
        tmp_path.write_text(output_text, encoding="utf-8")
        tmp_path.replace(output_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
