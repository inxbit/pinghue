"""Runtime configuration protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class RunConfig(Protocol):
    """Attributes needed by the runtime after CLI parsing."""

    targets: list[str]
    file: Path | None
    port: int | None
    interval: float
    timeout: float
    count: int | None
    duration: float | None
    no_tui: bool
    output: Path | None
    no_samples: bool
    concurrency: int
    jitter_threshold: float
    fail_threshold: int
    history_style: str
    numeric: bool
    address_family: str
    check: bool
    quiet: bool
    resolve_name: str | None
    host_label: str
    fail_on_any_down: bool
    fail_on_all_down: bool
    fail_on_down: bool
