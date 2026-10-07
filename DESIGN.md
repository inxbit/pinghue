---
name: pinghue
description: A near-black developer-tool page whose hero is the real pinghue TUI, running a simulated maintenance window.
colors:
  ground: "#08090a"
  text: "#f7f8f8"
  text-secondary: "#b4bac4"
  text-muted: "#8a8f98"
  hairline: "#23252a"
  hairline-strong: "#30343c"
  chip: "rgb(14 16 18 / 0.86)"
  tui-ground: "#0d1014"
  tui-bar: "#11151a"
  tui-text: "#e6edf3"
  tui-muted: "#8ea0b8"
  tui-line: "#232a33"
  signal-green: "#7ee787"
  signal-amber: "#f2cc60"
  signal-red: "#ff7b72"
  cursor-blue: "#58a6ff"
  key-ground: "#151a20"
typography:
  display:
    fontFamily: "Zalando Sans, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "max(40px, calc(78 * var(--u)))"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Zalando Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "max(30px, calc(54 * var(--u)))"
    fontWeight: 700
    lineHeight: 1.08
    letterSpacing: "-0.032em"
  lede:
    fontFamily: "Zalando Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "max(16px, calc(27 * var(--u)))"
    fontWeight: 300
    lineHeight: "max(24px, calc(34 * var(--u)))"
    letterSpacing: "-0.038em"
  title:
    fontFamily: "Zalando Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "max(17px, calc(21 * var(--u)))"
    fontWeight: 400
    lineHeight: 1.55
  body:
    fontFamily: "Zalando Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Zalando Sans, ui-sans-serif, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 500
    lineHeight: 1.5
  mono-data:
    fontFamily: "Ubuntu Sans Mono, ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "max(14px, calc(20 * var(--u)))"
    fontWeight: 400
    lineHeight: "max(20px, calc(26 * var(--u)))"
    fontFeature: "tnum"
  mono-small:
    fontFamily: "Ubuntu Sans Mono, ui-monospace, SF Mono, Menlo, Consolas, monospace"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.7
rounded:
  key: "6px"
  inner: "8px"
  control: "10px"
  surface: "12px"
  terminal: "calc(14 * var(--u))"
spacing:
  section-desktop: "calc(150 * var(--u))"
  section-tablet: "96px"
  section-phone: "72px"
  content-width: "min(calc(100% - 40px), calc(1240 * var(--u)))"
components:
  button:
    backgroundColor: "{colors.chip}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "max(48px, calc(56 * var(--u)))"
    padding: "0 max(20px, calc(28 * var(--u))) 0 max(16px, calc(22 * var(--u)))"
  command-chip:
    backgroundColor: "{colors.chip}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    height: "max(52px, calc(62 * var(--u)))"
  copy-button:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.inner}"
    size: "44px"
  nav-link:
    textColor: "{colors.text-secondary}"
    typography: "{typography.label}"
  nav-link-hover:
    textColor: "{colors.text}"
  tab:
    backgroundColor: "transparent"
    textColor: "{colors.text-muted}"
    rounded: "{rounded.inner}"
    height: "40px"
    padding: "0 18px"
  key-cap:
    backgroundColor: "{colors.key-ground}"
    textColor: "{colors.tui-text}"
    rounded: "{rounded.key}"
    height: "max(24px, calc(30 * var(--u)))"
  window:
    backgroundColor: "{colors.tui-ground}"
    textColor: "{colors.tui-text}"
    rounded: "{rounded.surface}"
  window-bar:
    backgroundColor: "{colors.tui-bar}"
    textColor: "{colors.text-muted}"
    height: "40px"
    padding: "0 16px"
  hero-terminal:
    backgroundColor: "{colors.tui-ground}"
    textColor: "{colors.tui-text}"
    typography: "{typography.mono-data}"
    rounded: "{rounded.terminal}"
    width: "calc(1472 * var(--u))"
---

# Design System: pinghue

## Overview

**Creative North Star: "The Table Is the Hero"**

pinghue.com is the dark developer-tool page done at full craft, with linear.app as the quality bar. A near-black ground, one clean grotesk, hairline structure, and nothing in color except the product. The hero is not a picture of the tool: it is the real pinghue TUI layout (the columns, states, eight-step glyph scale and key bindings from `src/pinghue`) running a deterministic simulated night from `docs/site.js`. Its keys work, it has a pause button, and dim Higgsfield footage of a data-center aisle at night plays behind it, shaded only where the copy and the table sit.

Everything outside the terminal windows stays neutral on purpose, so the table is the one thing on the page that carries hue. Because pinghue's name is its thesis (ping plus hue, color is state), color on this site means state and nothing else. Density is calm and generous outside the windows and dense inside them, the way a terminal is.

Desktop geometry is measured off the approved 1672 px comp and scaled through one unit, `--u`, so the page lands where the comp drew it. Rejected and not to return: neon-on-slate signal styling with scroll-story chapters, an all-monospace light world, SaaS gradient blobs, fake dashboards.

**Key Characteristics:**
- Near-black ground, three neutral text tones, hairline borders; the page itself is achromatic.
- The real TUI as the hero, live and keyboard-operable, over dim rack footage.
- Product colors only inside terminal windows, each with a state meaning.
- One grotesk (Zalando Sans) for everything human; one mono (Ubuntu Sans Mono) only for commands and data.
- Comp-true desktop sizes through `--u`, with px floors on type and controls.

## Colors

An achromatic near-black page wrapped around a terminal that is the only colored thing in view.

### Primary
- **Healthy Green** (signal-green): healthy state, ok probes, host and address cells, the copied checkmark. Exactly `GREEN` in `src/pinghue/ui.py`.
- **Slow Amber** (signal-amber): slow latency (over 100 ms), intermittent state, the refused mark. Exactly `AMBER` in `ui.py`.
- **Loss Red** (signal-red): loss, down state, failed probes, a failed copy. Exactly `RED` in `ui.py`.

### Secondary
- **Cursor Blue** (cursor-blue): the interaction color. The DataTable cursor tint at 22% (the unfocused value from `app.py`; the site does not use the 32% or 14% variants), the active key cap, the focus ring, text selection at 32%, and numbers in the JSON report.

### Neutral
- **Rack Black** (ground): the page ground, `theme-color`, and the fade target of the footage.
- **Paper White** (text): headlines, primary copy, button labels.
- **Ash** (text-secondary): ledes, nav links, list copy, footer links. A build addition beyond the direction contract's two text tones; it is the middle of the three page text tones.
- **Steel Gray** (text-muted): prompts, captions, notes, inactive tabs, the terminal clock.
- **Hairline** (hairline): section dividers, ruler cells, the not-list rules.
- **Strong Hairline** (hairline-strong): borders of controls (buttons, command chips, tabs, key caps) and the nav divider. Also a build addition.
- **Smoked Chip** (chip): the translucent ground of buttons, command chips and the tab rail, so footage reads faintly through them.
- **Window Ground** (tui-ground) and **Window Bar** (tui-bar): the terminal windows' own grounds. They are page-owned values, deliberately darker than the TUI's real `#101418` from `app.py`, so the windows sit in the page instead of on it.
- **Key Ground** (key-ground): the ground of every key cap, inline and in the hero footer.
- **Terminal Text** (tui-text), **Terminal Muted** (tui-muted), **Terminal Line** (tui-line): text, headers and rules inside windows. Text and muted equal `TEXT` and `MUTED` in `ui.py`.

### Named Rules
**The Color Is State Rule.** The three signal tokens (green, amber, red) appear only with state meaning: a probe result, a host state, a slow or lossy cell, a copy succeeding or failing. Three carve-outs are native to this world and stay: the hue wordmark gradient (green, amber, red, blue, a brand commitment), the macOS traffic-light dots in window bars (their own hexes, never the signal tokens), and cursor blue as focus ring and selection.

**The Product Owns Its Palette Rule.** Terminal text and state colors are the product's, not the site's. If `src/pinghue/ui.py` changes, the stylesheet follows; the test enforces it.

## Typography

**Display Font:** Zalando Sans (with ui-sans-serif, system-ui fallback), self-hosted variable woff2
**Body Font:** Zalando Sans
**Label/Mono Font:** Ubuntu Sans Mono (with ui-monospace, SF Mono, Menlo fallback), self-hosted variable woff2

**Character:** A tight, confident grotesk set with negative tracking at display sizes, against a humanist mono that reads like a real terminal. The mono never sets prose.

### Hierarchy
- **Display** (700, max(40px, 78u), 1.1, -0.035em): the one hero headline, balanced. The 404 headline uses the same voice at clamp(40px, 7vw, 72px).
- **Headline** (700, max(30px, 54u), 1.08, -0.032em): section titles, balanced.
- **Lede** (300, max(16px, 27u), -0.038em): the single line under the hero headline, in Ash.
- **Title** (400, max(17px, 21u), 1.55): section ledes, max 34em, in Ash.
- **Body** (400, 17px, 1.5): running copy, pane notes (max 44em), the not-list.
- **Label** (500, 15px): buttons, form labels. 600 marks definition terms and strong runs.
- **Mono data** (400, max(14px, 20u), tabular numerals): the hero table. Mini tables, code windows and command rows run at 14 to 15px.

The fixed small steps are tokens: 13px (bands, captions), 14px (windows, code), 15px (notes, facts, tabs, command rows), 17px (body).

### Named Rules
**The Mono Is Data Rule.** Ubuntu Sans Mono sets only commands, code, keys and terminal content. Headlines, ledes, labels and links are Zalando Sans.

**The Tabular Numbers Rule.** Every live number (latencies, the clock, the lab readout) uses tabular numerals so columns never jitter while the run ticks.

## Layout

One centered column. The hero stacks nav, headline, lede, the install chip beside View on GitHub, then the terminal at 1472 comp px (about 88% of the comp) filling the lower part of the first viewport. Below it, full-width sections divided by a single hairline, content at `min(100% - 40px, 1240u)`. Two-column splits run 5fr to 7fr (copy beside a window) and collapse to one column below 1100px.

**The comp unit.** `--u` is one comp pixel: `min(100cqw, 1840px) / 1672`, measured on the body's inline-size container. Desktop stops growing at 1840px. Type sizes, control heights and paddings carry px floors through `max(Npx, calc(N * var(--u)))`; widths, gaps and the hero table's column widths scale freely. Below 1100px `--u` freezes at 0.658px (its 1100px value), so every size is continuous across the breakpoint and the floors carry smaller screens.

**Rhythm.** Section padding is 150u on desktop, 96px under 1100px, 72px under 700px. Narrow screens hide terminal columns rather than shrink type: under 900px the hero table keeps host, state, last, loss and history; under 380px it drops last; the nav links leave under 700px and the GitHub button becomes a 44px icon with its label kept for assistive tech.

## Elevation & Depth

Flat and tonal. Depth comes from the footage behind the hero, the shade gradients that darken only the copy and table zones, and translucent smoked chips over the footage. Secondary terminal windows carry one soft ambient drop; nothing else casts a shadow.

### Shadow Vocabulary
- **Window drop** (`box-shadow: 0 32px 64px -28px rgb(0 0 0 / 0.75)`): terminal windows outside the hero only.
- **Selected tab ring** (`box-shadow: inset 0 0 0 1px var(--line-2)`): the selected tab in the tab rail.
- **Slider thumb** (`box-shadow: 0 2px 10px rgb(0 0 0 / 0.6)`): the latency lab's range thumb.

### Named Rules
**The Footage Stays Dim Rule.** The aisle footage is atmosphere. It is shaded under the headline and the table, fades to the ground at the hero's foot, loads only while the hero is on screen, and never plays under reduced motion or reduced data unless the visitor asks.

## Shapes

Softly rounded rectangles with 1px borders. Controls round at 10px, surfaces and the tab rail at 12px, inner pills (copy button, tabs) at 8px, key caps at 6px (5px inline), the hero terminal and the scale ruler at 14u. Status-light dots and failed-probe marks are circles; history bars are 1px-rounded columns in eighths of a cell. Borders are hairlines; there are no thick strokes.

## Components

### Buttons
Quiet and solid, never colored.
- **Shape:** gently rounded (10px).
- **Default:** smoked chip ground, Strong Hairline border, Paper White label at 500, an inline SVG icon at 1.32em.
- **Hover:** border lifts to a lighter gray and the ground to a near-opaque dark, 0.2s ease-out.
- **Focus:** a 2px cursor-blue outline, 3px offset.
- No filled or accent-colored primary exists; the install command chip is the primary action.

### Command Chip
The primary call to action is the command itself: a `$` prompt in Steel Gray, the command in mono Paper White, and a 44px copy button. Copy confirms with a green check or turns red on failure. The install section repeats it as full-width rows.

### Tabs
A rail on the smoked chip ground (12px, 4px inset). Tabs are mono labels in Steel Gray; the selected tab gets a darker ground, an inset hairline ring and Paper White. Hidden without JavaScript, where all panes show stacked.

### Navigation
Absolute over the hero, 94% Rack Black with a soft hairline. The wordmark sits left; Ash links brighten to white on hover; a vertical hairline separates them from the GitHub button.

### Key Caps
Mono caps on the Key Ground with a Strong Hairline border. In the hero footer they are real buttons that drive the simulation; the active one takes cursor blue. Without JavaScript they render disabled as the TUI's footer.

### Terminal Windows
Window Ground, a Window Bar with three traffic-light dots and a centered mono title, 1px Terminal Line borders, 12px corners, the window drop. Content is either a mini TUI table or a `pre` of commands and JSON.

### The Hero Terminal (signature)
The real TUI layout at 1472u: bar with dots, `PingHUE v{version}` title, a tabular clock and a pause button; the ten-column table plus history bars; the key footer masked to fade out; a muted caption. The run is deterministic (`docs/site.js`), loops after its simulated hour, starts paused under reduced motion, and accepts q, a, r, R, b, B and the arrow keys when focused. The no-JS rows in `docs/index.html` are generated from the same run.

### Latency Scale
A ten-cell ruler of the eight glyph heights plus the fail and refused marks, each with a mono band label, and a slider lab that maps a latency to its glyph live. Cells reflow 10, 5, then 2 per row.

### The Room Below the Hero
The sections below the hero read as one dark machine room where the hero's run keeps going.
- **Far row:** six history rows sit under the hero, one per host, lined up under the table's history column. `site.js` builds them from the same simulated run.
- **Floor:** they stand on a raised floor drawn in perspective (`assets/room-floor.svg`, hairline tokens only). It fades out before the Modes heading, and the same floor hangs from the Install rule.
- **Bands:** each section ends on one host's history band from that run, colored by real probe state at low strength and labeled with the host in muted mono:
  - Modes: edge-router-1
  - Scale: db-primary
  - Evidence: backup-nas
  - Small on purpose: dns-resolver
  - Install: api-gw
- **Light and grain:** terminal windows cast a soft light pool (#e6edf3 at 2 to 10 percent). A fine film grain (`assets/room-grain.svg`) gives the black some material.
- **Motion:** one sweep of light runs along each rule as it enters view (1.6 s), then the band standing on that rule runs up once (2.6 s). Nothing moves under reduced motion, and forced colors drop the room entirely.

## Do's and Don'ts

### Do:
- **Do** size desktop rules off the 1672px comp through `--u`, with a px floor on every type size, control height and padding.
- **Do** keep the page achromatic and let the terminal be the only thing in color.
- **Do** take terminal text and state colors from `src/pinghue/ui.py`; `tests/site_pages.test.mjs` reads the palette, columns, keys and limits from `src/pinghue` and fails when the page drifts.
- **Do** run `node scripts/site-rows.mjs` after changing the simulated run, so the no-JS rows match what `site.js` paints.
- **Do** run `node scripts/site-version.mjs` to set the version the site shows (hero title, 404 title, JSON report, social card).
- **Do** use inline SVG for icons, from the page's sprite.
- **Do** honor reduced motion: transitions collapse, the run starts paused, the footage does not autoplay.

### Don't:
- **Don't** add inline `style` attributes, inline event handlers or inline scripts; both pages ship a strict CSP with no `unsafe-inline`.
- **Don't** use green, amber or red without a state meaning, outside the wordmark carve-out.
- **Don't** set prose, headlines or labels in the mono.
- **Don't** use em or en dashes anywhere in copy; the test rejects them.
- **Don't** add eyebrow labels above section headlines, gradient blobs, neon glows, or scroll-story chapters.
- **Don't** fake the product: no invented columns, states or keys the TUI does not have.
