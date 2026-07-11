#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
source_html="$root/scripts/site-social-card.html"
output="$root/docs/assets/pinghue-social-card.png"
chrome="$(printenv CHROME_BIN 2>/dev/null || true)"
if [ -z "$chrome" ]; then
  chrome="$(command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || true)"
fi
if [ -z "$chrome" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi
[ -x "$chrome" ] || { printf 'Chrome not found; set CHROME_BIN\n' >&2; exit 1; }

"$chrome" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
  --force-device-scale-factor=1 --window-size=1200,630 --screenshot="$output" "file://$source_html"

python3 - "$output" <<'PY'
import struct
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = path.read_bytes()
if data[:8] != b"\x89PNG\r\n\x1a\n":
    raise SystemExit(f"{path} is not a PNG")
width, height = struct.unpack(">II", data[16:24])
if (width, height) != (1200, 630):
    raise SystemExit(f"expected 1200x630, got {width}x{height}")
print(f"wrote {path} ({width}x{height})")
PY
