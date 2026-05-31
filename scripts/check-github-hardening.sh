#!/usr/bin/env bash
set -euo pipefail

repo="${1:-${GITHUB_REPOSITORY:-inxbit/pinghue}}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
settings_dir="$(cd -- "${script_dir}/../.github/repo-settings" && pwd)"

if [[ ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'usage: %s owner/repo\n' "$0" >&2
  exit 2
fi

require_ruleset() {
  local name="$1"
  local target="$2"
  local expected_path="$3"

  local ruleset_id
  ruleset_id="$(
    gh api "repos/${repo}/rulesets" \
      --jq ".[] | select(.name == \"${name}\" and .target == \"${target}\" and .enforcement == \"active\") | .id" \
      | head -n 1
  )"

  if [[ -z "${ruleset_id}" ]]; then
    printf 'missing active %s ruleset: %s\n' "${target}" "${name}" >&2
    exit 1
  fi

  local hosted_ruleset
  hosted_ruleset="$(gh api "repos/${repo}/rulesets/${ruleset_id}")"

  RULESET_NAME="${name}" \
  RULESET_TARGET="${target}" \
  EXPECTED_RULESET_PATH="${expected_path}" \
  HOSTED_RULESET="${hosted_ruleset}" \
  python3 - <<'PY'
import json
import os
import sys

name = os.environ["RULESET_NAME"]
target = os.environ["RULESET_TARGET"]
expected_path = os.environ["EXPECTED_RULESET_PATH"]
hosted = json.loads(os.environ["HOSTED_RULESET"])
with open(expected_path, "r", encoding="utf-8") as handle:
    expected = json.load(handle)


def fail(message: str) -> None:
    print(f"{target} ruleset {name}: {message}", file=sys.stderr)
    raise SystemExit(1)


def rule_map(ruleset: dict) -> dict[str, dict]:
    return {rule["type"]: rule for rule in ruleset.get("rules", [])}


def context_set(ruleset: dict) -> set[str]:
    checks = (
        rule_map(ruleset)
        .get("required_status_checks", {})
        .get("parameters", {})
        .get("required_status_checks", [])
    )
    return {check.get("context", "") for check in checks}


for field in ("name", "target", "enforcement"):
    if hosted.get(field) != expected.get(field):
        fail(f"{field} drifted from checked-in policy")

hosted_conditions = hosted.get("conditions", {}).get("ref_name", {})
expected_conditions = expected.get("conditions", {}).get("ref_name", {})
for field in ("include", "exclude"):
    if hosted_conditions.get(field, []) != expected_conditions.get(field, []):
        fail(f"ref_name.{field} drifted from checked-in policy")

hosted_rules = rule_map(hosted)
expected_rules = rule_map(expected)
for rule_type in expected_rules:
    if rule_type not in hosted_rules:
        fail(f"missing required rule {rule_type}")

critical_rules = {
    "branch": (
        "deletion",
        "non_fast_forward",
        "required_signatures",
        "required_linear_history",
        "pull_request",
        "required_status_checks",
    ),
    "tag": (
        "deletion",
        "update",
        "non_fast_forward",
        "required_signatures",
    ),
}
for rule_type in critical_rules.get(target, ()):
    if rule_type not in hosted_rules:
        fail(f"missing critical rule {rule_type}")

critical_parameters = {
    "pull_request": (
        "allowed_merge_methods",
        "dismiss_stale_reviews_on_push",
        "required_approving_review_count",
        "required_review_thread_resolution",
    ),
    "update": ("update_allows_fetch_and_merge",),
}
for rule_type in ("pull_request", "update"):
    if rule_type not in expected_rules:
        continue
    hosted_parameters = hosted_rules[rule_type].get("parameters", {})
    expected_parameters = expected_rules[rule_type].get("parameters", {})
    for key in critical_parameters[rule_type]:
        expected_value = expected_parameters.get(key)
        hosted_value = hosted_parameters.get(key)
        if key == "update_allows_fetch_and_merge" and hosted_value is None:
            hosted_value = False
        if hosted_value != expected_value:
            fail(f"{rule_type}.{key} drifted from checked-in policy")

if "required_status_checks" in expected_rules:
    expected_contexts = context_set(expected)
    hosted_contexts = context_set(hosted)
    if not expected_contexts.issubset(hosted_contexts):
        missing = ", ".join(sorted(expected_contexts - hosted_contexts))
        fail(f"missing required status checks: {missing}")

    expected_strict = expected_rules["required_status_checks"].get("parameters", {}).get(
        "strict_required_status_checks_policy"
    )
    hosted_strict = hosted_rules["required_status_checks"].get("parameters", {}).get(
        "strict_required_status_checks_policy"
    )
    if hosted_strict != expected_strict:
        fail("required_status_checks.strict_required_status_checks_policy drifted from checked-in policy")
PY
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

require_ruleset "protect main" "branch" "${settings_dir}/main-ruleset.json"
require_ruleset "protect release tags" "tag" "${settings_dir}/release-tag-ruleset.json"
require_pypi_environment

printf 'repository hardening checks passed for %s\n' "${repo}"
