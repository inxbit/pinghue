# PRODUCT.md - pinghue

<!-- impeccable:product-schema 1 -->

## Platform

web

## What it is

`pinghue` is a colored, concurrent ICMP/TCP ping monitor CLI/TUI for maintenance windows. Local tool, no server, no daemon. Distributed via PyPI (`uv tool install pinghue`) and Homebrew (`inxbit/tap`). The site's single job: get an operator to install it in under a minute of reading.

## Target users

Network operators, SREs, and sysadmins running maintenance windows, migrations, and reachability checks, often at 02:00 under change-freeze pressure. Terminal-native audience; they trust tools that state their scope honestly.

## Positioning

Judges a maintenance window as a whole and closes it with evidence. Every host sits in one live table on a fixed latency scale that never rescales, so rows compare across hosts and across runs. Any loss, or jitter above `--jitter-threshold`, latches a host as `intermittent` until it is reset, so the record covers the whole run, not only the final moments. With `--output`, the run ends in a `schema_version: 1` JSON report whose stats cover every probe, and `--fail-on-any-down` or `--fail-on-all-down` turn down hosts into exit code `3`. A local, unprivileged tool: no server, no daemon, no credentials.

No source claims an advantage over another tool: other tools appear only in the README's What This Is Not list and in one compatibility note (Ctrl-C matches classic `ping`). Add no comparisons or speed claims without evidence.

## Operating Context

- Where it runs: a terminal on macOS or Linux with Unicode glyphs, Python 3.10 to 3.14.
- Getting it: `uv tool install pinghue` or `pipx install pinghue`, `python -m pip install pinghue` inside a virtual environment (`python -m pinghue` also runs it), or `brew install inxbit/tap/pinghue`.
- Before relying on ICMP: `pinghue --check`. On Linux, unprivileged ICMP may need `net.ipv4.ping_group_range` opened to the user's group; TCP mode (`-p PORT`) needs no privileges.
- During the window: targets as arguments or in a host file (`-f`); the TUI to watch, with `r`/`R` to reset and `b`/`B` to probe at once; `--no-tui` for scripts, cron, CI and smoke tests, with `--count` or `--duration`, `--output`, and `--fail-on-any-down` or `--fail-on-all-down` for exit code `3`.
- After the window: the operator attaches the report to the change ticket or change record as post-maintenance evidence; whoever reviews the change may read it. Accept the report only after pinghue exits successfully and the JSON parses, then rotate it as a separate step. `--host-label` names the system or window; otherwise `run.host` is `local`.
- Sensitive inputs: host lists and reports can carry private hostnames and addresses. `SECURITY.md` and the bug template ask reporters to keep customer hostnames, private IP inventories and production maintenance data out of public issues.
- Support: GitHub issues through bug and feature templates; security reports privately through GitHub Security Advisories; security fixes cover only the latest stable line.
- Not on record: typical host counts per window, and where pinghue runs (laptop, jump host, CI runner).

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

## Capabilities and Constraints

Product facts below summarize `README.md` and `src/pinghue`, which stay authoritative; the site contract test reads the source directly.

- Probes: ICMP echo by default over unprivileged datagram sockets (never requires root; `--check` warns when run as root) or TCP connect checks with `-p PORT`. Every target is probed concurrently (`--concurrency`, default 64, at most 1024), one probe in flight per target, every 1 s by default, up to 5,000 targets per run.
- Views: the TUI, one row per host with the columns host, address (hidden until `a`), state, last, min, avg, max, jitter, loss, mode and history under a `PingHUE v<version>` title; or `--no-tui`, one line per probe.
- States, judged over the whole run: `healthy`; `intermittent` (any loss, or jitter above `--jitter-threshold`, default 50 ms, latched until reset); `down` (`--fail-threshold` consecutive failures, default 3, or no reply at all); plus `resolving`, `dns_failure` and `permission_denied` (shown as `dns` and `denied` in the TUI) and `error`.
- Latency scale: one glyph per probe on fixed bands (up to 1, 3, 10, 30, 100, 300 and 1000 ms, then above) that never rescale; latency above 100 ms reads as slow. `·` marks a failed probe and `!` a TCP refusal.
- Report: `--output PATH` writes one `schema_version: 1` JSON run summary at exit. `stats` cover every probe and `samples` keep the recent tail (at most 1,000 per target). Files are `0600` and never replaced without `--overwrite`.
- Exit codes: `0` completed, `1` runtime error or `--check` not ready, `2` usage error, `3` a `--fail-on-any-down` or `--fail-on-all-down` condition.
- Runtime: macOS and Linux on Python 3.10 to 3.14; Windows is outside the contract. Python 3.10 support ends in the next major release after its October 2026 end of life. Configuration is flags only: no config file, and pinghue reads no environment variables of its own (the Textual TUI honors `NO_COLOR`).
- Network: no traffic beyond the operator's probes and OS DNS lookups. The code holds no telemetry, analytics or update check; that is a code fact, not a published promise.
- Terminology: `target` in the CLI and JSON, `host` in the TUI and prose. Name casing in use: `pinghue` for the command, package and prose, `PingHUE v<version>` in the TUI title, `PingHue` in the threat model and security report; no canonical prose casing is decided.
- Identity: author and copyright holder `inxbit` (person or organization is not on record); MIT license.

Site:

- Static site, no build step: plain HTML/CSS/JS in `docs/`, deployed by GitHub Pages workflow. Generators in `scripts/` (`site-rows.mjs`, `site-version.mjs`, `site-wordmark.py`, `gen-site-social-card.sh`) only rewrite committed files.
- `tests/site_pages.test.mjs` is a deploy-gating contract on file structure, key copy, palette, and the no-em-dash rule. Update it deliberately alongside content changes.
- Site changes deploy only via PR to `main` (branch protected).
- Both pages carry the same strict CSP (`default-src 'none'`; scripts, styles, images, fonts and media `'self'`; `base-uri` and `form-action` `'none'`; nothing inline), pinned by the contract test: no third-party scripts, fonts, embeds or analytics, and no forms.
- The contract test also reads product truth from the source (columns, keys, colors, glyph scale, thresholds, version, Python range, install commands, the README not-list), so the site cannot deploy once it drifts from the product.

## Evidence on Hand

Real output:

- `docs/assets/pinghue-demo.gif` and `docs/assets/pinghue-screenshot.png` (plus `.webp`): real captures of the released TUI, made by `scripts/gen-readme-assets.sh` against staged targets (public hosts, a refused localhost port, TEST-NET-1 addresses for the down rows), not a real maintenance window. The README shows them; the site does not.
- `schemas/output-v1.schema.json` and `examples/pinghue-output-example.json`, plus the README's host-file and `--no-tui` examples.
- Anything the shipped code prints can be reproduced on demand: the `--check` report, `--no-tui` lines, the JSON run summary.
- Release record: `CHANGELOG.md` (every release since 0.1.0 on 2026-05-14), signed release tags, GitHub artifact attestations for the wheel and sdist, a required CI check that two clean builds are byte-identical, and hash-pinned CI and release dependencies.
- Reviews: `AUDIT-2.0.1.md`, `REVIEW.md` and `security-best-practices-report.md` (dated test, coverage and scan results; measure again before any number appears publicly). None is an independent third-party audit.
- Brand assets: `docs/assets/pinghue-wordmark.svg` (rebuilt by `scripts/site-wordmark.py`), `docs/assets/pinghue-favicon.svg`, `docs/assets/pinghue-social-card.png` (rendered by `scripts/gen-site-social-card.sh`; also the README banner).

Simulated or generated (label it wherever it appears):

- The simulated run in `docs/site.js` (no-JS rows from `scripts/site-rows.mjs`) uses made-up hosts and is captioned as simulated; the site's `maintenance.json` sample and the social card show the same run.
- `docs/media/hero.mp4` and `docs/assets/hero-poster.webp` are AI-generated text-to-video footage of a generic data-center aisle (origin in `docs/assets/hero-poster.webp.json`), not a real facility.
- Every shipped PNG carries an origin text chunk and every WebP a `.json` sidecar; the demo GIF carries only a version comment.

Absent; do not fabricate:

- Testimonials, quotes, reviews, named users or customers, and customer or partner logos. The only marks are the GitHub link icon and the README's shields.io badges.
- Download, install, star or usage counts. None are on hand and nothing collects them: the CLI sends nothing home and the site loads no analytics.
- Benchmarks or performance data. 1,024 concurrent probes and 5,000 targets are limits, not measurements.
- Feature or performance comparisons with other tools.
- A capture, report or log from a real production maintenance window.
- Rights or license terms for the generated hero footage: do not call it licensed, stock or real footage.
- Pricing, paid tiers, a hosted version, sponsorship or commercial support.
- An independent security audit, certification, SBOM, SLSA level, or package signatures beyond the attestations (Sigstore signing is deferred).
- A team, company or support staff: the release checklist calls pinghue a single-maintainer project. No support SLA or security response time.

## Product Principles

- **Small on purpose, honest about scope.** Say plainly what pinghue is and is not, keep the What This Is Not list wherever the product is described, and add features in their smallest useful version.
- **The whole window counts.** States and stats cover every probe in the run, so a report reflects everything that happened.
- **Interfaces are contracts.** Flags, behavior, exit codes and the JSON schema change only through a deprecation window, a major release or a new `schema_version`.
- **Local, unprivileged, safe by default.** No server, daemon, listener or credentials; never require root; reports private and never overwritten by default; inputs bounded.
- **Real, never faked.** Show the real product and its real output, label anything simulated or generated, and never invent proof.

## Accessibility & Inclusion

Site rules the deploy-gating contract test enforces:

- Everything that moves can be paused (WCAG 2.2.2).
- Reduced motion is honored: nothing ticks and no footage loads unless the visitor presses play.
- Content that repaints every second never sits in an `aria-live` region.
- Every page has a skip link, and decorative media is hidden from assistive technology.

Product (current behavior, not a declared requirement): the TUI names each state in words and marks failed and refused probes with their own glyphs (`·`, `!`), so state never rests on color alone. `--history-style dots` or `none` and `--no-tui` give simpler or plain-text output, and the Textual TUI honors `NO_COLOR`.

Not declared: a conformance target such as a WCAG level.
