#!/usr/bin/env python3
"""Regenerate pinghue's data-driven README terminal artwork.

Writes two SVGs into ``docs/assets/`` on the shared Slate + Signal visual
system:

* ``pinghue-screenshot.svg`` -- the dense maintenance-window table.
* ``pinghue-demo.svg`` -- the animated probe-cycle demo.

The hero image (``docs/assets/pinghue-hero.svg``) is a hand-authored layout and
is intentionally not generated here. Output is deterministic (fixed RNG seeds),
so re-running this script produces byte-identical files unless the layout code
below changes.

Usage::

    python scripts/gen-readme-assets.py
"""

# This module is dense with inline SVG/data string literals where wrapping to
# the line-length limit would hurt readability more than help it.
# ruff: noqa: E501

from __future__ import annotations

import random
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets"

# Slate + Signal palette (see the README palette table).
BG, PANEL, HEADER, BORDER = "#101418", "#151b22", "#1b2630", "#2a313a"
TEXT, MUTED = "#e6edf3", "#8ea0b8"
GREEN, AMBER, RED, BLUE = "#7ee787", "#f2cc60", "#ff7b72", "#58a6ff"


def _esc(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------- #
# Dense screenshot
# --------------------------------------------------------------------------- #

def build_screenshot() -> str:
    """Return the dense maintenance-window table SVG."""
    random.seed(7)

    x = dict(host=24, address=212, state=360, last=492, min=560, avg=628,
             max=698, jitter=772, loss=858, mode=940, hist=1004)
    hist_w = 372
    w = 1420
    left, right = 12, w - 12
    row_h = 19

    rows = [
        ("1.1.1.1",         "1.1.1.1",       "healthy",      3.31,   2.85,   4.54,  34.0,  3.17, 0.0),
        ("8.8.8.8",         "8.8.8.8",       "healthy",      3.62,   2.22,   3.92,  25.3,  1.93, 0.0),
        ("example.com",     "203.0.113.10",  "healthy",     13.42,  12.86,  14.44,  31.3,  2.42, 0.0),
        ("edge-router",     "203.0.113.12",  "down",         None,   None,   None,  None,  None, 100.0),
        ("core-fw",         "198.51.100.8",  "healthy",     45.78,  44.07,  46.29,  81.8,  4.63, 0.0),
        ("db-primary",      "192.0.2.18",    "healthy",    238.00, 232.94, 238.30, 272.4,  8.12, 0.0),
        ("api-gateway",     "192.0.2.25",    "healthy",     89.74,  89.09,  93.44, 181.5, 11.83, 0.0),
        ("cache-a",         "198.51.100.52", "healthy",     81.36,  79.37,  81.71, 175.6,  8.30, 0.0),
        ("search-a",        "203.0.113.45",  "healthy",     99.81,  98.72, 100.10, 120.5,  2.57, 0.0),
        ("public-dns",      "64.6.64.6",     "healthy",     20.59,  19.42,  21.36,  39.4,  2.92, 0.0),
        ("old-node",        "203.0.113.88",  "down",         None,   None,   None,  None,  None, 100.0),
        ("regional-edge-a", "198.51.100.90", "intermittent",125.80, 112.24, 116.76, 161.6,  7.99, 0.75),
        ("regional-edge-b", "198.51.100.91", "healthy",    105.46, 104.41, 107.33, 191.0,  8.49, 0.0),
        ("regional-edge-c", "198.51.100.92", "intermittent",255.92,254.30, 256.21, 279.0,  3.42, 16.67),
        ("regional-edge-d", "198.51.100.93", "healthy",    137.19, 117.84, 120.62, 141.9,  4.81, 0.0),
        ("regional-edge-e", "198.51.100.94", "healthy",    218.97, 218.47, 221.33, 243.9,  5.32, 0.0),
        ("regional-edge-f", "198.51.100.95", "healthy",    256.76, 254.35, 256.20, 296.9,  4.37, 0.0),
    ]
    state_color = {"healthy": GREEN, "intermittent": AMBER, "down": RED}

    def fmt(value: float | None) -> str:
        return "-" if value is None else f"{value:.2f}"

    def text(px: int, py: int, value: object, cls: str = "", weight: str = "700", size: int = 14) -> str:
        return (f'<text class="mono {cls}" x="{px}" y="{py}" '
                f'font-size="{size}" font-weight="{weight}">{_esc(value)}</text>')

    def history(x0: int, ybase: int, state: str, avg: float | None) -> str:
        """Continuous bottom-aligned bar (green ok, amber slow) that BREAKS into
        red dots on loss/down, mirroring the tool's history glyphs."""
        out: list[str] = []
        n = 58
        seg = hist_w / n
        base = 5 if (avg or 0) < 60 else (7 if avg < 150 else 9)
        for i in range(n):
            px = x0 + i * seg
            r = random.random()
            if state == "down":
                if i % 2 == 0:
                    out.append(f'<rect x="{px:.1f}" y="{ybase-3}" width="3" height="3" fill="{RED}"/>')
                continue
            if state == "intermittent" and r < 0.07:
                out.append(f'<rect x="{px:.1f}" y="{ybase-3}" width="3" height="3" fill="{RED}"/>')
                continue
            slow = r > 0.85
            h = (base + 7) if slow else (base + int(r * 4))
            col = AMBER if slow else GREEN
            out.append(f'<rect x="{px:.2f}" y="{ybase-h}" width="{seg+0.7:.2f}" height="{h}" fill="{col}"/>')
        return "".join(out)

    rows_svg: list[str] = []
    selected_idx = 5  # db-primary highlighted
    table_top = 82
    y = table_top + row_h
    for idx, (host, addr, state, last, mn, avg, mx, jit, loss) in enumerate(rows):
        base = y + idx * row_h
        if idx == selected_idx:
            rows_svg.append(f'<rect x="{left+4}" y="{base-14}" width="{right-left-8}" '
                            f'height="{row_h}" fill="#113a55" opacity="0.55"/>')
        sc = state_color[state]
        last_col = AMBER if (last and last >= 300) else (RED if last is None else TEXT)
        jit_col = AMBER if (jit and jit >= 50) else TEXT
        loss_col = RED if (loss and loss > 0) else (RED if loss is None else TEXT)
        max_col = AMBER if (mx and mx >= 300) else TEXT
        rows_svg.append(text(x["host"], base, host, "green"))
        rows_svg.append(text(x["address"], base, addr, "muted"))
        rows_svg.append(f'<text class="mono" x="{x["state"]}" y="{base}" font-size="14" font-weight="700" fill="{sc}">{_esc(state)}</text>')
        rows_svg.append(f'<text class="mono" x="{x["last"]}" y="{base}" font-size="14" font-weight="700" fill="{last_col}">{_esc(fmt(last))}</text>')
        rows_svg.append(text(x["min"], base, fmt(mn), "text"))
        rows_svg.append(text(x["avg"], base, fmt(avg), "text"))
        rows_svg.append(f'<text class="mono" x="{x["max"]}" y="{base}" font-size="14" font-weight="700" fill="{max_col}">{_esc(fmt(mx))}</text>')
        rows_svg.append(f'<text class="mono" x="{x["jitter"]}" y="{base}" font-size="14" font-weight="700" fill="{jit_col}">{_esc(fmt(jit))}</text>')
        loss_txt = "-" if loss is None else f"{loss:.2f}%"
        rows_svg.append(f'<text class="mono" x="{x["loss"]}" y="{base}" font-size="14" font-weight="700" fill="{loss_col}">{_esc(loss_txt)}</text>')
        rows_svg.append(text(x["mode"], base, "icmp", "muted"))
        rows_svg.append(history(x["hist"], base - 2, state, avg))

    table_bottom = table_top + row_h + len(rows) * row_h + 6
    h = table_bottom + 84

    header_labels = "".join(
        f'<text x="{x[key]}" y="{table_top+1}">{label}</text>'
        for key, label in (
            ("host", "host"), ("address", "address"), ("state", "state"),
            ("last", "last"), ("min", "min"), ("avg", "avg"), ("max", "max"),
            ("jitter", "jitter"), ("loss", "loss"), ("mode", "mode"), ("hist", "history"),
        )
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">pinghue dense maintenance-window monitor</title>
  <desc id="d">A dense terminal table monitoring many hosts at once with the Slate and Signal palette: green healthy rows, amber intermittent rows, red down rows, latency columns, loss percentages, per-host history bars, a legend, and footer keybindings.</desc>
  <defs>
    <linearGradient id="panel" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{PANEL}"/><stop offset="1" stop-color="{BG}"/>
    </linearGradient>
    <style>
      .mono {{ font-family:'SFMono-Regular','JetBrains Mono',Menlo,Consolas,'Liberation Mono',monospace; }}
      .muted {{ fill:{MUTED}; }} .text {{ fill:{TEXT}; }} .green {{ fill:{GREEN}; }}
    </style>
  </defs>

  <rect x="3" y="3" width="{w-6}" height="{h-6}" rx="16" fill="{BG}" stroke="{BORDER}" stroke-width="2"/>

  <!-- window chrome -->
  <rect x="10" y="10" width="{w-20}" height="32" rx="9" fill="{HEADER}"/>
  <rect x="10" y="26" width="{w-20}" height="16" fill="{HEADER}"/>
  <circle cx="30" cy="26" r="6" fill="#ff5f57"/><circle cx="52" cy="26" r="6" fill="#ffbd2e"/><circle cx="74" cy="26" r="6" fill="#28c840"/>
  <text class="mono muted" x="96" y="31" font-size="12.5">pinghue 1.1.1.1 8.8.8.8 example.com edge-router core-fw db-primary api-gateway cache-a ...</text>

  <!-- status band -->
  <rect x="10" y="46" width="{w-20}" height="20" fill="#20313d"/>
  <text class="mono text" x="{w/2}" y="61" text-anchor="middle" font-size="14" font-weight="700">pinghue v2.1.0</text>
  <text class="mono" x="{right-12}" y="61" text-anchor="end" font-size="13" font-weight="700" fill="{AMBER}">18:38:44</text>

  <!-- table surface -->
  <rect x="10" y="{table_top-12}" width="{w-20}" height="{table_bottom-(table_top-12)}" fill="url(#panel)" stroke="{BORDER}"/>
  <rect x="10" y="{table_top-12}" width="{w-20}" height="20" fill="#263541"/>

  <!-- header labels -->
  <g class="mono muted" font-size="13" font-weight="700">
    {header_labels}
  </g>

  {"".join(rows_svg)}

  <!-- legend -->
  <rect x="10" y="{table_bottom}" width="{w-20}" height="26" fill="{BG}" stroke="{BORDER}"/>
  <g class="mono" font-size="13" font-weight="700">
    <text class="muted" x="{x["host"]}" y="{table_bottom+17}">history:</text>
    <rect x="92" y="{table_bottom+9}" width="22" height="8" rx="1" fill="{GREEN}"/><text class="muted" x="122" y="{table_bottom+17}">ok</text>
    <rect x="158" y="{table_bottom+5}" width="14" height="14" rx="1" fill="{AMBER}"/><text class="muted" x="180" y="{table_bottom+17}">slow</text>
    <text x="240" y="{table_bottom+17}" fill="{RED}">.</text><text class="muted" x="250" y="{table_bottom+17}">loss / down</text>
    <text x="360" y="{table_bottom+17}" fill="{AMBER}">!</text><text class="muted" x="370" y="{table_bottom+17}">tcp refused</text>
  </g>

  <!-- footer keybindings -->
  <rect x="10" y="{table_bottom+26}" width="{w-20}" height="30" rx="0" fill="{HEADER}"/>
  <g class="mono" font-size="13" font-weight="700">
    <text x="{x["host"]}" y="{table_bottom+46}" fill="{AMBER}">q</text><text x="{x["host"]+14}" y="{table_bottom+46}" fill="{TEXT}">Quit</text>
    <text x="92" y="{table_bottom+46}" fill="{AMBER}">a</text><text x="106" y="{table_bottom+46}" fill="{TEXT}">Address</text>
    <text x="186" y="{table_bottom+46}" fill="{RED}">r</text><text x="200" y="{table_bottom+46}" fill="{TEXT}">Reset</text>
    <text x="268" y="{table_bottom+46}" fill="{RED}">R</text><text x="284" y="{table_bottom+46}" fill="{TEXT}">Reset all</text>
    <text x="378" y="{table_bottom+46}" fill="{AMBER}">b</text><text x="392" y="{table_bottom+46}" fill="{TEXT}">Probe now</text>
    <text x="500" y="{table_bottom+46}" fill="{BLUE}">B</text><text x="516" y="{table_bottom+46}" fill="{TEXT}">Probe all</text>
  </g>

  <!-- scrollbar -->
  <rect x="{right-6}" y="{table_top+2}" width="6" height="{table_bottom-table_top-8}" rx="3" fill="#263541"/>
  <rect x="{right-6}" y="{table_top+2}" width="6" height="120" rx="3" fill="{MUTED}"/>
</svg>'''


# --------------------------------------------------------------------------- #
# Animated demo
# --------------------------------------------------------------------------- #

def build_demo() -> str:
    """Return the animated probe-cycle demo SVG."""
    random.seed(11)

    w, h = 1200, 360
    hx, hw = 904, 250  # history area x, width
    col = dict(host=44, address=196, state=340, last=470, avg=552, jitter=632,
               loss=740, mode=834, hist=hx)

    rows = [
        ("1.1.1.1",     "1.1.1.1",      "healthy",      "4.38",  "5.49",  "1.76", "0.00%",  "icmp"),
        ("db.internal", "10.42.3.17",   "intermittent", "312.00","38.42", "58.70","7.22%",  "icmp"),
        ("api.edge",    "203.0.113.10", "down",         "-",     "-",     "-",    "100.00%","tcp"),
    ]
    sc = {"healthy": GREEN, "intermittent": AMBER, "down": RED}
    row_y = [128, 164, 200]

    def bars(state: str, ybase: int, avg_small: bool = True) -> str:
        out: list[str] = []
        n = 50
        seg = hw / n
        base = 5 if avg_small else 8
        for i in range(n):
            px = hx + i * seg
            r = random.random()
            if state == "down":
                if i % 2 == 0:
                    out.append(f'<rect x="{px:.1f}" y="{ybase-3}" width="3" height="3" fill="{RED}"/>')
                continue
            if state == "intermittent" and r < 0.07:
                out.append(f'<rect x="{px:.1f}" y="{ybase-3}" width="3" height="3" fill="{RED}"/>')
                continue
            slow = r > 0.85
            h_bar = (base + 7) if slow else base + int(r * 4)
            color = AMBER if slow else GREEN
            out.append(f'<rect x="{px:.2f}" y="{ybase-h_bar}" width="{seg+0.7:.2f}" height="{h_bar}" fill="{color}"/>')
        return "".join(out)

    rows_svg: list[str] = []
    for (host, addr, st, last, avg, jit, loss, mode), y in zip(rows, row_y, strict=True):
        state_color = sc[st]
        last_c = AMBER if st == "intermittent" else (RED if last == "-" else TEXT)
        jit_c = AMBER if st == "intermittent" else TEXT
        loss_c = RED if loss != "0.00%" else TEXT
        if y == row_y[0]:
            rows_svg.append(f'<rect x="32" y="{y-15}" width="1136" height="22" fill="#113a55" opacity="0.45"/>')
        rows_svg += [
            f'<text class="mono" x="{col["host"]}" y="{y}" fill="{GREEN}" font-weight="700">{host}</text>',
            f'<text class="mono" x="{col["address"]}" y="{y}" fill="{MUTED}">{addr}</text>',
            f'<text class="mono" x="{col["state"]}" y="{y}" fill="{state_color}" font-weight="700">{st}</text>',
            f'<text class="mono" x="{col["last"]}" y="{y}" fill="{last_c}" font-weight="700">{last}</text>',
            f'<text class="mono" x="{col["avg"]}" y="{y}" fill="{TEXT}" font-weight="700">{avg}</text>',
            f'<text class="mono" x="{col["jitter"]}" y="{y}" fill="{jit_c}" font-weight="700">{jit}</text>',
            f'<text class="mono" x="{col["loss"]}" y="{y}" fill="{loss_c}" font-weight="700">{loss}</text>',
            f'<text class="mono" x="{col["mode"]}" y="{y}" fill="{MUTED}">{mode}</text>',
            bars(st, y - 2, avg_small=(st != "intermittent")),
        ]

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-labelledby="t d">
  <title id="t">animated pinghue terminal demo</title>
  <desc id="d">A looping terminal animation: a live probe cursor sweeps across continuous green and amber history bars that break into red dots on the down host, then a JSON summary write is confirmed.</desc>
  <defs>
    <linearGradient id="panel" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{PANEL}"/><stop offset="1" stop-color="{BG}"/></linearGradient>
    <linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{BLUE}" stop-opacity="0"/>
      <stop offset="0.5" stop-color="{BLUE}" stop-opacity="0.55"/>
      <stop offset="1" stop-color="{BLUE}" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="hclip"><rect x="{hx-4}" y="104" width="{hw+8}" height="108"/></clipPath>
    <style>.mono{{font-family:'SFMono-Regular','JetBrains Mono',Menlo,Consolas,'Liberation Mono',monospace;}}</style>
  </defs>

  <rect x="3" y="3" width="{w-6}" height="{h-6}" rx="16" fill="{BG}" stroke="{BORDER}" stroke-width="2"/>

  <rect x="24" y="22" width="{w-48}" height="316" rx="12" fill="url(#panel)" stroke="{BORDER}"/>
  <rect x="24" y="22" width="{w-48}" height="34" rx="12" fill="{HEADER}"/>
  <rect x="24" y="44" width="{w-48}" height="12" fill="{HEADER}"/>
  <circle cx="46" cy="39" r="6" fill="#ff5f57"/><circle cx="68" cy="39" r="6" fill="#ffbd2e"/><circle cx="90" cy="39" r="6" fill="#28c840"/>
  <text class="mono" x="{w/2}" y="44" text-anchor="middle" font-size="14" font-weight="700" fill="{TEXT}">pinghue v2.1.0</text>
  <text class="mono" x="{w-40}" y="44" text-anchor="end" font-size="14" fill="{AMBER}">probe cycle
    <animate attributeName="opacity" values="0.55;1;0.55" dur="2.4s" repeatCount="indefinite"/></text>

  <g class="mono" font-size="15">
    <g font-size="13" font-weight="700" fill="{MUTED}">
      <text x="{col['host']}" y="100">host</text>
      <text x="{col['address']}" y="100">address</text>
      <text x="{col['state']}" y="100">state</text>
      <text x="{col['last']}" y="100">last</text>
      <text x="{col['avg']}" y="100">avg</text>
      <text x="{col['jitter']}" y="100">jitter</text>
      <text x="{col['loss']}" y="100" fill="{MUTED}">loss</text>
      <text x="{col['mode']}" y="100">mode</text>
      <text x="{col['hist']}" y="100">history</text>
    </g>
    {"".join(rows_svg)}
  </g>

  <!-- live probe sweep over the history area -->
  <g clip-path="url(#hclip)">
    <rect x="0" y="104" width="60" height="108" fill="url(#sweep)">
      <animate attributeName="x" values="{hx-64};{hx+hw+4}" dur="3.0s" repeatCount="indefinite"/>
    </rect>
  </g>

  <!-- command + JSON write confirmation -->
  <line x1="40" y1="240" x2="{w-40}" y2="240" stroke="{BORDER}"/>
  <text class="mono" x="44" y="272" font-size="14" fill="{MUTED}">$ pinghue -f hosts.txt --duration 180 --output maintenance.json --overwrite</text>
  <g>
    <animate attributeName="opacity" values="0;0;1;1;1" keyTimes="0;0.45;0.6;0.95;1" dur="3.0s" repeatCount="indefinite"/>
    <text class="mono" x="44" y="302" font-size="14" fill="{GREEN}" font-weight="700">→ wrote maintenance.json  ·  3 targets  ·  schema_version 1</text>
  </g>
</svg>'''


def main() -> None:
    (ASSETS / "pinghue-screenshot.svg").write_text(build_screenshot())
    (ASSETS / "pinghue-demo.svg").write_text(build_demo())
    print(f"wrote pinghue-screenshot.svg and pinghue-demo.svg to {ASSETS}")


if __name__ == "__main__":
    main()
