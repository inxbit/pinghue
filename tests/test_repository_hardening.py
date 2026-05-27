import json
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_manifest_excludes_developer_only_workflows_and_scripts() -> None:
    manifest = read("MANIFEST.in")

    assert "prune .github" in manifest
    assert "prune packaging" in manifest
    assert "include scripts/pinghue" in manifest
    assert "recursive-include packaging" not in manifest
    assert "recursive-include scripts" not in manifest
    assert "recursive-include .github/workflows" not in manifest


def test_publish_workflow_has_attestations_and_concurrency() -> None:
    workflow = read(".github/workflows/publish.yml")

    assert "concurrency:" in workflow
    assert "attestations: write" in workflow
    assert (
        "actions/attest-build-provenance@a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32"
        in workflow
    )
    assert "subject-path: dist/*" in workflow


def test_dependency_audit_workflow_runs_pip_audit_weekly() -> None:
    workflow = read(".github/workflows/dependency-audit.yml")

    assert "schedule:" in workflow
    assert "pip-audit" in workflow


def test_main_ruleset_requires_pull_request_flow() -> None:
    ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    pull_request = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
    parameters = pull_request["parameters"]

    assert parameters["required_approving_review_count"] == 0
    assert parameters["required_review_thread_resolution"] is True
    assert parameters["allowed_merge_methods"] == ["squash", "rebase"]


def test_ci_and_homebrew_cover_python_313() -> None:
    ci = read(".github/workflows/ci.yml")
    formula = read("packaging/homebrew/pinghue.rb")
    pyproject = read("pyproject.toml")

    assert '"3.13"' in ci
    assert 'depends_on "python@3.13"' in formula
    assert '"Programming Language :: Python :: 3.13"' in pyproject


def test_homebrew_test_uses_fail_on_down_against_local_tcp_server() -> None:
    formula = read("packaging/homebrew/pinghue.rb")

    assert "TCPServer.new" in formula
    assert "--fail-on-down" in formula


def test_pyproject_advertises_stable_release_status() -> None:
    pyproject = read("pyproject.toml")

    assert '"Development Status :: 5 - Production/Stable"' in pyproject
    assert '"Development Status :: 3 - Alpha"' not in pyproject


def test_output_schema_has_stable_v1_id_and_samples_window_contract() -> None:
    schema = json.loads(read("schemas/output-v1.schema.json"))
    run_definition = schema["$defs"]["run"]

    assert schema["$id"] == "https://raw.githubusercontent.com/inxbit/pinghue/main/schemas/output-v1.schema.json"
    assert "samples_window" in run_definition["required"]
    assert run_definition["properties"]["samples_window"]["minimum"] == 0


def test_readme_declares_stable_support_and_deprecation_policy() -> None:
    readme = read("README.md")

    assert "## Stability Policy" in readme
    assert "`schema_version: 1`" in readme
    assert "at least one minor release" in readme
    assert "## Supported Platforms" in readme
    assert "macOS and Linux" in readme


def test_release_checklist_revalidates_release_security_gates() -> None:
    checklist = read("docs/release-checklist.md")

    assert "pip-audit" in checklist
    assert "hosted hardening" in checklist
    assert "GitHub rulesets" in checklist
    assert "`pypi` environment" in checklist
    assert "Sigstore wheel signing" in checklist


def test_repository_hardening_drift_check_is_scheduled() -> None:
    workflow = read(".github/workflows/repository-hardening.yml")
    script = read("scripts/check-github-hardening.sh")

    assert "schedule:" in workflow
    assert "scripts/check-github-hardening.sh" in workflow
    assert "set -euo pipefail" in script
    assert "protect main" in script
    assert "protect release tags" in script
    assert "pypi" in script
    assert "reviewers" in script
    assert "v*.*.*" in script


def test_changelog_has_dated_1_0_release_section() -> None:
    changelog = read("CHANGELOG.md")

    assert "Starting with `1.0.0`, this project follows semantic versioning." in changelog
    assert "## [Unreleased]" in changelog
    assert "## 2.0.1 - 2026-05-27" in changelog
    assert "Sanitized environment-doctor DNS diagnostic output" in changelog
    assert "## 2.0.0 - 2026-05-26" in changelog
    assert "`--output PATH` no longer replaces an existing regular file by default" in changelog
    assert "## 1.0.0 - 2026-05-20" in changelog


def test_release_version_surfaces_match_package_version() -> None:
    pyproject = read("pyproject.toml")
    readme = read("README.md")
    example = json.loads(read("examples/pinghue-output-example.json"))
    hero = read("docs/assets/pinghue-hero.svg")
    demo = read("docs/assets/pinghue-demo.svg")
    screenshot = read("docs/assets/pinghue-screenshot.svg")

    assert 'version = "2.0.1"' in pyproject
    assert "Current version: `2.0.1`." in readme
    assert example["pinghue_version"] == "2.0.1"
    assert "v2.0.1" in hero
    assert "v2.0.1" in demo
    assert "v2.0.1" in screenshot


def test_security_policy_matches_stable_support_line() -> None:
    security = read("SECURITY.md")

    assert "`2.x` | Yes" in security
    assert "`1.x` | Yes" not in security
    assert "`0.3.x` | Yes" not in security
