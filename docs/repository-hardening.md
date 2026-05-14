# Repository Hardening

This repository is intended to be operated with strict release controls.

## Required Repository Settings

- Default branch: `main`
- Private vulnerability reporting: enabled
- Dependabot alerts: enabled
- Dependabot security updates: enabled
- Actions permissions: allow GitHub Actions, disallow untrusted broad write permissions
- Branch and tag rulesets: active
- `pypi` environment: protected

## Branch Ruleset

Apply `.github/repo-settings/main-ruleset.json` after the first `main` push.

The ruleset requires:

- pull requests for `main`
- signed commits
- linear history
- no force pushes
- no branch deletion
- stale review dismissal
- code owner review
- review thread resolution
- CI status checks

## Release Tag Ruleset

Apply `.github/repo-settings/release-tag-ruleset.json`.

The ruleset protects `v*.*.*` tags from update and deletion, and requires signed commits for matching refs.

## PyPI Environment

The helper script uses `.github/repo-settings/pypi-environment.json` as the documented baseline, configures the current authenticated GitHub user as the required reviewer, and limits the environment to `v*.*.*` tag deployments. If a team should review releases instead, change the environment reviewer in the GitHub web UI after running the script.

## Commands

```sh
scripts/apply-github-hardening.sh inxbit/pinghue
```

## Local Maintainer Defaults

```sh
git config commit.gpgsign true
git config tag.gpgsign true
git config gpg.format ssh
```

The local repository should never contain PyPI tokens or GitHub tokens. Publishing uses GitHub OIDC and PyPI trusted publishing.
