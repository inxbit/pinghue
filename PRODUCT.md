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

Decided with the owner on 2026-10-06 after two rejected directions (the dark Slate + Signal scroll story, then a light glyph-terrain world). Details and the live system are in `DESIGN.md`.

- Direction: the dark developer-tool page done at full craft, with linear.app as the quality bar. Dark is a standing preference; no light world.
- Page palette: near-black `#08090a`, white `#f7f8f8`, gray `#8a8f98`, hairlines `#23252a`. The page itself stays neutral so the terminal is the only thing in color.
- Product palette, inside terminal windows only, exactly as `src/pinghue/ui.py` and the Textual CSS paint it: green `#7ee787` (healthy, ok), amber `#f2cc60` (slow, intermittent, refused), red `#ff7b72` (loss, down), text `#e6edf3`, muted `#8ea0b8`, cursor `#58a6ff` at 22%. Signal colors appear only with state meaning, never as decoration.
- Wordmark: "ping" in white + "hue" in the green-amber-red-blue gradient (kept at the owner's request), shipped as the outlined `docs/assets/pinghue-wordmark.svg`. The favicon keeps the four hue bars.
- Type: Zalando Sans for display and UI, Ubuntu Sans Mono for the terminal and commands. Mono is for code and data only.
- Signature element: the hero is the real TUI layout (columns, keys, states, fixed glyph scale) running a deterministic simulated run whose keys work; behind it, Higgsfield footage of a data-center aisle at night.

## Anti-references

No SaaS gradient-blob heroes, no fake dashboards, no marketing buzzwords, no em dashes, no eyebrow-label-on-every-section scaffolding. The site must read like it was made by the people who made the tool. Also rejected by the owner: neon-on-slate signal styling with scroll-story chapters (the 2026-07 site), and an all-monospace light "glyph terrain" world (2026-10 round 1).

## Constraints

- Static site, no build step: plain HTML/CSS/JS in `docs/`, deployed by GitHub Pages workflow. Generators in `scripts/` (`site-rows.mjs`, `site-wordmark.py`, `gen-site-social-card.sh`) only rewrite committed files.
- `tests/site_pages.test.mjs` is a deploy-gating contract on file structure, key copy, palette, and the no-em-dash rule. Update it deliberately alongside content changes.
- Site changes deploy only via PR to `main` (branch protected).
