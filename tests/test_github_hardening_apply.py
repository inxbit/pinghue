import json
import os
import subprocess
from pathlib import Path
from typing import Any

FAKE_GH = """#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
scenario = json.loads(Path(os.environ["FAKE_GH_SCENARIO"]).read_text(encoding="utf-8"))
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")

if "--slurp" in args and "--jq" in args:
    print("the `--slurp` option is not supported with `--jq`", file=sys.stderr)
    raise SystemExit(2)

if args[:3] == ["api", "user", "--jq"]:
    print(str(scenario.get("viewer_id", 42)))
    raise SystemExit

endpoint = next((arg for arg in args[1:] if arg.startswith("repos/")), "")
method = args[args.index("-X") + 1] if "-X" in args else "GET"
query = args[args.index("--jq") + 1] if "--jq" in args else ""

if endpoint == "repos/example/project" and method == "GET":
    branch = scenario.get("default_branch", "main")
    print(branch if query else json.dumps({"default_branch": branch}))
elif endpoint == "repos/example/project/git/ref/heads/main" and method == "GET":
    if not scenario.get("main_ref_exists", True):
        raise SystemExit(1)
    print(json.dumps({"ref": "refs/heads/main"}))
elif endpoint == "repos/example/project/rulesets" and method == "GET":
    if scenario.get("wrong_target_ruleset_id") and ".target" not in query:
        print(str(scenario["wrong_target_ruleset_id"]))
elif endpoint == "repos/example/project/environments" and method == "GET":
    environment_name = scenario.get("pypi_environment_name")
    if environment_name is None and scenario.get("pypi_exists"):
        environment_name = "pypi"
    environments = [{"name": environment_name}] if environment_name else []
    print(json.dumps([{"environments": environments}]))
elif endpoint == "repos/example/project/environments/pypi" and method == "GET":
    if not scenario.get("pypi_readable", True):
        raise SystemExit(1)
    print(json.dumps(scenario.get("pypi_environment", {})))
elif endpoint == "repos/example/project/environments/pypi" and method == "PUT":
    capture_path = scenario.get("environment_capture")
    if capture_path:
        shutil.copyfile(args[args.index("--input") + 1], capture_path)
elif (
    endpoint == "repos/example/project/environments/pypi/deployment-branch-policies"
    and method == "GET"
):
    policies = scenario.get("pypi_policies", [])
    if query:
        wrong_name = next(
            (policy for policy in policies if policy.get("name") == "v*.*.*"),
            None,
        )
        if wrong_name:
            print(str(wrong_name["id"]))
    else:
        print(json.dumps({"branch_policies": policies}))
elif (
    endpoint == "repos/example/project/environments/pypi/deployment-branch-policies"
    and method == "POST"
):
    if scenario.get("pypi_policy_create_fails"):
        raise SystemExit(1)
    print(json.dumps({"id": 900, "name": "v*.*.*", "type": "tag"}))
"""


def _run_apply(
    tmp_path: Path,
    *,
    scenario: dict[str, Any] | None = None,
    cwd: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "gh-invocations.jsonl"
    scenario_path = tmp_path / "scenario.json"
    gh = fake_bin / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8")
    gh.chmod(0o755)
    scenario_path.write_text(json.dumps(scenario or {}), encoding="utf-8")
    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "FAKE_GH_LOG": str(log_path),
            "FAKE_GH_SCENARIO": str(scenario_path),
        }
    )

    result = subprocess.run(
        [
            "bash",
            str(Path.cwd() / "scripts/apply-github-hardening.sh"),
            "example/project",
        ],
        cwd=cwd or Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    invocations = [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    return result, invocations


def _called(
    invocations: list[list[str]],
    method: str,
    endpoint: str,
) -> bool:
    prefix = ["api", "-X", method]
    return any(args[:3] == prefix and endpoint in args for args in invocations)


def test_apply_hardening_resolves_baselines_from_its_script_location(
    tmp_path: Path,
) -> None:
    result, invocations = _run_apply(tmp_path, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    flattened = "\n".join(repr(args) for args in invocations)
    expected_settings = str(Path.cwd() / ".github/repo-settings")
    assert f"{expected_settings}/main-ruleset.json" in flattened
    assert f"{expected_settings}/release-tag-ruleset.json" in flattened


def test_apply_hardening_reconciles_the_tracked_reviewer_identity(
    tmp_path: Path,
) -> None:
    environment_capture = tmp_path / "environment-payload.json"
    scenario = {
        "default_branch": "develop",
        "main_ref_exists": True,
        "wrong_target_ruleset_id": 77,
        "pypi_exists": True,
        "pypi_environment": {
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {
                            "type": "Team",
                            "reviewer": {"id": 99, "slug": "release-managers"},
                        },
                        {
                            "type": "User",
                            "reviewer": {"id": 100, "login": "release-owner"},
                        },
                    ],
                }
            ]
        },
        "pypi_policies": [
            {"id": 501, "name": "v*.*.*", "type": "branch"},
            {"id": 502, "name": "*", "type": "tag"},
        ],
        "environment_capture": str(environment_capture),
    }

    result, invocations = _run_apply(tmp_path, scenario=scenario)

    assert result.returncode == 0, result.stderr
    assert not any("repos/example/project/rulesets/77" in args for args in invocations)
    assert sum(
        _called([args], "POST", "repos/example/project/rulesets")
        for args in invocations
    ) == 2

    payload = json.loads(environment_capture.read_text(encoding="utf-8"))
    assert "can_admins_bypass" not in payload
    assert payload["reviewers"] == [{"type": "User", "id": 18606875}]
    assert payload["prevent_self_review"] is False
    assert payload["deployment_branch_policy"] == {
        "protected_branches": False,
        "custom_branch_policies": True,
    }
    for policy_id in (501, 502):
        assert _called(
            invocations,
            "DELETE",
            "repos/example/project/environments/pypi/"
            f"deployment-branch-policies/{policy_id}",
        )
    assert _called(
        invocations,
        "POST",
        "repos/example/project/environments/pypi/deployment-branch-policies",
    )
    for endpoint in (
        "repos/example/project/actions/permissions",
        "repos/example/project/actions/permissions/workflow",
        "repos/example/project/private-vulnerability-reporting",
        "repos/example/project/vulnerability-alerts",
        "repos/example/project/automated-security-fixes",
    ):
        assert _called(invocations, "PUT", endpoint)

    main_ref_index = next(
        index
        for index, args in enumerate(invocations)
        if "repos/example/project/git/ref/heads/main" in args
    )
    patch_index = next(
        index
        for index, args in enumerate(invocations)
        if _called([args], "PATCH", "repos/example/project")
    )
    assert main_ref_index < patch_index


def test_apply_hardening_uses_tracked_reviewer_for_mixed_case_pypi_environment(
    tmp_path: Path,
) -> None:
    environment_capture = tmp_path / "environment-payload.json"
    scenario = {
        "pypi_environment_name": "PyPI",
        "pypi_environment": {
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {
                            "type": "Team",
                            "reviewer": {"id": 99, "slug": "release-managers"},
                        }
                    ],
                }
            ]
        },
        "environment_capture": str(environment_capture),
    }

    result, _ = _run_apply(tmp_path, scenario=scenario)

    assert result.returncode == 0, result.stderr
    payload = json.loads(environment_capture.read_text(encoding="utf-8"))
    assert payload["reviewers"] == [{"type": "User", "id": 18606875}]
    assert payload["prevent_self_review"] is False


def test_apply_hardening_does_not_patch_default_branch_without_main_ref(
    tmp_path: Path,
) -> None:
    result, invocations = _run_apply(
        tmp_path,
        scenario={"default_branch": "develop", "main_ref_exists": False},
    )

    assert result.returncode != 0
    assert not _called(invocations, "PATCH", "repos/example/project")


def test_apply_hardening_fails_closed_when_existing_environment_is_unreadable(
    tmp_path: Path,
) -> None:
    result, invocations = _run_apply(
        tmp_path,
        scenario={"pypi_exists": True, "pypi_readable": False},
    )

    assert result.returncode != 0
    assert not _called(invocations, "PUT", "repos/example/project/environments/pypi")


def test_apply_hardening_preserves_existing_policies_when_policy_creation_fails(
    tmp_path: Path,
) -> None:
    result, invocations = _run_apply(
        tmp_path,
        scenario={
            "pypi_policies": [{"id": 501, "name": "*", "type": "tag"}],
            "pypi_policy_create_fails": True,
        },
    )

    assert result.returncode != 0
    assert _called(
        invocations,
        "POST",
        "repos/example/project/environments/pypi/deployment-branch-policies",
    )
    assert not _called(
        invocations,
        "DELETE",
        "repos/example/project/environments/pypi/deployment-branch-policies/501",
    )
