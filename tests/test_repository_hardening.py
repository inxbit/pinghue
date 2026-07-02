import json
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_manifest_excludes_developer_only_workflows_and_scripts() -> None:
    manifest = read("MANIFEST.in")

    assert "prune .github" in manifest
    assert "prune packaging" in manifest
    assert "include scripts/pinghue" in manifest
    assert "recursive-include docs *.md *.svg *.gif *.png" in manifest
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


def test_dependency_audit_pins_bootstrap_tooling() -> None:
    # L15: pip/setuptools/wheel must not be installed unpinned; the audit
    # toolchain installs from the hash-pinned lock file.
    workflow = read(".github/workflows/dependency-audit.yml")

    assert "--upgrade pip setuptools wheel" not in workflow
    assert "--require-hashes -r requirements-audit.txt" in workflow


def test_requirements_audit_is_hash_pinned() -> None:
    # The audit toolchain is pinned with sha256 hashes for --require-hashes.
    requirements = read("requirements-audit.txt")

    assert "pip-audit==" in requirements
    assert "setuptools==82.0.1" in requirements
    assert "wheel==0.47.0" in requirements
    assert "--hash=sha256:" in requirements


def test_requirements_build_is_hash_pinned() -> None:
    # L14: the build backend is pinned with sha256 hashes for --require-hashes.
    requirements = read("requirements-build.txt")

    assert "build==1.5.0" in requirements
    assert "setuptools==82.0.1" in requirements
    assert "wheel==0.47.0" in requirements
    assert "--hash=sha256:" in requirements


def test_publish_workflow_verifies_tag_and_hash_pins_build() -> None:
    workflow = read(".github/workflows/publish.yml")

    # L13: the build must fail when the tag disagrees with the package version.
    assert "Verify tag matches package version" in workflow
    assert 'GITHUB_REF_NAME#v' in workflow
    # L14: the artifact-producing toolchain is installed from the hash-pinned lock.
    assert "pip install --require-hashes -r requirements-build.txt" in workflow
    assert 'SOURCE_DATE_EPOCH: "0"' in workflow
    # I3: GitHub release is created with the built-in gh CLI, not a third-party action.
    assert "gh release create" in workflow
    assert "softprops/action-gh-release" not in workflow


def test_publish_workflow_keeps_package_check_out_of_build_job() -> None:
    workflow = read(".github/workflows/publish.yml")

    build_job, check_and_publish = workflow.split("  check:", 1)

    assert "twine==6.2.0" not in build_job
    assert "twine check dist/*" not in build_job
    assert "python -m build --no-isolation" in build_job
    assert "twine==6.2.0" in check_and_publish
    assert "twine check dist/*" in check_and_publish
    assert "needs:\n      - build\n      - check" in check_and_publish


def test_ci_build_matches_publish_build_path() -> None:
    # L16: CI exercises the same build invocation as publish.
    ci = read(".github/workflows/ci.yml")

    assert "pip install --require-hashes -r requirements-build.txt" in ci
    assert "python -m build --no-isolation" in ci
    assert 'SOURCE_DATE_EPOCH: "0"' in ci


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


def test_readme_artwork_uses_packaged_real_assets() -> None:
    readme = read("README.md")
    manifest = read("MANIFEST.in")

    assert "docs/assets/pinghue-demo.gif" in readme
    assert "docs/assets/pinghue-screenshot.png" in readme
    assert Path("docs/assets/pinghue-demo.gif").is_file()
    assert Path("docs/assets/pinghue-screenshot.png").is_file()
    assert "docs/assets/pinghue-demo.svg" not in readme
    assert "docs/assets/pinghue-screenshot.svg" not in readme
    assert "scripts/gen-readme-assets.py" not in readme
    assert "recursive-include docs *.md *.svg *.gif *.png" in manifest


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


def test_repository_hardening_drift_check_validates_ruleset_internals() -> None:
    script = read("scripts/check-github-hardening.sh")

    assert "main-ruleset.json" in script
    assert "release-tag-ruleset.json" in script
    assert "required_review_thread_resolution" in script
    assert "required_status_checks" in script
    assert "required_signatures" in script
    assert "update_allows_fetch_and_merge" in script


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
    formula = read("packaging/homebrew/pinghue.rb")

    # The demo (GIF) and screenshot (PNG) are real TUI captures, so the version
    # they show is rendered from the package itself and needs no text surface here.
    assert 'version = "3.0.0"' in pyproject
    assert "Current version: `3.0.0`." in readme
    assert example["pinghue_version"] == "3.0.0"
    assert "v3.0.0" in hero
    assert "pinghue-3.0.0.tar.gz" in formula
    assert "pinghue-2.1.0.tar.gz" not in formula


def test_security_policy_matches_stable_support_line() -> None:
    security = read("SECURITY.md")

    assert "`3.x` | Yes" in security
    assert "`2.x` | Yes" not in security
    assert "`1.x` | Yes" not in security
