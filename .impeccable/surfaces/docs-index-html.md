---
version: 1
slug: "docs-index-html"
primary_target: "docs/index.html"
related_targets: ["docs/404.html"]
---

# pinghue.com landing page

Scope: `docs/index.html` (plus 404), the only web surface. Mode: Persuade.
Audience: network operators, SREs, sysadmins in 02:00 change windows. Job: understand pinghue in seconds and install it. Action: copy `uv tool install pinghue`. Proof: the real TUI (columns, states, glyph scale, keys), the JSON report, the "what it is not" list.
Constraints: static `docs/`, no build step, strict CSP, deploy-gating `tests/site_pages.test.mjs`, user pins: dark, start over from round 1, not like the old Slate + Signal site, keep the hue wordmark, Linear-level craft.
Unresolved: hero video treatment (subtle background vs real footage big), decided live at the hero checkpoint.

## Direction contract

THESIS: Category standard at Linear's craft: the real pinghue TUI is the hero. Refuses concept costumes, scroll-story chapters, neon.

OWN-WORLD: Near-black #08090a, white #f7f8f8, gray #8a8f98, hairlines #23252a; one clean grotesk, mono only in commands and the terminal; green #7ee787, amber #f2cc60, red #ff7b72 only in the TUI and the hue wordmark; dim rack-light footage behind.

STORY: Visitor sees the real table, gets "every host, one table, one fixed scale, JSON evidence", copies the install.

FIRST VIEWPORT: Nav: hue wordmark left; Modes, Scale, Evidence, Install, GitHub right. Centered headline "Every host. One live table.", one line of copy, install chip (primary) beside View on GitHub. Below, a TUI window about 80% wide filling the lower 60%. Footage behind.

FORM: canon, user-chosen; seed 6aee1fdc; approved comp `.impeccable/mocks/comp-c.png`.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
