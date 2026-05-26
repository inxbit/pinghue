"""Display-safe string helpers."""

from __future__ import annotations

import re

UNSAFE_DISPLAY_CHARACTER_PATTERN = re.compile(r"[^\x20-\x7e]")


def _escape_display_character(match: re.Match[str]) -> str:
    codepoint = ord(match.group(0))
    if codepoint <= 0xFF:
        return f"\\x{codepoint:02x}"
    if codepoint <= 0xFFFF:
        return f"\\u{codepoint:04x}"
    return f"\\U{codepoint:08x}"


def sanitize_display(value: str) -> str:
    """Escape ambiguous/control characters before rendering operator-visible text."""
    return UNSAFE_DISPLAY_CHARACTER_PATTERN.sub(_escape_display_character, value)
