# Release Checklist

This checklist is for maintainers publishing `pinghue`.

## One-Time Repository Setup

- Create `inxbit/pinghue`.
- Enable private vulnerability reporting.
- Enable Dependabot alerts and security updates.
- Add rulesets from `.github/repo-settings/`.
- Create the `pypi` environment and require manual approval.
- Configure PyPI trusted publishing:
  - owner: `inxbit`
  - repository: `pinghue`
  - workflow: `publish.yml`
  - environment: `pypi`

## Pre-Release Verification

```sh
pytest
ruff check .
mypy src
python -m build
twine check dist/*
pinghue --version
pinghue -p 1 127.0.0.1 -c 1 --no-tui
```

## Version Update

- Update `pyproject.toml`.
- Update `src/pinghue/__init__.py`.
- Update `CHANGELOG.md`.
- Update README version references if needed.

## Release

```sh
git status --short
git commit -S -m "release: prepare v0.1.0"
git tag -s v0.1.0 -m "Release v0.1.0"
git push origin main
git push origin v0.1.0
```

The tag push triggers `.github/workflows/publish.yml`.

## After PyPI Publish

- Verify the PyPI page renders correctly.
- Verify `uv tool install pinghue` works.
- Verify `pipx install pinghue` works.
- Update `packaging/homebrew/pinghue.rb` with the PyPI sdist SHA256.
- In `inxbit/homebrew-tap`, run:

```sh
brew update-python-resources Formula/pinghue.rb
brew audit --strict Formula/pinghue.rb
brew test Formula/pinghue.rb
```
