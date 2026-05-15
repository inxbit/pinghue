import json
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_manifest_excludes_developer_only_workflows_and_scripts() -> None:
    manifest = read("MANIFEST.in")

    assert "prune .github" in manifest
    assert "prune packaging" in manifest
    assert "prune scripts" in manifest
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


def test_main_ruleset_requires_review() -> None:
    ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    pull_request = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")

    assert pull_request["parameters"]["required_approving_review_count"] == 1


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
