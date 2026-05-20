"""Host-file parsing."""

from pathlib import Path

MAX_HOST_FILE_BYTES = 1024 * 1024
MAX_HOST_FILE_LINES = 5_000


def parse_host_file(path: str | Path) -> list[str]:
    """Return targets from a plain text host file."""
    host_path = Path(path)
    if not host_path.is_file():
        raise ValueError(f"host file must be a regular file: {host_path}")

    if host_path.stat().st_size > MAX_HOST_FILE_BYTES:
        raise ValueError(
            f"host file too large; maximum is {MAX_HOST_FILE_BYTES} bytes: {host_path}"
        )

    targets: list[str] = []

    for line_number, line in enumerate(host_path.read_text(encoding="utf-8").splitlines(), start=1):
        if line_number > MAX_HOST_FILE_LINES:
            raise ValueError(
                f"host file has too many lines; maximum is {MAX_HOST_FILE_LINES}: {host_path}"
            )

        value = line.split("#", 1)[0].strip()

        if not value:
            continue

        targets.append(value)

    return targets
