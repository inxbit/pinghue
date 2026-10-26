#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
source_html="$root/scripts/site-touch-icon.html"
output="$root/docs/assets/apple-touch-icon.png"
output_dir="$(dirname -- "$output")"
temp_stub=""
temp_output=""
cleanup() {
  if [ -n "$temp_stub" ]; then
    rm -f -- "$temp_stub"
  fi
  if [ -n "$temp_output" ]; then
    rm -f -- "$temp_output"
  fi
}
trap cleanup EXIT HUP INT TERM

chrome="$(printenv CHROME_BIN 2>/dev/null || true)"
if [ -z "$chrome" ]; then
  chrome="$(command -v google-chrome 2>/dev/null || command -v chromium 2>/dev/null || true)"
fi
if [ -z "$chrome" ] && [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
  chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
fi
[ -x "$chrome" ] || { printf 'Chrome not found; set CHROME_BIN\n' >&2; exit 1; }

temp_stub="$(mktemp "$output_dir/.apple-touch-icon.tmp.XXXXXX")"
temp_output="${temp_stub}.png"
mv -f -- "$temp_stub" "$temp_output"
temp_stub=""
"$chrome" --headless=new --disable-gpu --hide-scrollbars --allow-file-access-from-files \
  --force-device-scale-factor=1 --window-size=180,180 --screenshot="$temp_output" "file://$source_html"

python3 - "$temp_output" <<'PY'
import struct
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = path.read_bytes()
if data[:8] != b"\x89PNG\r\n\x1a\n":
    raise SystemExit(f"{path} is not a PNG")
width, height = struct.unpack(">II", data[16:24])
if (width, height) != (180, 180):
    raise SystemExit(f"expected 180x180, got {width}x{height}")
PY

mv -f -- "$temp_output" "$output"
temp_output=""
printf 'wrote %s (180x180)\n' "$output"
trap - EXIT HUP INT TERM
