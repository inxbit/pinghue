"""Display-safe string helpers."""

from __future__ import annotations


def sanitize_display(value: str) -> str:
    """Escape terminal control characters before rendering operator-visible text."""
    parts: list[str] = []
    for character in value:
        codepoint = ord(character)
        if codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
            parts.append(f"\\x{codepoint:02x}")
        else:
            parts.append(character)
    return "".join(parts)
