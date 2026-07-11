#!/usr/bin/env bash
set -euo pipefail

repo="${1:-${GITHUB_REPOSITORY:-inxbit/pinghue}}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
settings_dir="$(cd -- "${script_dir}/../.github/repo-settings" && pwd)"

if [[ ! "${repo}" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'usage: %s owner/repo\n' "$0" >&2
  exit 2
fi

API_STATUS=""
API_BODY=""

read_api_response() {
  local endpoint="$1"
  local response_path
  local body_path
  local command_status
  response_path="$(mktemp "${TMPDIR:-/tmp}/pinghue-gh-api.XXXXXX")"
  body_path="${response_path}.body"

  if gh api --include "${endpoint}" >"${response_path}" 2>/dev/null; then
    command_status=0
  else
    command_status=$?
  fi

  API_STATUS="$(
    python3 - "${response_path}" "${body_path}" <<'PY'
import re
import sys
from pathlib import Path

response_path = Path(sys.argv[1])
body_path = Path(sys.argv[2])
text = response_path.read_text(encoding="utf-8", errors="replace").replace(
    "\r\n", "\n"
)
lines = text.splitlines(keepends=True)
headers = [
    (index, match.group(1))
    for index, line in enumerate(lines)
    if (match := re.match(r"^HTTP/\S+\s+([0-9]{3})\b", line))
]
if not headers:
    body_path.write_text(text, encoding="utf-8")
    print("")
    raise SystemExit

header_index, status = headers[-1]
body_index = header_index + 1
while body_index < len(lines) and lines[body_index].strip():
    body_index += 1
if body_index < len(lines):
    body_index += 1
body_path.write_text("".join(lines[body_index:]), encoding="utf-8")
print(status)
PY
  )"
  API_BODY="$(<"${body_path}")"
  rm -f "${response_path}" "${body_path}"

  if [[ -z "${API_STATUS}" && "${command_status}" -eq 0 ]]; then
    # Retain compatibility with gh-compatible test doubles that omit headers.
    API_STATUS="200"
  fi
  return "${command_status}"
}

is_github_permission_denied_response() {
  [[ "${API_STATUS}" == "403" ]] || return 1
  API_RESPONSE_BODY="${API_BODY}" python3 - <<'PY'
import json
import os

try:
    payload = json.loads(os.environ["API_RESPONSE_BODY"])
except json.JSONDecodeError:
    raise SystemExit(1)
if not isinstance(payload, dict):
    raise SystemExit(1)
raise SystemExit(
    0
    if payload.get("message") == "Resource not accessible by integration"
    else 1
)
PY
}

require_ruleset() {
  local name="$1"
  local target="$2"
  local expected_path="$3"

  local ruleset_ids
  local ruleset_id
  local duplicate_id
  ruleset_ids="$(
    gh api --paginate --slurp "repos/${repo}/rulesets" \
      --jq ".[][] | select(.name == \"${name}\" and .target == \"${target}\" and .enforcement == \"active\") | .id"
  )"
  ruleset_id="$(printf '%s\n' "${ruleset_ids}" | sed -n '1p')"
  duplicate_id="$(printf '%s\n' "${ruleset_ids}" | sed -n '2p')"

  if [[ -n "${duplicate_id}" ]]; then
    printf 'multiple active %s rulesets named %s\n' "${target}" "${name}" >&2
    exit 1
  fi

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


def status_check_map(ruleset: dict) -> dict[str, int | None]:
    checks = (
        rule_map(ruleset)
        .get("required_status_checks", {})
        .get("parameters", {})
        .get("required_status_checks", [])
    )
    return {
        check.get("context", ""): check.get("integration_id")
        for check in checks
    }


for field in ("name", "target", "enforcement"):
    if hosted.get(field) != expected.get(field):
        fail(f"{field} drifted from checked-in policy")

hosted_conditions = hosted.get("conditions", {}).get("ref_name", {})
expected_conditions = expected.get("conditions", {}).get("ref_name", {})
for field in ("include", "exclude"):
    if hosted_conditions.get(field, []) != expected_conditions.get(field, []):
        fail(f"ref_name.{field} drifted from checked-in policy")

expected_bypass_actors = expected.get("bypass_actors", [])
if "bypass_actors" not in hosted:
    if os.environ.get("PINGHUE_ALLOW_HIDDEN_BYPASS_ACTORS") == "1":
        print(
            f"warning: {target} ruleset {name}: bypass_actors is hidden from this token",
            file=sys.stderr,
        )
    else:
        fail("bypass_actors is unavailable; use repository-administration read access")
elif hosted.get("bypass_actors", []) != expected_bypass_actors:
    fail("bypass_actors drifted from checked-in policy")

hosted_rules = rule_map(hosted)
expected_rules = rule_map(expected)
hosted_rule_types = sorted(rule.get("type", "") for rule in hosted.get("rules", []))
expected_rule_types = sorted(rule.get("type", "") for rule in expected.get("rules", []))
if hosted_rule_types != expected_rule_types:
    fail("rule types drifted from checked-in policy")
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
        "require_code_owner_review",
        "require_last_push_approval",
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
    expected_checks = status_check_map(expected)
    hosted_checks = status_check_map(hosted)
    for context, integration_id in expected_checks.items():
        if context not in hosted_checks:
            fail(f"missing required status check: {context}")
        if hosted_checks[context] != integration_id:
            fail(f"required status check source drifted: {context}")

    extra_checks = sorted(set(hosted_checks) - set(expected_checks))
    if extra_checks:
        fail("unexpected required status checks: " + ", ".join(extra_checks))

    expected_check_list = (
        expected_rules["required_status_checks"]
        .get("parameters", {})
        .get("required_status_checks", [])
    )
    hosted_check_list = (
        hosted_rules["required_status_checks"]
        .get("parameters", {})
        .get("required_status_checks", [])
    )
    if len(hosted_check_list) != len(expected_check_list):
        fail("required status check list drifted from checked-in policy")

    expected_status_parameters = expected_rules["required_status_checks"].get(
        "parameters", {}
    )
    hosted_status_parameters = hosted_rules["required_status_checks"].get(
        "parameters", {}
    )
    for key in (
        "do_not_enforce_on_create",
        "strict_required_status_checks_policy",
    ):
        if hosted_status_parameters.get(key) != expected_status_parameters.get(key):
            fail(f"required_status_checks.{key} drifted from checked-in policy")
PY
}

require_pypi_environment() {
  local hosted_environment
  hosted_environment="$(gh api "repos/${repo}/environments/pypi")"
  HOSTED_ENVIRONMENT="${hosted_environment}" \
  EXPECTED_ENVIRONMENT_PATH="${settings_dir}/pypi-environment.json" \
  python3 - <<'PY'
import json
import os
import sys

hosted = json.loads(os.environ["HOSTED_ENVIRONMENT"])
with open(os.environ["EXPECTED_ENVIRONMENT_PATH"], "r", encoding="utf-8") as handle:
    expected = json.load(handle)


def fail(message: str) -> None:
    print(f"pypi environment {message}", file=sys.stderr)
    raise SystemExit(1)


for field in ("can_admins_bypass", "deployment_branch_policy"):
    if field not in hosted:
        fail(f"field is unavailable: {field}")
    if hosted[field] != expected[field]:
        if field == "can_admins_bypass" and hosted[field] is True:
            fail("administrator bypass is enabled")
        fail(f"drifted from checked-in policy: {field}")

wait_rules = [
    rule
    for rule in hosted.get("protection_rules", [])
    if rule.get("type") == "wait_timer"
]
if len(wait_rules) > 1:
    fail("contains multiple wait-timer protection rules")
hosted_wait_timer = wait_rules[0].get("wait_timer") if wait_rules else 0
if hosted_wait_timer != expected["wait_timer"]:
    fail("drifted from checked-in policy: wait_timer")

reviewer_rules = [
    rule
    for rule in hosted.get("protection_rules", [])
    if rule.get("type") == "required_reviewers"
]
if len(reviewer_rules) != 1:
    fail("must contain exactly one required-reviewers protection rule")
rule = reviewer_rules[0]
if rule.get("prevent_self_review") != expected["prevent_self_review"]:
    fail("drifted from checked-in policy: prevent_self_review")

hosted_reviewers = []
for entry in rule.get("reviewers", []):
    reviewer = entry.get("reviewer") or {}
    reviewer_id = reviewer.get("id", entry.get("id"))
    reviewer_type = entry.get("type")
    if reviewer_type not in {"User", "Team"} or not isinstance(reviewer_id, int):
        fail("returned an invalid reviewer")
    hosted_reviewers.append({"type": reviewer_type, "id": reviewer_id})

sort_key = lambda reviewer: (reviewer["type"], reviewer["id"])
if sorted(hosted_reviewers, key=sort_key) != sorted(
    expected["reviewers"], key=sort_key
):
    fail("reviewer identities drifted from checked-in policy")
PY

  local hosted_policies
  hosted_policies="$(
    gh api --paginate --slurp \
      "repos/${repo}/environments/pypi/deployment-branch-policies"
  )"
  HOSTED_POLICIES="${hosted_policies}" python3 - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["HOSTED_POLICIES"])
pages = payload if isinstance(payload, list) else [payload]
policies = []
for page in pages:
    if isinstance(page, dict):
        policies.extend(page.get("branch_policies", []))

normalized = [
    {"name": policy.get("name"), "type": policy.get("type")}
    for policy in policies
]
expected = [{"name": "v*.*.*", "type": "tag"}]
if normalized != expected:
    print(
        "pypi environment deployment policies drifted; "
        "expected the sole v*.*.* tag policy",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

require_json_settings() {
  local label="$1"
  local endpoint="$2"
  local expected_path="$3"
  local expected_section="${4:-}"
  local allow_hidden="${5:-0}"
  local hosted

  if ! read_api_response "${endpoint}"; then
    if [[ "${allow_hidden}" == "1" \
      && "${PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS:-0}" == "1" ]] \
      && is_github_permission_denied_response; then
      printf 'warning: %s is hidden from the scheduled token (HTTP 403)\n' \
        "${label}" >&2
      return
    fi
    if [[ -n "${API_STATUS}" ]]; then
      printf '%s is unavailable (HTTP %s)\n' "${label}" "${API_STATUS}" >&2
    else
      printf '%s is unavailable (no HTTP response)\n' "${label}" >&2
    fi
    exit 1
  fi
  hosted="${API_BODY}"

  SETTINGS_LABEL="${label}" \
  HOSTED_SETTINGS="${hosted}" \
  EXPECTED_SETTINGS_PATH="${expected_path}" \
  EXPECTED_SETTINGS_SECTION="${expected_section}" \
  python3 - <<'PY'
import json
import os
import sys

label = os.environ["SETTINGS_LABEL"]
try:
    hosted = json.loads(os.environ["HOSTED_SETTINGS"])
except json.JSONDecodeError:
    print(f"{label} returned invalid JSON", file=sys.stderr)
    raise SystemExit(1)
with open(os.environ["EXPECTED_SETTINGS_PATH"], "r", encoding="utf-8") as handle:
    expected = json.load(handle)
section = os.environ["EXPECTED_SETTINGS_SECTION"]
if section:
    expected = expected[section]

missing = [key for key in expected if key not in hosted]
for key, value in expected.items():
    if key in hosted and hosted.get(key) != value:
        print(f"{label} drifted from checked-in policy: {key}", file=sys.stderr)
        raise SystemExit(1)

if missing:
    print(
        f"{label} fields are unavailable: " + ", ".join(sorted(missing)),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY
}

require_vulnerability_alerts() {
  local expected
  expected="$(
    python3 -c 'import json, sys; print(str(json.load(open(sys.argv[1], encoding="utf-8"))["vulnerability_alerts"]["enabled"]).lower())' \
      "${settings_dir}/security-features.json"
  )"
  if [[ "${expected}" != "true" ]]; then
    printf 'checked-in vulnerability alerts policy must remain enabled\n' >&2
    exit 1
  fi
  if read_api_response "repos/${repo}/vulnerability-alerts"; then
    return
  fi
  if [[ "${PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS:-0}" == "1" ]] \
    && is_github_permission_denied_response; then
    printf 'warning: vulnerability alerts are hidden from the scheduled token (HTTP 403)\n' >&2
    return
  fi
  if [[ "${API_STATUS}" == "404" ]]; then
    printf 'vulnerability alerts are disabled (HTTP 404)\n' >&2
  elif [[ -n "${API_STATUS}" ]]; then
    printf 'vulnerability alerts are unavailable (HTTP %s)\n' "${API_STATUS}" >&2
  else
    printf 'vulnerability alerts are unavailable (no HTTP response)\n' >&2
  fi
  exit 1
}

require_repository_settings() {
  require_json_settings \
    "default branch" \
    "repos/${repo}" \
    "${settings_dir}/repository.json"
  require_json_settings \
    "Actions repository permissions" \
    "repos/${repo}/actions/permissions" \
    "${settings_dir}/actions-permissions.json" \
    "" \
    "1"
  require_json_settings \
    "Actions workflow permissions" \
    "repos/${repo}/actions/permissions/workflow" \
    "${settings_dir}/actions-workflow-permissions.json" \
    "" \
    "1"
  require_json_settings \
    "private vulnerability reporting" \
    "repos/${repo}/private-vulnerability-reporting" \
    "${settings_dir}/security-features.json" \
    "private_vulnerability_reporting" \
    "1"
  require_vulnerability_alerts
  require_json_settings \
    "automated security fixes" \
    "repos/${repo}/automated-security-fixes" \
    "${settings_dir}/security-features.json" \
    "automated_security_fixes" \
    "1"
}

require_pages_site() {
  local expected_domain
  expected_domain="$(tr -d '[:space:]' < "${script_dir}/../docs/CNAME")"

  local pages_state
  pages_state="$(
    gh api "repos/${repo}/pages" \
      --jq '[.build_type, .cname // "", (.https_enforced | tostring), .https_certificate.state // ""] | join("|")'
  )"

  local expected_state="workflow|${expected_domain}|true|approved"
  if [[ "${pages_state}" != "${expected_state}" ]]; then
    printf 'pages site drifted (build_type|cname|https_enforced|certificate): got "%s", expected "%s"\n' \
      "${pages_state}" "${expected_state}" >&2
    exit 1
  fi
}

require_repository_settings
require_ruleset "protect main" "branch" "${settings_dir}/main-ruleset.json"
require_ruleset "protect release tags" "tag" "${settings_dir}/release-tag-ruleset.json"
require_pypi_environment
require_pages_site

printf 'repository hardening checks passed for %s\n' "${repo}"
