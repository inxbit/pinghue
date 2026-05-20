"""Display-safe string helpers."""

from __future__ import annotations

import re

CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def sanitize_display(value: str) -> str:
    """Escape terminal control characters before rendering operator-visible text."""
    return CONTROL_CHARACTER_PATTERN.sub(
        lambda match: f"\\x{ord(match.group(0)):02x}",
        value,
    )
