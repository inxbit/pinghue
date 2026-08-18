# Repository Hardening

This repository is intended to be operated with strict release controls.

## Required Repository Settings

- Default branch: `main`
- Private vulnerability reporting: enabled
- Dependabot alerts: enabled
- Dependabot security updates: enabled
- Actions permissions: enabled with full-SHA pinning required
- Default workflow token permissions: read-only, without pull-request approval
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
- review thread resolution
- CI status checks

For a solo-maintainer repository, the checked-in template intentionally requires
pull requests but sets required approving reviews to `0`. GitHub does not allow
authors to approve their own pull requests, so a one-review requirement blocks
all solo-maintainer releases. If the repository gains another maintainer with
write access, raise `required_approving_review_count` in
`.github/repo-settings/main-ruleset.json`.

## Release Tag Ruleset

Apply `.github/repo-settings/release-tag-ruleset.json`.

The ruleset protects `v*.*.*` tags from update and deletion, and requires signed commits for matching refs.

## PyPI Environment

The helper script uses `.github/repo-settings/pypi-environment.json` as the
baseline and limits the environment to the sole `v*.*.*` tag deployment
policy. Every apply reconciles the environment to the exact reviewer type and
numeric GitHub ID in that baseline, replacing any hosted reviewer drift. To
change the required reviewer, update and review the tracked baseline first.

Disable **Allow administrators to bypass configured protection rules** in the
hosted `pypi` environment. GitHub returns `can_admins_bypass` when reading the
environment, so the drift check verifies it. GitHub's supported REST and
GraphQL update inputs do not accept that field, so the apply helper removes it
from the update payload and maintainers must change it in the settings UI.

## Actions and Security Features

The tracked Actions baselines require enabled repository Actions, full-SHA
action pinning, a read-only default workflow token, and no workflow permission
to approve pull requests. The helper also enables private vulnerability
reporting, vulnerability alerts, and automated security fixes. The default
branch baseline is `.github/repo-settings/repository.json`; the apply helper
confirms `refs/heads/main` exists before changing the hosted default branch.

## Commands

```sh
scripts/apply-github-hardening.sh inxbit/pinghue
```

Check the hosted settings for drift:

```sh
scripts/check-github-hardening.sh inxbit/pinghue
```

The drift check covers the default branch, Actions permissions, security
features, the branch and tag rulesets (including required-check source bindings
and bypass actors), the `pypi` environment (including exact reviewer type/ID),
and the GitHub Pages site (workflow build type, custom domain matching
`docs/CNAME`, HTTPS enforcement, and certificate state).

GitHub hides `bypass_actors` from the read-only token used by hosted workflow
runs. The workflow sets `PINGHUE_ALLOW_HIDDEN_BYPASS_ACTORS=1` for that
reduced-visibility check and emits a warning. Local manual and pre-release
checks fail closed when `bypass_actors` is hidden, so run them with repository
administration read access.

Some administration-only Actions, repository, and security settings can also
be hidden from the hosted workflow token. Hosted runs set
`PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS=1`. The check tolerates only an HTTP 403
from an administration-only endpoint whose JSON body exactly reports
`Resource not accessible by integration`. Rate-limit, abuse-limit, malformed,
HTTP 404, HTTP 5xx, network, default-branch, and missing-field responses still
fail, as does visible drift. Local manual and pre-release checks fail closed
unless every tracked setting is readable and matches its baseline.

The `pypi` environment itself is always checked fail-closed. Its administrator
bypass field, reviewer identities, self-review policy, timer, and deployment
policy must all be present and match the checked-in baseline; reduced-visibility
mode does not waive any of those publish-boundary controls.

`.github/workflows/repository-hardening.yml` runs the same drift check on a
weekly schedule and whenever repository-hardening files change.

## Local Maintainer Defaults

```sh
git config commit.gpgsign true
git config tag.gpgsign true
git config gpg.format ssh
```

The local repository should never contain PyPI tokens or GitHub tokens. Publishing uses GitHub OIDC and PyPI trusted publishing.
