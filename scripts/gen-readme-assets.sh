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
# Requirements: vhs and ffmpeg (brew install vhs ffmpeg) and pinghue on PATH
# (or an editable install at .venv/bin/pinghue). Needs network access; the
# staged targets are real (healthy public hosts, a refused localhost port, and
# unroutable TEST-NET-1 addresses for the down rows).
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
assets="${root}/docs/assets"

command -v vhs >/dev/null 2>&1 || { printf 'vhs not found (brew install vhs)\n' >&2; exit 1; }
command -v ffmpeg >/dev/null 2>&1 || { printf 'ffmpeg not found (brew install ffmpeg)\n' >&2; exit 1; }

pinghue_bin="$(command -v pinghue 2>/dev/null || true)"
[ -n "${pinghue_bin}" ] || pinghue_bin="${root}/.venv/bin/pinghue"
[ -x "${pinghue_bin}" ] || { printf 'pinghue not found (pip install -e ".[dev]")\n' >&2; exit 1; }
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
Type "export PATH=${bindir}:\$PATH"
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
Type "export PATH=${bindir}:\$PATH"
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

printf 'wrote pinghue-demo.gif and pinghue-screenshot.png to %s\n' "${assets}"
