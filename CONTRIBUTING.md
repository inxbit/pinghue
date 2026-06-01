# Contributing

## Development Setup

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
pytest
ruff check .
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

The hero image (`docs/assets/pinghue-hero.svg`) is hand-authored.

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
pytest
ruff check .
mypy src
python -m build
twine check dist/*
```

## Release Policy

Releases are tag-driven. The publish workflow requires:

- a signed release tag
- GitHub Actions CI passing
- PyPI trusted publishing configured for `inxbit/pinghue`
- a protected `pypi` GitHub environment

See `docs/release-checklist.md`.
