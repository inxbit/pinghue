# PRODUCT.md - pinghue

## Register

Brand. The only web surface is the landing page at `docs/` (pinghue.com); its job is design-as-communication, not app UI.

## What it is

`pinghue` is a colored, concurrent ICMP/TCP ping monitor CLI/TUI for maintenance windows. Local tool, no server, no daemon. Distributed via PyPI (`uv tool install pinghue`) and Homebrew (`inxbit/tap`). The site's single job: get an operator to install it in under a minute of reading.

## Target users

Network operators, SREs, and sysadmins running maintenance windows, migrations, and reachability checks, often at 02:00 under change-freeze pressure. Terminal-native audience; they trust tools that state their scope honestly.

## Brand personality

Dense, vigilant, calm at 2am. Honest about what it is not (the README has a "What This Is Not" section; the site keeps it). The name is the thesis: ping + hue, color IS state.

## Brand tokens (committed, do not reinvent)

- Palette is the product's documented "Slate + Signal" palette (see README): bg `#101418`, panel `#151b22`, header `#1b2630`, border `#2a313a`, text `#e6edf3`, muted `#8ea0b8`, and four semantic signals: green `#7ee787` (healthy), amber `#f2cc60` (slow/intermittent), red `#ff7b72` (loss/down), blue `#58a6ff` (focus).
- Rule: signal colors appear only with state meaning, never as decoration.
- Wordmark: "ping" in text color + "hue" in the green-amber-red-blue gradient, with a gradient rule (from `docs/assets/pinghue-hero.svg`). The wordmark gradient is committed identity.
- Type: Archivo (variable, width axis, self-hosted) + JetBrains Mono (variable, self-hosted). Mono is earned: the product is literally a terminal.
- Signature element: live scripted terminal simulation in the hero (`docs/script.js`), using the product's real fixed latency glyph scale.

## Anti-references

No SaaS gradient-blob heroes, no fake dashboards, no marketing buzzwords, no em dashes, no eyebrow-label-on-every-section scaffolding. The site must read like it was made by the people who made the tool.

## Constraints

- Static site, no build step: plain HTML/CSS/JS in `docs/`, deployed by GitHub Pages workflow.
- `tests/site_pages.test.mjs` is a deploy-gating contract on file structure, key copy, palette, and the no-em-dash rule. Update it deliberately alongside content changes.
- Site changes deploy only via PR to `main` (branch protected).
