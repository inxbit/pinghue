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
pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=80
ruff check .
mypy src
pip-audit
rm -rf build dist src/*.egg-info
SOURCE_DATE_EPOCH=0 python -m build --no-isolation
twine check dist/*
pinghue --version
pinghue -p 1 127.0.0.1 -c 1 --no-tui
scripts/check-github-hardening.sh inxbit/pinghue
```

## Hosted Hardening Re-Verification

Re-verify hosted hardening before every release, even when no repository settings were intentionally changed:

- `scripts/check-github-hardening.sh inxbit/pinghue` passes.
- GitHub rulesets for `main` and `v*.*.*` release tags are active.
- The `main` ruleset still requires pull requests, status checks, signed commits, and review-thread resolution.
- The `main` ruleset still allows squash and rebase merges only.
- The `pypi` environment still requires manual approval and is limited to `v*.*.*` tag deployments.
- PyPI trusted publishing still points at owner `inxbit`, repository `pinghue`, workflow `publish.yml`, and environment `pypi`.
- The publish workflow still uses GitHub artifact attestations for `dist/*`.
- The GitHub Pages custom domain still points at `pinghue.com`, the certificate is approved with HTTPS enforced, and the DNS records at the provider still match the GitHub Pages A/AAAA/CNAME set.

Sigstore wheel signing is intentionally deferred for this single-maintainer project. Current releases rely on signed Git tags, PyPI trusted publishing, and GitHub artifact attestations for provenance. Revisit Sigstore wheel signing before adding additional maintainers or changing the release trust model.

## Version Update

- Update `pyproject.toml`.
- Update `CHANGELOG.md`.
- Update README version references if needed.

## Release

```sh
git status --short
git commit -S -m "release: prepare vX.Y.Z"
git tag -s vX.Y.Z -m "Release vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

The tag push triggers `.github/workflows/publish.yml`.

## After PyPI Publish

- Verify the PyPI page renders correctly.
- Verify `uv tool install pinghue` works.
- Verify `pipx install pinghue` works.
- Verify GitHub artifact attestations for the published wheel and sdist.
- Update `packaging/homebrew/pinghue.rb` with the PyPI sdist SHA256.
- In `inxbit/homebrew-tap`, run:

```sh
brew update-python-resources Formula/pinghue.rb
brew audit --strict Formula/pinghue.rb
brew test Formula/pinghue.rb
```
