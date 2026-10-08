# Contributing

## Development Setup

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
mypy src
```

## README Artwork

The animated demo and dense screenshot in the README are real captures of the
TUI. Regenerate them with [`vhs`](https://github.com/charmbracelet/vhs) and
`ffmpeg` installed and `pinghue` on `PATH`:

```sh
brew install vhs ffmpeg
scripts/gen-readme-assets.sh
```

The generator verifies the local `pinghue --version` value against
`pyproject.toml` and embeds that version in the generated GIF/PNG metadata.

The README banner is the site's social card (`docs/assets/pinghue-social-card.png`,
see Website below); `node scripts/site-version.mjs` keeps its version current.

## Website

`docs/` is the published site (pinghue.com): GitHub Pages behind Cloudflare
(proxied DNS). Every push to `main` that touches `docs/` deploys, gated by
`tests/site_pages.test.mjs`. Do not keep working notes in `docs/`.

What the page is made of (redesign of 2026-10, recorded in `DESIGN.md`):

- `index.html`, `site.css`, `site.js`, `404.html`. No build step. The strict
  CSP in both pages is pinned by the contract test: `default-src 'none'`,
  everything `'self'`, `media-src 'self'` for the hero footage, nothing inline.
- The hero is the real TUI layout (columns, keys, colors, glyph scale, state
  rules) running a deterministic simulated run from `site.js`. The no-JS rows
  in `index.html` are that run at 02:31:04: after changing the run, regenerate
  them with `node scripts/site-rows.mjs` (the test fails until you do).
- Fonts, self-hosted from Google Fonts' latin files, unmodified:
  `fonts/zalando-sans-latin.woff2` (Zalando Sans, SIL OFL 1.1, reserved font
  name "Zalando", so never subset or modify it under that name) and
  `fonts/ubuntu-sans-mono-latin.woff2` (Ubuntu Sans Mono, Ubuntu Font Licence 1.0).
- `assets/pinghue-wordmark.svg` is the hue wordmark outlined to paths, so it
  needs no web font: `scripts/site-wordmark.py` regenerates it (usage in the file).
- `media/hero.mp4` (1280x720, 4 s seamless loop, no audio) and its first frame
  `assets/hero-poster.webp` (2508x1412). The clip comes from Higgsfield
  `bytedance/seedance-2.5/text-to-video` (5 s, 720p, 16:9) with this prompt,
  then an ffmpeg crossfade loop and a slight darken:
  "Cinematic slow dolly forward down a narrow data center cold aisle at 2 a.m.:
  tall black server racks on both sides with tiny green status LEDs blinking,
  one amber LED blinking on the right, cool dim overhead light falling off into
  darkness at the end of the aisle, perforated floor tiles, shallow depth of
  field, realistic 35mm film look, moody and quiet. No text, no signs, no
  labels, no people, no screens, no neon glow."
  It loads only when the hero is on screen, never under reduced motion unless
  the visitor presses play, and the pause button stops it with the live table.
- `assets/pinghue-social-card.png` is rendered from `scripts/site-social-card.html`
  by `scripts/gen-site-social-card.sh` (headless Chrome).
- The site shows the released version in four places (the hero and 404 TUI
  titles, the JSON report, the social card). At release time,
  `node scripts/site-version.mjs` copies the `pyproject.toml` version into all
  of them and re-renders the card; the site test fails while any is stale.
- Every shipped raster carries its origin: a PNG `tEXt` chunk, or a `.json`
  sidecar next to a WebP.

`scripts/gen-readme-assets.sh` writes the README screenshot and
`docs/assets/pinghue-screenshot.webp` (needs `cwebp`: `brew install webp`).
The README links those files, so they stay in `docs/assets/` although the
site itself no longer shows them.

Cloudflare settings that live outside the repo (zone `pinghue.com`):

- Web Analytics (RUM) auto-injection is **off**. The site CSP is
  `script-src 'self'`, so an injected beacon is blocked and only produces
  console errors. Keep it off, or change the CSP deliberately (contract test,
  `404.html`, and the threat model all pin it).
- Cache rules: `/fonts/*` browser TTL 1 year, `/assets/*` 7 days, everything
  else 4 hours (zone default). **Fonts are cached for a year by filename: if a
  font file changes, rename it** and update `site.css`, the `index.html`
  preloads, and `scripts/site-social-card.html`. Files under `/assets/` may be
  stale for up to 7 days after a release, so a changed social card or favicon
  can take that long to reach returning visitors.
- `site.css` and `site.js` stay at 4 hours on purpose. Their names are fixed
  (the contract test pins `site.js`), so a longer TTL would strand visitors on
  old code after a deploy.

## Commit and PR Policy

- Work on branches named with conventional prefixes such as `feat/`, `fix/`, `docs/`, `ci/`, or `release/`.
- Submit changes through pull requests.
- Keep commits signed and verified.
- Keep changes focused; avoid unrelated refactors.
- Include tests for behavior changes.
- Update README, schema examples, or release docs when user-facing behavior changes.

Useful local defaults:

```sh
git config commit.gpgsign true
git config tag.gpgsign true
git config gpg.format ssh
```

## Required Checks Before Merge

```sh
python -m pip install --require-hashes -r requirements-build.txt
python -m pip install --require-hashes -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=85
ruff check .
ruff format --check .
mypy src
pip-audit --strict --disable-pip -r requirements.txt
pip-audit --strict --disable-pip -r requirements-build.txt
pip-audit --strict --disable-pip -r requirements-audit.txt
rm -rf build dist src/*.egg-info
SOURCE_DATE_EPOCH=0 python -m build --no-isolation
SOURCE_DATE_EPOCH=0 python scripts/normalize-sdist.py dist/*.tar.gz
twine check dist/*
```

## Release Policy

Releases are tag-driven. Release policy requires:

- a signed release tag
- GitHub Actions CI passing for the exact merged `main` commit before tagging
- release-workflow validation rerun against the exact tagged commit
- PyPI trusted publishing configured for `inxbit/pinghue`
- a protected `pypi` GitHub environment

See `.github/release-checklist.md`.
