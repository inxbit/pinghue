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

The generator verifies the local `pinghue --version` value against
`pyproject.toml` and embeds that version in the generated GIF/PNG metadata.

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
python -m pip install --require-hashes -r requirements-build.txt
python -m pip install --require-hashes -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=80
ruff check .
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

See `docs/release-checklist.md`.
