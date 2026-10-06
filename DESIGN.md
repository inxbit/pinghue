---
name: pinghue.com
description: The table is the terrain. Black typewriter ink on warm paper, one monospace grid, drawn in pinghue's own latency glyphs.
colors:
  paper: "#efede6"
  ink: "#161513"
  ink-2: "#4f4d48"
  rule: "#b3afa4"
  green: "#1d7a37"
  amber: "#9c5a00"
  red: "#b8322b"
  blue: "#1f5fbf"
typography:
  display:
    fontFamily: "\"Inconsolata\", ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "calc(16.28 * var(--u))"
    fontWeight: 400
    lineHeight: 1
    fontVariation: "\"wdth\" 100"
  headline:
    fontFamily: "\"Inconsolata\", ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "calc(35 * var(--u))"
    fontWeight: 400
    lineHeight: 1
    fontVariation: "\"wdth\" 100"
  title:
    fontFamily: "\"Inconsolata\", ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "calc(27.3 * var(--u))"
    fontWeight: 400
    lineHeight: 1.4
    fontVariation: "\"wdth\" 100"
  body:
    fontFamily: "\"Inconsolata\", ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "max(15px, calc(23.4 * var(--u)))"
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: "\"tnum\""
    fontVariation: "\"wdth\" 80"
  label:
    fontFamily: "\"Inconsolata\", ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "max(12px, calc(21 * var(--u)))"
    fontWeight: 400
    lineHeight: 1.45
    fontVariation: "\"wdth\" 80"
  table:
    fontFamily: "\"Inconsolata\", ui-monospace, \"SF Mono\", Menlo, Consolas, monospace"
    fontSize: "max(12.5px, calc(17.5 * var(--u)))"
    fontWeight: 400
    lineHeight: "calc(30.4 * var(--u))"
    fontFeature: "\"tnum\""
    fontVariation: "\"wdth\" 80"
rounded:
  none: "0"
spacing:
  gutter-left: "calc(30 * var(--u))"
  gutter-right: "calc(37 * var(--u))"
  column-gap: "calc(60 * var(--u))"
  side-column: "calc(560 * var(--u))"
  row-gap: "calc(40 * var(--u))"
  stacked-gutter: "16px"
  hit-target: "44px"
components:
  copy-button:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "0 0.15em"
    height: "{spacing.hit-target}"
  copy-button-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  tilt-toggle:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.none}"
    padding: "10px 0"
  tilt-toggle-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  nav-link:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    typography: "{typography.title}"
    height: "{spacing.hit-target}"
  nav-link-hover:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
  skip-link:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    padding: "10px 14px"
  table-cell:
    textColor: "{colors.ink}"
    typography: "{typography.table}"
    padding: "0 0 0 calc(10 * var(--u))"
  range-thumb:
    backgroundColor: "{colors.ink}"
    rounded: "{rounded.none}"
    width: "18px"
    height: "30px"
---

# Design System: pinghue.com

## Overview

**Creative North Star: "The Table Is the Terrain"**

pinghue.com is a printed page from a terminal. Black typewriter ink sits on warm paper, everything is set on one monospace grid, and the page is drawn with pinghue's own fixed latency ramp (▁▂▃▄▅▆▇█). The signature move is a canvas terrain, one ridge per host, that tilts flat into the exact table pinghue prints: head-on, each ridge is the history column. The headline is not set in a display face; it is typed in full-block glyphs and cut into cells by a CSS mask, so even the letterforms are made of the tool's own marks.

The density is that of a terminal at 2am: tight line heights, tabular numerals, commands shown exactly as they are typed, prompts (`$ `, `# `, `- `) in secondary ink. Color is withheld from everything except state. There are no boxes, cards, glows, shadows or gradient fills; structure comes from rule lines and the grid. Interactive controls are plain words in square brackets that invert to paper-on-ink when touched.

The page refuses the dark headline-left, terminal-right dev-tool hero and the SaaS gradient-blob hero. It reads as if the people who wrote the tool also printed its manual.

**Key Characteristics:**
- Ink on warm paper, one monospace face, width axis doing the work of a type pairing.
- Green, amber and red appear only on latency data and state words.
- The glyph ramp is the house silhouette: headline, terrain, runway, history column, footer mark, 404.
- Desktop geometry is the approved comp's own pixels (1u = frame/1586); below 1100px the page stacks.
- Flat: depth is drawn (perspective plane, tonal ridges), never cast.
- Everything that moves on a clock can be paused, and reduced motion gets the final frame.

## Colors

A two-ink paper palette with three state colors that are only ever data, and one functional blue for focus.

### Primary
- **Typewriter Ink** (ink): all text, the wordmark, the block-glyph headline, structural 1px edges (transcript and JSON left borders, runway baseline, dawn band, footer top), the slider thumb, and the inverted hover fill.

### Secondary
- **Faded Ink** (ink-2): prompts and comment prefixes, deck, ledes, notes, transcript notes, definition text, labels such as the install `via` column and runway limits. Prose that is commentary, not command.

### Tertiary (state colors, data only)
- **Healthy Green** (green): `healthy` state, glyphs at or under 100ms.
- **Slow Amber** (amber): `intermittent` state, glyphs and values over 100ms, jitter over threshold, refused-connection marks.
- **Down Red** (red): `down` state, loss, failed probes (`·`), the null `-` in a failed cell.

### Neutral
- **Warm Paper** (paper): the only background; also `theme-color`, scrollbar track, and the text color inside inversions and selection.
- **Pencil Rule** (rule): 1px dotted row separators, 1px solid table header line, 1px dashed sheet boundaries. Never text.
- **Focus Blue** (blue): the 2px `:focus-visible` outline only.

The terrain canvas carries its own drawing inks (a secondary ink `#5d5b55`, floor `#a9a59a`, plane `#c6c2b7`) and per-band foot-to-crest shades of green and amber. These are canvas internals, not page tokens.

### Named Rules
**The Color Is State Rule.** Green, amber and red appear only where they carry pinghue's latency meaning: a state word, a value past a threshold, or a glyph in the scale. Never on a heading, a link, a button, a border or a decoration.

**The Focus Only Blue Rule.** Blue exists for the keyboard focus ring and nothing else.

**The Inversion Rule.** Hover, selection and the active runway step are all the same treatment: ink fill, paper text. There is no other highlight color.

## Typography

**Display Font:** Inconsolata variable (self-hosted ASCII subset, with ui-monospace, SF Mono, Menlo, Consolas fallback)
**Body Font:** Inconsolata, same file
**Label/Mono Font:** Inconsolata, same file

**Character:** One face, two widths. Running text is condensed on the `wdth` axis to 80%; the top line, section headings, glyph art and display numerals open to 100%. The pairing is the width axis, not a second family.

### Hierarchy
- **Display** (400, block glyphs at 16.28u wide line / 14.08u second line, line-height 1): the "THE TABLE IS THE TERRAIN." headline only, typed as rows of `█` from `scripts/site-headline.mjs` and cut into cells by the cell mask. The real text sits in a visually hidden span.
- **Headline** (400, 35u, line-height 1, stacked 22px): section headings, lowercase sentences ("one scale, every run").
- **Title** (400, 27.3u, wdth 100, stacked 17px): the top line and footer wordmark; nav at 25u.
- **Body** (400, max(15px, 23.4u), line-height 1.45, tabular numerals, stacked 15px / 16px from 700px): all prose and commands. Ledes and notes cap at 68ch, transcript notes at 78ch.
- **Command** (400, 28.1u): the numbered hero steps and the install list, the lines a visitor copies.
- **Label** (400, max(12px, 21u), ink-2): deck, install `via`, runway limits.
- **Table** (400, max(12.5px, 17.5u), line-height 30.4u, stacked 13px / 14px): the TUI table; transcripts and JSON sit at max(12.5px, 18.5u) and max(12.5px, 21u).

### Named Rules
**The One Face Rule.** Inconsolata is the only family. Contrast comes from the width axis (80% text, 100% display) and from ink versus faded ink, never from a second face. Weight stays 400 everywhere except 600 on inline key names in transcript notes.

**The Lowercase Voice Rule.** Headings, nav, controls, deck, labels and state words are lowercase; running prose is sentence case. Capitals as display appear only in the block-glyph letterforms.

**The Block Loading Rule.** The font loads with `font-display: block`; the glyph art is cut on a 0.5em grid and a fallback face mis-tiles it.

**The No Dash Rule.** Visible copy carries no em or en dashes, literal or as entities; `tests/site_pages.test.mjs` enforces it on both pages. Use a period, a comma, or a colon. The hyphen `-` is the null value in a failed table cell.

## Layout

**Desktop comp frame (1100px and up).** The frame is `min(100cqw, 1840px)` on a body container, and one unit is `1u = frame / 1586`. Every desktop measure is written as `calc(N * var(--u))`, so the approved comp scales without reflowing and stops growing at 1840px. The hero is an absolute frame 1586u by 852u: copy column at left 30u, top 124u, 720u wide; terrain stage at left 575u, top 99u, 981u by 753u, with the table placed inside it at 137u, 479u. The top line overlays the hero absolutely.

**Sheets.** Below the hero, each section is a sheet 1586u wide with gutters of 30u left and 37u right, separated by a dashed rule. Two-column sheets (evidence, dawn) use a flexible text column plus a 560u side column with a 60u gap. Vertical rhythm is set per section in u (40u row gaps, 96u to 110u bottom padding).

**Stacked layout (below 1100px).** Everything becomes static flow. The unit becomes `100cqw / 760`, gutters are 16px, type switches to literal pixels (body 15px, 16px from 700px; headings 22px; table 13px, 14px from 700px). The stage becomes a canvas band `min(68cqw - 12px, 540px)` tall above the table, the table drops its avg and jitter columns and right-anchors the history column, and two-column grids collapse to one. The head-on toggle hides when the stage is narrow.

**Narrow (below 700px).** The narrow headline variant (three lines, 33 cells wide) replaces the wide one, and deck lines run inline.

**Standalone pages.** The 404 is a centered column `min(100% - 2rem, 720px)` on rem and clamp sizing; it shares the palette, face and rules, not the comp frame.

### Named Rules
**The Comp Unit Rule.** Desktop measures are multiples of u, not px or rem. Floors (`max(12px, ...)`) are allowed only to keep small text legible.

**The 44px Rule.** Every link and control in a row of text gets a 44px hit area, by min-height or by negative margin plus padding, without growing the line.

## Elevation & Depth

Flat. There is no `box-shadow`, no backdrop blur, no glow and no surface fill other than the ink inversion. Depth is drawn, not cast: the terrain canvas lays a dotted perspective plane receding to the back row and shades each ridge stack from a light foot to a dark crest in its band's state hue. On the page itself, hierarchy is carried by a small line vocabulary.

### Shadow Vocabulary
None. The line vocabulary stands in for it:
- **Row rule** (1px dotted, rule): table rows, install rows, not-list rows.
- **Header rule** (1px solid, rule): under the table head.
- **Sheet rule** (1px dashed, rule): top of each sheet, 404 shell top and bottom.
- **Structural edge** (1px solid, ink): transcript and JSON left borders, runway baseline, dawn band, footer.

### Named Rules
**The Drawn Depth Rule.** If something needs to read as nearer or deeper, draw it in the terrain or rule it with a line. Never lift it with a shadow.

## Shapes

Square everywhere: radius 0 on buttons, slider thumbs and links. The recurring form is the cell. Block glyphs are masked into their own cells by two intersected gradient masks: horizontally the ink occupies 4% to 90% of each 0.5em cell, vertically 85% of each row (the row pitch is font-size times 0.84 to 0.92). The social card uses the same mask with whole-pixel stops so the raster stays crisp. The runway types each latency band as a stack of these cells, band k is k rows tall.

Controls are written in bracket notation: square brackets added by `::before` and `::after` around a lowercase word (`[copy]`, `[head-on]`, `[pause]`). Prefix marks come from the shell: `$ ` before commands, `# ` before comments and definition terms, `- ` before not-list items, all in faded ink.

## Components

### Buttons
Plain words in brackets; the press is an inversion.
- **Shape:** square (0), no border, no background at rest.
- **Copy:** lowercase `[copy]` in ink beside a command; 44px hit area via 10px block padding and negative margins. On success the label reads `Copied` with a solid underline and a polite status announcement, then resets after 1.8s. Hidden without JavaScript.
- **Toggle (head-on, pause):** dotted underline at rest, solid underline when `aria-pressed="true"`; pause relabels to `resume`. Both start `hidden` and appear only when JavaScript can honor them.
- **Hover / Focus:** ink fill, paper text; 2px blue outline offset 3px on `:focus-visible`.

### Links
- **Inline:** inherit ink, 1px dotted underline offset 0.22em, solid on hover.
- **Nav and wordmark:** no underline, 44px tall, inversion on hover.

### Inputs / Fields
- **Range slider:** transparent 44px track area; the track is a 1px dashed ink line (2px on, 4px off); the thumb is a square ink block 18px by 30px. Cursor `ew-resize`.

### Navigation
One top text line: lowercase wordmark `pinghue` left, five lowercase anchors right (map, modes, evidence, install, github), 38u apart. Stacked, it flows above the hero and the anchors scroll horizontally without a scrollbar.

### Terrain Stage (signature)
A figure holding a poster image, a canvas and the real table. On load a landscape clip is printed through the glyph ramp (equalized luminance, darkest tenths hatched), held about 2.8s, then the terrain repaints over it cell by cell from the horizon forward in 1.3s. A 1s clock advances a deterministic simulated run, repainting terrain and table together; it stops when the stage is off screen, the tab is hidden, or the visitor pauses. Head-on tilts the terrain flat in 700ms (cubic ease-out) onto the history column, then hands over to the real glyphs. Without JavaScript the poster and the static table show the final frame. The table has no `aria-live`; it repaints every second.

### The Table
pinghue's exact columns: host, state, last, avg, jitter, loss, history. Fixed layout, no wrapping, dotted row rules, state words and over-threshold values colored by the Color Is State Rule, the history column in scale glyphs with slight letter-spacing.

### Runway and Lab
The fixed scale as eight cell stacks plus loss and refused marks, each with its limit beneath. Dragging the lab slider lifts the matching step 8u (160ms) and inverts its limit, prints the glyph large, and appends it to a history trail.

### Transcript and Report
Commands and their output set with a 1px ink left edge. The JSON report prints line by line (140ms per line) the first time it scrolls into view.

### Dawn Band
A 400u band between ink rules where a sunrise clip is printed through the same ramp while in view; the closing line sits beside it.

## Do's and Don'ts

### Do:
- **Do** size every desktop measure as `calc(N * var(--u))` against the 1586-unit frame, capped at 1840px, and switch to the stacked pixel layout below 1100px.
- **Do** keep color to the state meanings: green healthy, amber slow or intermittent, red loss or down.
- **Do** write controls as lowercase words in square brackets that invert to paper on ink.
- **Do** draw new display art in block glyphs through the cell mask, generated by `scripts/site-headline.mjs`, with the real text in a visually hidden span.
- **Do** give anything that moves on a clock a pause that stops all of it, the clip included, and stop the clock off screen and in hidden tabs.
- **Do** honor reduced motion by drawing the terrain once with no clip, no clock, no printing and no transitions.
- **Do** carry the shell prefixes (`$ `, `# `, `- `) in faded ink for commands, comments and lists.

### Don't:
- **Don't** use green, amber or red on anything that is not latency data or a state word.
- **Don't** add boxes, cards, rounded corners, shadows or glows.
- **Don't** fill a surface or text with a gradient; tonal shading lives only inside state-colored terrain data, and gradients appear only as masks or dash patterns.
- **Don't** add a second typeface or use icon fonts; the only glyph marks are pinghue's own (▁▂▃▄▅▆▇█, `·`, `!`).
- **Don't** put eyebrow labels above section headings.
- **Don't** use em or en dashes in visible copy.
- **Don't** put `aria-live` on the ticking table.
- **Don't** build the dark headline-left, terminal-right dev-tool hero.
