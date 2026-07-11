#!/usr/bin/env bash
set -euo pipefail

repo="${1:-inxbit/pinghue}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd -- "${script_dir}/.." && pwd)"
settings_dir="${root}/.github/repo-settings"

if [[ ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'usage: %s owner/repo\n' "$0" >&2
  exit 2
fi

validate_security_feature_policy() {
  python3 - "${settings_dir}/security-features.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    policy = json.load(handle)

expected = {
    "private_vulnerability_reporting": {"enabled": True},
    "vulnerability_alerts": {"enabled": True},
    "automated_security_fixes": {"enabled": True, "paused": False},
}
if policy != expected:
    raise SystemExit(
        "security-features.json must keep private reporting, vulnerability "
        "alerts, and automated security fixes enabled and unpaused"
    )
PY
}

validate_pypi_environment_policy() {
  python3 - "${settings_dir}/pypi-environment.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    policy = json.load(handle)

expected = {
    "wait_timer": 0,
    "prevent_self_review": False,
    "can_admins_bypass": False,
    "reviewers": [{"type": "User", "id": 18606875}],
    "deployment_branch_policy": {
        "protected_branches": False,
        "custom_branch_policies": True,
    },
}
if policy != expected:
    raise SystemExit(
        "pypi-environment.json must keep exact tag-only deployment policy, "
        "required-reviewer settings, and administrator bypass disabled"
    )
PY
}

ensure_default_branch() {
  local expected_branch
  expected_branch="$(
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["default_branch"])' \
      "${settings_dir}/repository.json"
  )"

  local hosted_branch
  hosted_branch="$(gh api "repos/${repo}" --jq '.default_branch')"
  if [[ "${hosted_branch}" == "${expected_branch}" ]]; then
    return
  fi

  if ! gh api "repos/${repo}/git/ref/heads/${expected_branch}" >/dev/null; then
    printf 'cannot set default branch: refs/heads/%s does not exist\n' "${expected_branch}" >&2
    exit 1
  fi
  gh api -X PATCH "repos/${repo}" -f "default_branch=${expected_branch}" >/dev/null
  printf 'configured default branch: %s\n' "${expected_branch}"
}

upsert_ruleset() {
  local name="$1"
  local target="$2"
  local payload="$3"
  local ids
  local id
  local duplicate_id

  ids="$(
    gh api --paginate --slurp "repos/${repo}/rulesets" \
      --jq ".[][] | select(.name == \"${name}\" and .target == \"${target}\") | .id"
  )"
  id="$(printf '%s\n' "${ids}" | sed -n '1p')"
  duplicate_id="$(printf '%s\n' "${ids}" | sed -n '2p')"
  if [[ -n "${duplicate_id}" ]]; then
    printf 'multiple %s rulesets named %s; refusing ambiguous update\n' "${target}" "${name}" >&2
    exit 1
  fi

  if [[ -n "${id}" ]]; then
    gh api -X PUT "repos/${repo}/rulesets/${id}" --input "${payload}" >/dev/null
    printf 'updated %s ruleset: %s\n' "${target}" "${name}"
  else
    gh api -X POST "repos/${repo}/rulesets" --input "${payload}" >/dev/null
    printf 'created %s ruleset: %s\n' "${target}" "${name}"
  fi
}

prepare_environment_payload() {
  local output_path="$1"
  local environment_inventory
  local environment_exists

  environment_inventory="$(
    gh api --paginate --slurp "repos/${repo}/environments"
  )"
  environment_exists="$(
    HOSTED_ENVIRONMENTS="${environment_inventory}" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["HOSTED_ENVIRONMENTS"])
pages = payload if isinstance(payload, list) else [payload]
names = {
    environment.get("name", "").casefold()
    for page in pages
    if isinstance(page, dict)
    for environment in page.get("environments", [])
    if isinstance(environment.get("name"), str)
}
print("true" if "pypi" in names else "false")
PY
  )"

  if [[ "${environment_exists}" == "true" ]]; then
    gh api "repos/${repo}/environments/pypi" >/dev/null
  fi

  BASELINE_PATH="${settings_dir}/pypi-environment.json" \
  python3 - "${output_path}" <<'PY'
import json
import os
import sys

with open(os.environ["BASELINE_PATH"], "r", encoding="utf-8") as handle:
    payload = json.load(handle)

if payload.get("can_admins_bypass") is not False:
    raise SystemExit("pypi environment baseline must disable administrator bypass")
# GitHub exposes this value when reading an environment, but its supported
# REST and GraphQL update inputs do not accept it. Keep it in desired state for
# drift verification and require maintainers to apply it in the settings UI.
payload.pop("can_admins_bypass")
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

reconcile_pypi_deployment_policy() {
  local policies
  local plan_path="$1"
  local desired_id
  local action
  local policy_id

  policies="$(
    gh api --paginate --slurp \
      "repos/${repo}/environments/pypi/deployment-branch-policies"
  )"
  if [[ -z "${policies}" ]]; then
    policies='[]'
  fi

  HOSTED_POLICIES="${policies}" python3 - "${plan_path}" <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["HOSTED_POLICIES"])
pages = payload if isinstance(payload, list) else [payload]
policies = []
for page in pages:
    if isinstance(page, dict):
        policies.extend(page.get("branch_policies", []))

desired = [
    policy
    for policy in policies
    if policy.get("name") == "v*.*.*" and policy.get("type") == "tag"
]
retained_id = desired[0].get("id") if desired else None
if retained_id is not None and not isinstance(retained_id, int):
    raise SystemExit("pypi deployment policy returned a non-integer ID")

with open(sys.argv[1], "w", encoding="utf-8") as handle:
    if retained_id is not None:
        handle.write(f"retain {retained_id}\n")
    for policy in policies:
        policy_id = policy.get("id")
        if not isinstance(policy_id, int):
            raise SystemExit("pypi deployment policy returned a missing or non-integer ID")
        if policy_id != retained_id:
            handle.write(f"delete {policy_id}\n")
PY

  desired_id="$(awk '$1 == "retain" {print $2}' "${plan_path}")"
  if [[ -z "${desired_id}" ]]; then
    gh api -X POST "repos/${repo}/environments/pypi/deployment-branch-policies" \
      -f name='v*.*.*' \
      -f type='tag' >/dev/null
    printf 'created pypi deployment tag policy: v*.*.*\n'
  else
    printf 'pypi deployment tag policy already exists: v*.*.*\n'
  fi

  # Establish the desired allow policy before removing any existing policy. A
  # failed create therefore leaves the environment's previous boundary intact.
  while read -r action policy_id; do
    if [[ "${action}" != "delete" ]]; then
      continue
    fi
    gh api -X DELETE \
      "repos/${repo}/environments/pypi/deployment-branch-policies/${policy_id}" \
      >/dev/null
    printf 'removed unexpected pypi deployment policy: %s\n' "${policy_id}"
  done < "${plan_path}"
}

tmp_environment="$(mktemp)"
tmp_policy_plan="$(mktemp)"
trap 'rm -f "${tmp_environment}" "${tmp_policy_plan}"' EXIT

validate_security_feature_policy
validate_pypi_environment_policy
ensure_default_branch
upsert_ruleset "protect main" "branch" "${settings_dir}/main-ruleset.json"
upsert_ruleset "protect release tags" "tag" "${settings_dir}/release-tag-ruleset.json"

prepare_environment_payload "${tmp_environment}"
gh api -X PUT "repos/${repo}/environments/pypi" \
  --input "${tmp_environment}" >/dev/null
printf 'configured environment: pypi\n'
reconcile_pypi_deployment_policy "${tmp_policy_plan}"

gh api -X PUT "repos/${repo}/actions/permissions" \
  --input "${settings_dir}/actions-permissions.json" >/dev/null
printf 'configured Actions repository permissions\n'
gh api -X PUT "repos/${repo}/actions/permissions/workflow" \
  --input "${settings_dir}/actions-workflow-permissions.json" >/dev/null
printf 'configured Actions workflow permissions\n'

gh api -X PUT "repos/${repo}/private-vulnerability-reporting" >/dev/null
printf 'enabled private vulnerability reporting\n'
gh api -X PUT "repos/${repo}/vulnerability-alerts" >/dev/null
printf 'enabled vulnerability alerts\n'
gh api -X PUT "repos/${repo}/automated-security-fixes" >/dev/null
printf 'enabled automated security fixes\n'
