#!/usr/bin/env bash
set -euo pipefail

repo="${1:-inxbit/pinghue}"
root="$(git rev-parse --show-toplevel)"

if [[ ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'usage: %s owner/repo\n' "$0" >&2
  exit 2
fi

upsert_ruleset() {
  local name="$1"
  local payload="$2"
  local id

  id="$(
    gh api "repos/${repo}/rulesets" \
      --jq ".[] | select(.name == \"${name}\") | .id" \
      | head -n 1
  )"

  if [[ -n "${id}" ]]; then
    gh api -X PUT "repos/${repo}/rulesets/${id}" --input "${payload}" >/dev/null
    printf 'updated ruleset: %s\n' "${name}"
  else
    gh api -X POST "repos/${repo}/rulesets" --input "${payload}" >/dev/null
    printf 'created ruleset: %s\n' "${name}"
  fi
}

tmp_environment="$(mktemp)"
trap 'rm -f "${tmp_environment}"' EXIT
reviewer_id="$(gh api user --jq .id)"

printf '{"wait_timer":0,"reviewers":[{"type":"User","id":%s}],"prevent_self_review":false,"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}\n' \
  "${reviewer_id}" >"${tmp_environment}"

upsert_ruleset "protect main" "${root}/.github/repo-settings/main-ruleset.json"
upsert_ruleset "protect release tags" "${root}/.github/repo-settings/release-tag-ruleset.json"

gh api -X PUT "repos/${repo}/environments/pypi" \
  --input "${tmp_environment}" >/dev/null
printf 'configured environment: pypi\n'

existing_policy="$(
  gh api "repos/${repo}/environments/pypi/deployment-branch-policies" \
    --jq '(.branch_policies // [])[] | select(.name == "v*.*.*") | .id' \
    | head -n 1
)"

if [[ -z "${existing_policy}" ]]; then
  gh api -X POST "repos/${repo}/environments/pypi/deployment-branch-policies" \
    -f name='v*.*.*' \
    -f type='tag' >/dev/null
  printf 'created pypi deployment tag policy: v*.*.*\n'
else
  printf 'pypi deployment tag policy already exists: v*.*.*\n'
fi
