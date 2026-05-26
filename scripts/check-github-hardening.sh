#!/usr/bin/env bash
set -euo pipefail

repo="${1:-${GITHUB_REPOSITORY:-inxbit/pinghue}}"

if [[ ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'usage: %s owner/repo\n' "$0" >&2
  exit 2
fi

require_ruleset() {
  local name="$1"
  local target="$2"

  local result
  result="$(
    gh api "repos/${repo}/rulesets" \
      --jq ".[] | select(.name == \"${name}\" and .target == \"${target}\" and .enforcement == \"active\") | .name" \
      | head -n 1
  )"

  if [[ "${result}" != "${name}" ]]; then
    printf 'missing active %s ruleset: %s\n' "${target}" "${name}" >&2
    exit 1
  fi
}

require_pypi_environment() {
  local environment_ok
  environment_ok="$(
    gh api "repos/${repo}/environments/pypi" \
      --jq '.deployment_branch_policy.custom_branch_policies == true and .deployment_branch_policy.protected_branches == false'
  )"

  if [[ "${environment_ok}" != "true" ]]; then
    printf 'pypi environment does not require custom deployment branch policies\n' >&2
    exit 1
  fi

  local reviewers_ok
  reviewers_ok="$(
    gh api "repos/${repo}/environments/pypi" \
      --jq 'any(.protection_rules[]?; .type == "required_reviewers" and ((.reviewers // []) | length > 0))'
  )"

  if [[ "${reviewers_ok}" != "true" ]]; then
    printf 'pypi environment has no required reviewers\n' >&2
    exit 1
  fi

  local tag_policy
  tag_policy="$(
    gh api "repos/${repo}/environments/pypi/deployment-branch-policies" \
      --jq '(.branch_policies // [])[] | select(.name == "v*.*.*" and .type == "tag") | .name' \
      | head -n 1
  )"

  if [[ "${tag_policy}" != "v*.*.*" ]]; then
    printf 'pypi environment is missing v*.*.* tag deployment policy\n' >&2
    exit 1
  fi
}

require_ruleset "protect main" "branch"
require_ruleset "protect release tags" "tag"
require_pypi_environment

printf 'repository hardening checks passed for %s\n' "${repo}"
