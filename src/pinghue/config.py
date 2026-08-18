"""Runtime configuration."""

from __future__ import annotations

import argparse
from pathlib import Path


class RunConfig(argparse.Namespace):
    """Parsed CLI arguments, as consumed by the runtime.

    The CLI parser fills this namespace directly, so this class is the single
    declaration of every option's name and type.
    """

    targets: list[str]
    file: Path | None
    port: int | None
    interval: float
    timeout: float
    count: int | None
    duration: float | None
    no_tui: bool
    output: Path | None
    overwrite: bool
    output_mode: str
    no_samples: bool
    concurrency: int
    jitter_threshold: float
    fail_threshold: int
    history_style: str
    numeric: bool
    ipv4: bool
    ipv6: bool
    address_family: str
    check: bool
    quiet: bool
    resolve_name: str | None
    host_label: str
    fail_on_any_down: bool
    fail_on_all_down: bool
