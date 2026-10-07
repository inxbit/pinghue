"""Outline the pinghue wordmark (Archivo, wght 750, tracking -0.025em) to SVG paths.

Usage: uv run --no-project --with fonttools --with brotli python scripts/site-wordmark.py IN OUT
IN is the Archivo variable font (OFL). The site no longer ships it; the latin subset the
wordmark was drawn from is the docs/fonts/archivo-var-latin.woff2 removed in the 2026-10
redesign (git log -- that path). OUT is docs/assets/pinghue-wordmark.svg.

"ping" in the page white, "hue" in the committed green-amber-red-blue gradient
(stops from docs/assets/pinghue-hero.svg and main's .wm-hue), so the logo needs
no web font and survives the strict CSP as a plain <img>.
"""

import sys

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

src, out = sys.argv[1], sys.argv[2]
font = TTFont(src)
axes = {a.axisTag: (a.minValue, a.defaultValue, a.maxValue) for a in font["fvar"].axes}
loc = {"wght": 750}
if "wdth" in axes:
    loc["wdth"] = 100
font = instancer.instantiateVariableFont(font, loc)
upm = font["head"].unitsPerEm
cmap = font.getBestCmap()
gs = font.getGlyphSet()
hmtx = font["hmtx"]
track = -0.025 * upm

x = 0.0
parts = {"ping": [], "hue": []}
hue_x0 = hue_x1 = None
bounds = BoundsPen(gs)
for i, ch in enumerate("pinghue"):
    name = cmap[ord(ch)]
    key = "ping" if i < 4 else "hue"
    if key == "hue" and hue_x0 is None:
        hue_x0 = x
    pen = SVGPathPen(gs)
    gs[name].draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))
    parts[key].append(pen.getCommands())
    gs[name].draw(TransformPen(bounds, (1, 0, 0, -1, x, 0)))
    adv = hmtx[name][0]
    x += adv + (track if i < 6 else 0)
    if i == 6:
        hue_x1 = x
xmin, ymin, xmax, ymax = bounds.bounds
pad = 0.02 * upm
vx, vy = xmin - pad, ymin - pad
vw, vh = (xmax - xmin) + 2 * pad, (ymax - ymin) + 2 * pad
scale = 44 / upm  # 44px font-size reference, the comp's wordmark size
view = f"{vx:.1f} {vy:.1f} {vw:.1f} {vh:.1f}"
box = f'viewBox="{view}" width="{vw * scale:.0f}" height="{vh * scale:.0f}"'
ramp = f'x1="{hue_x0:.1f}" y1="0" x2="{hue_x1:.1f}" y2="0"'
svg = f'''<svg xmlns="http://www.w3.org/2000/svg" {box} role="img" aria-label="pinghue">
  <defs>
    <linearGradient id="hue" gradientUnits="userSpaceOnUse" {ramp}>
      <stop offset="0" stop-color="#7ee787"/>
      <stop offset="0.38" stop-color="#f2cc60"/>
      <stop offset="0.68" stop-color="#ff7b72"/>
      <stop offset="1" stop-color="#58a6ff"/>
    </linearGradient>
  </defs>
  <path fill="#f7f8f8" d="{" ".join(parts["ping"])}"/>
  <path fill="url(#hue)" d="{" ".join(parts["hue"])}"/>
</svg>
'''
with open(out, "w") as f:
    f.write(svg)
print("axes", axes, "upm", upm, "size", f"{vw * scale:.0f}x{vh * scale:.0f}", "bytes", len(svg))
