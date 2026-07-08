#!/usr/bin/env bash
# Regenerate pinghue's README terminal artwork from the REAL TUI.
#
# Produces two authentic captures in docs/assets/ using vhs + ffmpeg:
#   - pinghue-demo.gif        a short recording of a live probe run
#   - pinghue-screenshot.png  the final frame of a denser run (the still)
#
# The hero image (docs/assets/pinghue-hero.svg) is hand-authored and is not
# regenerated here.
#
# Requirements: vhs and ffmpeg (brew install vhs ffmpeg) plus either an editable
# install at .venv/bin/pinghue or a matching pinghue on PATH. Needs network
# access; the staged targets are real (healthy public hosts, a refused localhost
# port, and unroutable TEST-NET-1 addresses for the down rows).
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
assets="${root}/docs/assets"

command -v vhs >/dev/null 2>&1 || { printf 'vhs not found (brew install vhs)\n' >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { printf 'ffmpeg not found (brew install ffmpeg)\n' >&2; exit 1; }

venv_pinghue="${root}/.venv/bin/pinghue"
if [ -x "${venv_pinghue}" ]; then
  pinghue_bin="${venv_pinghue}"
else
  pinghue_bin="$(command -v pinghue 2>/dev/null || true)"
fi
[ -x "${pinghue_bin}" ] || { printf 'pinghue not found (pip install -e ".[dev]")\n' >&2; exit 1; }
package_version="$(awk -F '"' '/^version = / { print $2; exit }' "${root}/pyproject.toml")"
expected_version="pinghue ${package_version}"
actual_version="$("${pinghue_bin}" --version)"
[ "${actual_version}" = "${expected_version}" ] || {
  printf 'pinghue version mismatch: expected "%s", got "%s"\n' "${expected_version}" "${actual_version}" >&2
  exit 1
}
bindir="$(cd -- "$(dirname -- "${pinghue_bin}")" && pwd)"

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# --- demo gif: a few hosts, selection parked on a down row so healthy rows stay green ---
cat > "${tmp}/demo.tape" <<TAPE
Output "${assets}/pinghue-demo.gif"
Set Shell "bash"
Set FontSize 20
Set Width 1480
Set Height 470
Set Padding 22
Set BorderRadius 16
Set Framerate 24
Hide
Type "unset NO_COLOR; export TERM=xterm-256color COLORTERM=truecolor PATH=${bindir}:\$PATH"
Enter
Type "clear"
Enter
Show
Type "pinghue -p 443 1.1.1.1 8.8.8.8 9.9.9.9 example.com github.com 127.0.0.1 192.0.2.1 --timeout 1"
Enter
Sleep 1500ms
Down@120ms 6
Sleep 12s
TAPE
vhs "${tmp}/demo.tape"

# --- dense screenshot: many hosts, addresses shown; the still is the last frame ---
cat > "${tmp}/shot.tape" <<TAPE
Output "${tmp}/shot.gif"
Set Shell "bash"
Set FontSize 18
Set Width 1600
Set Height 560
Set Padding 22
Set BorderRadius 16
Set Framerate 12
Hide
Type "unset NO_COLOR; export TERM=xterm-256color COLORTERM=truecolor PATH=${bindir}:\$PATH"
Enter
Type "clear"
Enter
Show
Type "pinghue -p 443 1.1.1.1 8.8.8.8 9.9.9.9 example.com github.com cloudflare.com wikipedia.org mozilla.org python.org debian.org kernel.org gitlab.com fastly.com 127.0.0.1 192.0.2.1 192.0.2.2 --timeout 1"
Enter
Sleep 1500ms
Type "a"
Down@90ms 15
Sleep 19s
TAPE
vhs "${tmp}/shot.tape"
ffmpeg -y -loglevel error -sseof -0.5 -i "${tmp}/shot.gif" -update 1 -frames:v 1 "${assets}/pinghue-screenshot.png"
python3 - "${expected_version}" "${assets}/pinghue-demo.gif" "${assets}/pinghue-screenshot.png" <<'PY'
import binascii
import struct
import sys
from pathlib import Path

version = sys.argv[1]
gif_path = Path(sys.argv[2])
png_path = Path(sys.argv[3])
keyword = b"pinghue-version"
text = keyword + b"\0" + version.encode("utf-8")
comment = keyword + b"=" + version.encode("utf-8")


def subblocks(payload: bytes) -> bytes:
    blocks = bytearray()
    for start in range(0, len(payload), 255):
        chunk = payload[start : start + 255]
        blocks.append(len(chunk))
        blocks.extend(chunk)
    blocks.append(0)
    return bytes(blocks)


gif = gif_path.read_bytes()
if not gif.startswith((b"GIF87a", b"GIF89a")):
    raise SystemExit(f"{gif_path} is not a GIF")
pos = 13
packed = gif[10]
if packed & 0x80:
    pos += 3 * (2 ** ((packed & 0x07) + 1))
gif_path.write_bytes(gif[:pos] + b"\x21\xfe" + subblocks(comment) + gif[pos:])


def png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


png = png_path.read_bytes()
signature = b"\x89PNG\r\n\x1a\n"
if not png.startswith(signature):
    raise SystemExit(f"{png_path} is not a PNG")
out = bytearray(signature)
pos = len(signature)
inserted = False
while pos < len(png):
    length = struct.unpack(">I", png[pos : pos + 4])[0]
    kind = png[pos + 4 : pos + 8]
    data = png[pos + 8 : pos + 8 + length]
    end = pos + 12 + length
    if not (kind == b"tEXt" and data.split(b"\0", 1)[0] == keyword):
        out.extend(png[pos:end])
    if kind == b"IHDR" and not inserted:
        out.extend(png_chunk(b"tEXt", text))
        inserted = True
    pos = end
png_path.write_bytes(bytes(out))
PY

printf 'wrote pinghue-demo.gif and pinghue-screenshot.png to %s\n' "${assets}"
