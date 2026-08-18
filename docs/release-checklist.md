# Release Checklist

This checklist is for maintainers publishing `pinghue`.

## One-Time Repository Setup

- Create `inxbit/pinghue`.
- Enable private vulnerability reporting.
- Enable Dependabot alerts and security updates.
- Run `scripts/apply-github-hardening.sh inxbit/pinghue` after the first `main`
  push, then run `scripts/check-github-hardening.sh inxbit/pinghue` with
  repository-administration read access and require a clean result.
- In GitHub's `pypi` environment settings, disable administrator bypass for
  protection rules. GitHub exposes the value to read-only verification, but
  its supported REST and GraphQL update inputs do not accept it, so the helper
  cannot apply it.
- Configure PyPI trusted publishing:
  - owner: `inxbit`
  - repository: `pinghue`
  - workflow: `publish.yml`
  - environment: `pypi`

## Pre-Release Verification

```sh
python -m pip install --require-hashes -r requirements-build.txt
python -m pip install --require-hashes -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=85
ruff check .
mypy src
pip-audit --strict --disable-pip -r requirements.txt
pip-audit --strict --disable-pip -r requirements-build.txt
pip-audit --strict --disable-pip -r requirements-audit.txt
rm -rf build dist src/*.egg-info
SOURCE_DATE_EPOCH=0 python -m build --no-isolation
SOURCE_DATE_EPOCH=0 python scripts/normalize-sdist.py dist/*.tar.gz
twine check dist/*
pinghue --version
pinghue -p 1 127.0.0.1 -c 1 --no-tui
scripts/check-github-hardening.sh inxbit/pinghue
```

Regenerate the universal development lock after an intentional dependency
change, review the full diff, and repeat the hash-enforced installs above:

```sh
uv pip compile pyproject.toml --extra dev --universal --generate-hashes \
  --output-file requirements.txt
```

## Hosted Hardening Re-Verification

Re-verify hosted hardening before every release, even when no repository settings were intentionally changed:

- `scripts/check-github-hardening.sh inxbit/pinghue` passes.
- GitHub rulesets for `main` and `v*.*.*` release tags are active.
- The `main` ruleset still requires pull requests, status checks, signed commits, and review-thread resolution.
- The required status checks still include dependency audits under Python 3.10
  and 3.13 so both sides of supported dependency markers are evaluated.
- The `main` ruleset still allows squash and rebase merges only.
- The `pypi` environment still requires manual approval and is limited to `v*.*.*` tag deployments.
- The `pypi` environment's required reviewer type and numeric GitHub ID still
  match `.github/repo-settings/pypi-environment.json` exactly.
- The `pypi` environment still disallows administrator bypass; verify this in
  the GitHub settings UI. The drift check verifies the read response, but the
  supported update APIs cannot apply the setting.
- PyPI trusted publishing still points at owner `inxbit`, repository `pinghue`, workflow `publish.yml`, and environment `pypi`.
- The publish workflow still uses GitHub artifact attestations for `dist/*`.
- The required `Reproducible distributions` check still creates two independent
  pristine source trees, changes the second tree's mtimes, normalizes both
  sdists, and compares wheel/sdist bytes.
- The GitHub Pages custom domain still points at `pinghue.com`, the certificate is approved with HTTPS enforced (`scripts/check-github-hardening.sh` verifies this), and the DNS records at the provider still match the GitHub Pages A/AAAA/CNAME set.

Sigstore wheel signing is intentionally deferred for this single-maintainer project. Current releases rely on signed Git tags, PyPI trusted publishing, and GitHub artifact attestations for provenance. Revisit Sigstore wheel signing before adding additional maintainers or changing the release trust model.

## Version Update

- Update `pyproject.toml`.
- Update `CHANGELOG.md`.
- Update all version surfaces: README, `SECURITY.md`, the example/site JSON,
  hero/favicons, and any current-version release text.
- Run `scripts/gen-readme-assets.sh` so the GIF/PNG version metadata and real
  TUI captures match the new version.
- Run the version-surface and asset-metadata tests in
  `tests/test_repository_hardening.py`.
- Build and normalize the release sdist, then update
  `packaging/homebrew/pinghue.rb` with the version, the temporary pre-publication source URL
  `https://files.pythonhosted.org/packages/source/p/pinghue/pinghue-X.Y.Z.tar.gz`,
  and the reviewed SHA256. The publish workflow blocks before PyPI if that
  version or hash differs from the exact built sdist. The short
  `/packages/source/` URL is only a staging value while the release does not yet
  exist on PyPI; it is not acceptable in the final repository or tap formula.

## Release PR

```sh
git switch -c release/X.Y.Z
git status --short
git commit -S -m "release: prepare vX.Y.Z"
git push -u origin release/X.Y.Z
```

Open a release PR and wait for its exact-head required checks. Merge the release PR
through the protected `main` branch. Then wait for the exact merged `main` SHA's `CI`
workflow to pass. Do not tag the release branch.

## Tag and Publish

After GitHub shows the release PR as merged, fetch the public state and create
the signed annotated tag from the merged `origin/main` commit:

```sh
git fetch --prune origin
git switch main
git merge --ff-only origin/main
merged_main_sha="$(git rev-parse origin/main)"
git tag -s vX.Y.Z -m "Release vX.Y.Z" "${merged_main_sha}"
git tag -v vX.Y.Z
git push origin vX.Y.Z
```

The tag push triggers `.github/workflows/publish.yml`, which independently
requires a verified annotated tag whose commit is reachable from `origin/main`,
reruns Ruff, Mypy, and the coverage-gated test suite against that exact commit,
audits all three hash locks under Python 3.10 and 3.13, and revalidates the tag
identity immediately before PyPI publication and again before creating the
GitHub release. It also installs and runs both exact distributions and verifies
the staged Homebrew formula SHA before publication.

## After PyPI Publish

- Verify the PyPI page renders correctly.
- Verify `uv tool install pinghue` works.
- Verify `pipx install pinghue` works.
- Verify GitHub artifact attestations for the published wheel and sdist.
- Read `https://pypi.org/pypi/pinghue/X.Y.Z/json`, locate the sole sdist entry
  named `pinghue-X.Y.Z.tar.gz`, and replace the formula's top-level source URL
  with that exact published sdist URL. It must use HTTPS on
  `files.pythonhosted.org` and must not contain `/packages/source/`.
- Download that exact URL and verify its exact published SHA256 matches the
  reviewed `packaging/homebrew/pinghue.rb` value. Stop the release if it differs.
- Run `brew update-python-resources packaging/homebrew/pinghue.rb` now that the
  PyPI release metadata is available. Confirm the command changed only resource
  stanzas and preserved the exact top-level URL and reviewed SHA256, then merge
  that formula refresh into `inxbit/pinghue`.
- Copy the exact reviewed `packaging/homebrew/pinghue.rb` into
  `inxbit/homebrew-tap/Formula/pinghue.rb` on a tap release branch. In the tap,
  run:

```sh
brew audit --strict --online Formula/pinghue.rb
brew fetch --formula Formula/pinghue.rb
brew test Formula/pinghue.rb
brew install --formula Formula/pinghue.rb
pinghue --version
```

- Commit the tap formula, push the branch, merge its PR after tap CI passes,
  and fetch/install the published `inxbit/tap/pinghue` formula once more to
  verify the public tap serves the released version.
