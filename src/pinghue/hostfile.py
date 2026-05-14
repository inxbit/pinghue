"""Host-file parsing."""

from pathlib import Path


def parse_host_file(path: str | Path) -> list[str]:
    """Return targets from a plain text host file."""
    host_path = Path(path)
    targets: list[str] = []

    for line in host_path.read_text(encoding="utf-8").splitlines():
        value = line.strip()

        if not value or value.startswith("#"):
            continue

        targets.append(value)

    return targets
