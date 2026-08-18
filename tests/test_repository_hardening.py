import json
import os
import re
import struct
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from pinghue.models import ProbeSample, SampleStatus, summarize_samples


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def package_version() -> str:
    match = re.search(r'^version = "([^"]+)"$', read("pyproject.toml"), re.MULTILINE)
    assert match is not None
    return match.group(1)


def png_text_chunks(path: str) -> dict[str, str]:
    data = Path(path).read_bytes()
    signature = b"\x89PNG\r\n\x1a\n"
    assert data.startswith(signature)
    chunks: dict[str, str] = {}
    position = len(signature)
    while position < len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        if kind == b"tEXt":
            key, value = payload.split(b"\0", 1)
            chunks[key.decode("latin-1")] = value.decode("latin-1")
        position += 12 + length
    return chunks


def gif_comments(path: str) -> list[str]:
    data = Path(path).read_bytes()
    assert data.startswith((b"GIF87a", b"GIF89a"))
    comments: list[str] = []
    position = 13
    packed = data[10]
    if packed & 0x80:
        position += 3 * (2 ** ((packed & 0x07) + 1))
    while position < len(data):
        introducer = data[position]
        if introducer == 0x3B:
            break
        if introducer == 0x21 and data[position + 1] == 0xFE:
            position += 2
            comment = bytearray()
            while data[position] != 0:
                size = data[position]
                position += 1
                comment.extend(data[position : position + size])
                position += size
            comments.append(comment.decode("latin-1"))
            position += 1
            continue
        if introducer == 0x21:
            position += 2
            while data[position] != 0:
                position += 1 + data[position]
            position += 1
            continue
        if introducer == 0x2C:
            position += 10
            image_packed = data[position - 1]
            if image_packed & 0x80:
                position += 3 * (2 ** ((image_packed & 0x07) + 1))
            position += 1
            while data[position] != 0:
                position += 1 + data[position]
            position += 1
            continue
        raise AssertionError(f"unexpected GIF block 0x{introducer:02x} at byte {position}")
    return comments


def test_manifest_excludes_developer_only_workflows_and_scripts() -> None:
    manifest = read("MANIFEST.in")

    assert "prune .github" in manifest
    assert "prune packaging" in manifest
    assert "include scripts/pinghue" in manifest
    assert "recursive-include docs *.md *.svg *.gif *.png" in manifest
    assert "recursive-include packaging" not in manifest
    assert "recursive-include scripts" not in manifest
    assert "recursive-include .github/workflows" not in manifest
    assert "exclude tests/test_repository_hardening.py" in manifest
    assert "exclude tests/test_github_hardening_apply.py" in manifest
    assert "exclude tests/test_normalize_sdist.py" in manifest
    assert "exclude CONTRIBUTING.md" in manifest
    assert "exclude docs/release-checklist.md" in manifest
    assert "exclude docs/repository-hardening.md" in manifest


def test_publish_workflow_has_attestations_and_concurrency() -> None:
    workflow = read(".github/workflows/publish.yml")

    assert "concurrency:" in workflow
    assert "attestations: write" in workflow
    assert (
        "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8"
        in workflow
    )
    assert "subject-path: dist/*" in workflow

    publish_job = workflow.split("  publish:", 1)[1].split("  release:", 1)[0]
    assert "contents: read" in publish_job
    assert "artifact-metadata: write" not in publish_job


def test_every_workflow_job_has_a_timeout() -> None:
    for workflow_path in sorted(Path(".github/workflows").glob("*.yml")):
        workflow = workflow_path.read_text(encoding="utf-8")
        job_count = len(re.findall(r"^    runs-on: ", workflow, re.MULTILINE))
        timeout_count = len(re.findall(r"^    timeout-minutes: \d+$", workflow, re.MULTILINE))

        assert job_count > 0, workflow_path
        assert timeout_count == job_count, workflow_path


def test_ci_cancels_superseded_pull_request_runs_but_not_main_pushes() -> None:
    workflow = read(".github/workflows/ci.yml")

    assert "concurrency:" in workflow
    assert "group: ci-${{ github.ref }}" in workflow
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in workflow


def test_coverage_gate_is_consistent_across_ci_and_docs() -> None:
    gate = "pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=85"

    for path in (
        ".github/workflows/ci.yml",
        ".github/workflows/publish.yml",
        "CONTRIBUTING.md",
        "docs/release-checklist.md",
    ):
        assert gate in read(path), path


def test_dependency_audit_workflow_runs_pip_audit_weekly() -> None:
    workflow = read(".github/workflows/dependency-audit.yml")

    assert "schedule:" in workflow
    assert "pip-audit" in workflow
    assert 'python-version: ["3.10", "3.13"]' in workflow
    assert "python-version: ${{ matrix.python-version }}" in workflow


def test_dependency_audits_are_required_for_every_pull_request() -> None:
    workflow = read(".github/workflows/dependency-audit.yml")
    ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    status_rule = next(
        rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks"
    )
    contexts = {
        check["context"]
        for check in status_rule["parameters"]["required_status_checks"]
    }

    pull_request_trigger = workflow.split("pull_request:", 1)[1].split(
        "permissions:", 1
    )[0]
    assert "paths:" not in pull_request_trigger
    assert "name: Dependency audit / Python ${{ matrix.python-version }}" in workflow
    assert {
        "Dependency audit / Python 3.10",
        "Dependency audit / Python 3.13",
    } <= contexts


def test_dependency_audit_tracks_and_audits_exact_hash_locks() -> None:
    workflow = read(".github/workflows/dependency-audit.yml")

    for lock in (
        "requirements.txt",
        "requirements-build.txt",
        "requirements-audit.txt",
    ):
        assert f"pip-audit --strict --disable-pip -r {lock}" in workflow
    assert "pip-audit --skip-editable ." not in workflow


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
    assert "setuptools==84.0.0" in requirements
    assert "wheel==0.48.0" in requirements
    assert "--hash=sha256:" in requirements


def test_requirements_audit_covers_supported_python_floor() -> None:
    requirements = read("requirements-audit.txt")

    assert "--python-version 3.10" in requirements
    assert "typing-extensions==" in requirements


def test_development_requirements_cover_supported_python_floor() -> None:
    requirements = read("requirements.txt")

    assert "--python-version 3.10" in requirements
    assert re.search(
        r"^backports-tarfile==\S+ ; .*python_full_version < '3\.12'",
        requirements,
        re.MULTILINE,
    )
    assert re.search(
        r"^rpds-py==\S+ ; python_full_version < '3\.11' \\$",
        requirements,
        re.MULTILINE,
    )
    assert re.search(
        r"^rpds-py==\S+ ; python_full_version >= '3\.11' \\$",
        requirements,
        re.MULTILINE,
    )


def test_requirements_build_is_hash_pinned() -> None:
    # L14: the build backend is pinned with sha256 hashes for --require-hashes.
    requirements = read("requirements-build.txt")

    assert "build==1.5.0" in requirements
    assert "setuptools==84.0.0" in requirements
    assert "wheel==0.48.0" in requirements
    assert "--hash=sha256:" in requirements


def test_build_backend_metadata_support_and_lock_stay_aligned() -> None:
    pyproject = read("pyproject.toml")
    requirements = read("requirements-build.txt")

    assert 'requires = ["setuptools==84.0.0", "wheel==0.48.0"]' in pyproject
    assert "setuptools==84.0.0" in requirements
    assert "wheel==0.48.0" in requirements


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


def test_publish_workflow_verifies_annotated_tag_identity_and_main_ancestry() -> None:
    workflow = read(".github/workflows/publish.yml")

    assert "fetch-depth: 0" in workflow
    assert "Verify annotated release tag" in workflow
    assert "verification.verified" in workflow
    assert ".tag" in workflow
    assert "tag_name" in workflow
    assert "object.type" in workflow
    assert "object.sha" in workflow
    assert "GITHUB_SHA" in workflow
    assert "origin/main" in workflow
    assert "merge-base --is-ancestor" in workflow


def test_workflow_actions_are_pinned_to_full_commit_shas() -> None:
    # Repo policy is full-SHA pinning; assert it across every workflow rather
    # than hardcoding one SHA per structural test.
    for path in sorted(Path(".github/workflows").glob("*.yml")):
        for used in re.findall(r"uses: (\S+)", path.read_text(encoding="utf-8")):
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", used), f"{path}: {used}"


def test_publish_workflow_revalidates_the_exact_tagged_commit() -> None:
    workflow = read(".github/workflows/publish.yml")

    validate_job = workflow.split("  validate:", 1)[1].split("  publish:", 1)[0]
    publish_job = workflow.split("  publish:", 1)[1].split("  release:", 1)[0]

    assert "needs: build" in validate_job
    assert re.search(r"actions/checkout@[0-9a-f]{40}", validate_job) is not None
    assert "persist-credentials: false" in validate_job
    assert "python -m pip install --require-hashes -r requirements-build.txt" in validate_job
    assert "python -m pip install --require-hashes -r requirements.txt" in validate_job
    assert "python -m pip install --no-deps --no-build-isolation -e ." in validate_job
    assert "ruff check ." in validate_job
    assert "mypy src" in validate_job
    assert "pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=85" in validate_job
    assert "- validate" in publish_job


def test_publish_workflow_rechecks_tag_and_audits_locks_before_pypi() -> None:
    workflow = read(".github/workflows/publish.yml")
    audit_job = workflow.split("  audit:", 1)[1].split("  publish:", 1)[0]
    publish_job = workflow.split("  publish:", 1)[1].split("  release:", 1)[0]

    assert 'python-version: ["3.10", "3.13"]' in audit_job
    assert "python -m pip install --require-hashes -r requirements-audit.txt" in audit_job
    for lock in (
        "requirements.txt",
        "requirements-build.txt",
        "requirements-audit.txt",
    ):
        assert f"pip-audit --strict --disable-pip -r {lock}" in audit_job
    assert "- audit" in publish_job
    assert "Verify release tag immediately before publishing" in publish_job
    attest_index = publish_job.index("Generate artifact attestations")
    verify_index = publish_job.index("Verify release tag immediately before publishing")
    pypi_index = publish_job.index("Publish to PyPI")
    assert attest_index < verify_index < pypi_index
    assert "git/ref/tags/${TAG}" in publish_job
    assert "git/tags/${tag_object}" in publish_job
    assert "verification.verified" in publish_job
    assert '"${tag_commit}" != "${GITHUB_SHA}"' in publish_job


def test_publish_workflow_executes_exact_artifacts_and_checks_formula_hash() -> None:
    workflow = read(".github/workflows/publish.yml")
    check_job = workflow.split("  check:", 1)[1].split("  validate:", 1)[0]

    assert "Verify staged Homebrew formula" in check_job
    assert "packaging/homebrew/pinghue.rb" in check_job
    assert "hashlib.sha256" in check_job
    assert "urlparse" in check_job
    assert '"files.pythonhosted.org"' in check_job
    assert "Install and smoke-test exact wheel" in check_job
    assert "Install and smoke-test exact source distribution" in check_job
    assert "--no-deps --no-build-isolation dist/*.tar.gz" in check_job
    assert check_job.count("127.0.0.1") >= 2


def test_publish_sdist_smoke_installs_hash_pinned_build_backend() -> None:
    workflow = read(".github/workflows/publish.yml")
    check_job = workflow.split("  check:", 1)[1].split("  validate:", 1)[0]

    build_backend_install = check_job.index(
        "python -m pip install --require-hashes -r requirements-build.txt"
    )
    sdist_install = check_job.index(
        '"${RUNNER_TEMP}/sdist-smoke/bin/python" -m pip install'
    )

    assert build_backend_install < sdist_install


def test_release_job_revalidates_tag_identity_and_refuses_tag_creation() -> None:
    workflow = read(".github/workflows/publish.yml")
    release_job = workflow.split("  release:", 1)[1]

    assert "Verify release tag again" in release_job
    assert "git/ref/tags/${TAG}" in release_job
    assert "git/tags/${tag_object}" in release_job
    assert "tag_name" in release_job
    assert "verification.verified" in release_job
    assert "target_type" in release_job
    assert "tag_commit" in release_job
    assert "GITHUB_SHA" in release_job
    assert 'gh release create "$TAG" dist/* --verify-tag' in release_job


def test_publish_workflow_keeps_package_check_out_of_build_job() -> None:
    workflow = read(".github/workflows/publish.yml")

    build_job, check_and_publish = workflow.split("  check:", 1)

    assert "twine==6.2.0" not in build_job
    assert "twine check dist/*" not in build_job
    assert "python -m build --no-isolation" in build_job
    assert re.search(r"actions/checkout@[0-9a-f]{40}", check_and_publish) is not None
    assert "persist-credentials: false" in check_and_publish
    assert (
        "python -m pip install --require-hashes -r requirements.txt"
        in check_and_publish
    )
    assert "pip install twine==" not in check_and_publish
    assert "twine check dist/*" in check_and_publish
    assert "needs:\n      - build\n      - check\n      - validate" in check_and_publish


def test_ci_build_matches_publish_build_path() -> None:
    # L16: CI exercises the same build invocation as publish.
    ci = read(".github/workflows/ci.yml")

    assert "pip install --require-hashes -r requirements-build.txt" in ci
    assert "python -m build --no-isolation" in ci
    assert 'SOURCE_DATE_EPOCH: "0"' in ci


def test_ci_and_publish_normalize_source_distributions() -> None:
    ci = read(".github/workflows/ci.yml")
    publish = read(".github/workflows/publish.yml")
    command = "python scripts/normalize-sdist.py dist/*.tar.gz"

    assert command in ci
    assert command in publish
    assert ci.index("python -m build --no-isolation") < ci.index(command)
    assert publish.index("python -m build --no-isolation") < publish.index(command)


def test_ci_requires_a_two_build_reproducibility_check() -> None:
    ci = read(".github/workflows/ci.yml")
    ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    status_rule = next(
        rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks"
    )
    contexts = {
        check["context"]
        for check in status_rule["parameters"]["required_status_checks"]
    }
    job = ci.split("  reproducible-build:", 1)[1]

    assert "name: Reproducible distributions" in job
    assert job.count("python -m build --no-isolation") == 2
    assert job.count("python scripts/normalize-sdist.py") == 2
    assert "cmp " in job
    assert '"${RUNNER_TEMP}/source-one"' in job
    assert '"${RUNNER_TEMP}/source-two"' in job
    assert "working-directory: ${{ runner.temp }}/source-one" in job
    assert "working-directory: ${{ runner.temp }}/source-two" in job
    assert "Reproducible distributions" in contexts


def test_ci_installs_only_hash_pinned_locks_before_the_local_project() -> None:
    ci = read(".github/workflows/ci.yml")
    requirements = read("requirements.txt")
    build_install = "python -m pip install --require-hashes -r requirements-build.txt"
    dev_install = "python -m pip install --require-hashes -r requirements.txt"
    project_install = "python -m pip install --no-deps --no-build-isolation -e ."

    assert "--generate-hashes" in requirements.splitlines()[1]
    assert "--hash=sha256:" in requirements
    assert build_install in ci
    assert dev_install in ci
    assert project_install in ci
    assert ci.index(build_install) < ci.index(dev_install) < ci.index(project_install)
    assert 'pip install -e ".[dev]"' not in ci


def test_main_ruleset_requires_pull_request_flow() -> None:
    ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    pull_request = next(rule for rule in ruleset["rules"] if rule["type"] == "pull_request")
    parameters = pull_request["parameters"]

    assert parameters["required_approving_review_count"] == 0
    assert parameters["required_review_thread_resolution"] is True
    assert parameters["allowed_merge_methods"] == ["squash", "rebase"]


def test_rulesets_disallow_bypass_and_bind_checks_to_github_actions() -> None:
    main_ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    tag_ruleset = json.loads(read(".github/repo-settings/release-tag-ruleset.json"))
    status_rule = next(
        rule for rule in main_ruleset["rules"] if rule["type"] == "required_status_checks"
    )
    required_checks = status_rule["parameters"]["required_status_checks"]

    assert main_ruleset["bypass_actors"] == []
    assert tag_ruleset["bypass_actors"] == []
    assert required_checks
    assert all(
        set(check) == {"context", "integration_id"} and check["integration_id"] == 15368
        for check in required_checks
    )


def test_pypi_environment_baseline_disables_administrator_bypass() -> None:
    environment = json.loads(read(".github/repo-settings/pypi-environment.json"))
    apply_script = read("scripts/apply-github-hardening.sh")

    assert environment["can_admins_bypass"] is False
    assert environment["reviewers"] == [{"type": "User", "id": 18606875}]
    assert 'payload.pop("can_admins_bypass")' in apply_script
    assert "validate_pypi_environment_policy" in apply_script
    assert apply_script.index("validate_pypi_environment_policy\n") < apply_script.index(
        "ensure_default_branch\n"
    )


def test_ci_and_homebrew_cover_python_313() -> None:
    ci = read(".github/workflows/ci.yml")
    formula = read("packaging/homebrew/pinghue.rb")
    pyproject = read("pyproject.toml")

    assert '"3.13"' in ci
    assert 'depends_on "python@3.13"' in formula
    assert '"Programming Language :: Python :: 3.13"' in pyproject


def test_ci_and_classifiers_cover_python_314() -> None:
    ci = read(".github/workflows/ci.yml")
    pyproject = read("pyproject.toml")
    readme = read("README.md")

    assert '"3.14"' in ci
    assert '"Programming Language :: Python :: 3.14"' in pyproject
    assert "Python 3.10 through 3.14" in readme


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
    assert "A run with no successful replies is `down` immediately" in readme
    assert "safety or security limit" in readme


def test_example_output_contains_a_possible_retained_sample_tail() -> None:
    example = json.loads(read("examples/pinghue-output-example.json"))

    for target in example["targets"]:
        sent = target["stats"]["sent"]
        samples = target["samples"]
        samples_window = example["run"]["samples_window"]
        assert len(samples) == min(sent, samples_window)
        if sent == len(samples):
            reconstructed = [
                ProbeSample(
                    timestamp=datetime.fromisoformat(
                        sample["timestamp"].replace("Z", "+00:00")
                    ),
                    latency_ms=sample["latency_ms"],
                    status=SampleStatus(sample["status"]),
                    error=sample["error"],
                )
                for sample in samples
            ]
            assert target["stats"] == asdict(summarize_samples(reconstructed))


def test_readme_discloses_existing_file_overwrite_tradeoff() -> None:
    readme = read("README.md")

    assert "not crash-atomic" in readme
    assert "descriptor-verified in-place rewrite" in readme
    assert "multiply linked regular files" in readme


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


def test_readme_artwork_captures_are_current_package_version() -> None:
    expected = f"pinghue {package_version()}"

    assert f"pinghue-version={expected}" in gif_comments("docs/assets/pinghue-demo.gif")
    assert png_text_chunks("docs/assets/pinghue-screenshot.png")["pinghue-version"] == expected


def test_release_checklist_revalidates_release_security_gates() -> None:
    checklist = read("docs/release-checklist.md")

    assert "pip-audit" in checklist
    assert "hosted hardening" in checklist
    assert "GitHub rulesets" in checklist
    assert "`pypi` environment" in checklist
    assert "Sigstore wheel signing" in checklist
    assert "--require-hashes -r requirements.txt" in checklist
    assert "pip-audit --strict --disable-pip -r requirements.txt" in checklist
    assert "pip-audit --strict --disable-pip -r requirements-build.txt" in checklist
    assert "pip-audit --strict --disable-pip -r requirements-audit.txt" in checklist
    assert "exact merged `main` SHA" in checklist
    assert "administrator bypass" in checklist
    assert "normalize-sdist.py" in checklist
    assert "Reproducible distributions" in checklist
    assert "SHA256 matches" in checklist


def test_release_checklist_requires_the_exact_pypi_url_for_final_formula() -> None:
    checklist = read("docs/release-checklist.md")
    after_publish = checklist.split("## After PyPI Publish", 1)[1]

    assert "exact published sdist URL" in after_publish
    assert "must not contain `/packages/source/`" in after_publish
    assert "exact published SHA256" in after_publish


def test_release_docs_tag_only_the_merged_public_main_commit() -> None:
    checklist = read("docs/release-checklist.md")
    readme = read("README.md")

    assert "git push origin main" not in checklist
    assert "release/X.Y.Z" in checklist
    assert "Merge the release PR" in checklist
    assert "git tag -s vX.Y.Z" in checklist
    assert "origin/main" in checklist
    assert checklist.index("Merge the release PR") < checklist.index("git tag -s vX.Y.Z")
    assert "merged public `main` commit" in readme


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


def test_hardening_scripts_paginate_ruleset_and_deployment_policy_inventories() -> None:
    for path in (
        "scripts/check-github-hardening.sh",
        "scripts/apply-github-hardening.sh",
    ):
        script = read(path)
        assert script.count("--paginate") >= 2
        assert '"repos/${repo}/rulesets"' in script
        assert '"repos/${repo}/environments/pypi/deployment-branch-policies"' in script
        logical_lines = script.replace("\\\n", " ").splitlines()
        assert not any(
            "gh api" in line
            and "--slurp" in line
            and ("--jq" in line or "--template" in line)
            for line in logical_lines
        )


def _run_repository_hardening_check(
    tmp_path: Path,
    *,
    main_ruleset: dict[str, object],
    tag_ruleset: dict[str, object],
    pypi_policies: list[dict[str, object]] | None = None,
    actions_permissions: dict[str, object] | None = None,
    workflow_permissions: dict[str, object] | None = None,
    private_vulnerability_reporting: dict[str, object] | None = None,
    vulnerability_alerts_enabled: bool = True,
    automated_security_fixes: dict[str, object] | None = None,
    default_branch: str | None = "main",
    can_admins_bypass: bool = False,
    admin_bypass_available: bool = True,
    pypi_reviewers: list[dict[str, object]] | None = None,
    prevent_self_review: bool = False,
    allow_hidden_bypass_actors: bool = False,
    allow_hidden_admin_settings: bool = False,
    api_failure_statuses: dict[str, int] | None = None,
    api_failure_bodies: dict[str, object] | None = None,
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    (tmp_path / "main.json").write_text(json.dumps(main_ruleset), encoding="utf-8")
    (tmp_path / "tag.json").write_text(json.dumps(tag_ruleset), encoding="utf-8")
    policies = (
        [{"id": 301, "name": "v*.*.*", "type": "tag"}]
        if pypi_policies is None
        else pypi_policies
    )
    (tmp_path / "pypi-policies.json").write_text(
        json.dumps({"branch_policies": policies}), encoding="utf-8"
    )
    pypi_environment: dict[str, object] = {
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        },
        "protection_rules": [
            {
                "type": "required_reviewers",
                "prevent_self_review": prevent_self_review,
                "reviewers": [
                    {
                        "type": reviewer["type"],
                        "reviewer": {"id": reviewer["id"]},
                    }
                    for reviewer in (
                        pypi_reviewers
                        if pypi_reviewers is not None
                        else [{"type": "User", "id": 18606875}]
                    )
                ],
            }
        ],
    }
    if admin_bypass_available:
        pypi_environment["can_admins_bypass"] = can_admins_bypass
    (tmp_path / "pypi-environment-live.json").write_text(
        json.dumps(pypi_environment), encoding="utf-8"
    )
    if actions_permissions is None:
        actions_permissions = {
            "enabled": True,
            "allowed_actions": "all",
            "sha_pinning_required": True,
        }
    if workflow_permissions is None:
        workflow_permissions = {
            "default_workflow_permissions": "read",
            "can_approve_pull_request_reviews": False,
        }
    if private_vulnerability_reporting is None:
        private_vulnerability_reporting = {"enabled": True}
    if automated_security_fixes is None:
        automated_security_fixes = {"enabled": True, "paused": False}
    for name, value in (
        ("actions", actions_permissions),
        ("workflow", workflow_permissions),
        ("private-reporting", private_vulnerability_reporting),
        ("security-fixes", automated_security_fixes),
    ):
        (tmp_path / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    gh = fake_bin / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if not args or args[0] != "api":
    raise SystemExit(f"unexpected gh invocation: {args!r}")

endpoint = next((arg for arg in args[1:] if arg.startswith("repos/")), "")
query = args[args.index("--jq") + 1] if "--jq" in args else ""
failure_statuses = json.loads(os.environ.get("FAKE_API_FAILURE_STATUSES", "{}"))
failure_bodies = json.loads(os.environ.get("FAKE_API_FAILURE_BODIES", "{}"))
if endpoint in failure_statuses:
    status = failure_statuses[endpoint]
    if "--include" in args:
        print(f"HTTP/2.0 {status} Error\\nContent-Type: application/json\\n")
        body = failure_bodies.get(endpoint)
        if body is not None:
            print(body if isinstance(body, str) else json.dumps(body))
    raise SystemExit(1)
if endpoint == "repos/example/project/rulesets":
    print("101" if "protect main" in query else "102")
elif endpoint == "repos/example/project/rulesets/101":
    print(Path(os.environ["FAKE_MAIN_RULESET"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/rulesets/102":
    print(Path(os.environ["FAKE_TAG_RULESET"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/environments/pypi":
    print(Path(os.environ["FAKE_PYPI_ENVIRONMENT"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/environments/pypi/deployment-branch-policies":
    print(Path(os.environ["FAKE_PYPI_POLICIES"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/actions/permissions/workflow":
    print(Path(os.environ["FAKE_WORKFLOW_PERMISSIONS"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/actions/permissions":
    print(Path(os.environ["FAKE_ACTIONS_PERMISSIONS"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/private-vulnerability-reporting":
    print(Path(os.environ["FAKE_PRIVATE_REPORTING"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project/vulnerability-alerts":
    if os.environ["FAKE_VULNERABILITY_ALERTS"] != "1":
        raise SystemExit(1)
elif endpoint == "repos/example/project/automated-security-fixes":
    print(Path(os.environ["FAKE_SECURITY_FIXES"]).read_text(encoding="utf-8"))
elif endpoint == "repos/example/project":
    if os.environ["FAKE_DEFAULT_BRANCH"] == "__HIDDEN__":
        raise SystemExit(1)
    print(json.dumps({"default_branch": os.environ["FAKE_DEFAULT_BRANCH"]}))
elif endpoint == "repos/example/project/pages":
    print(os.environ["FAKE_PAGES_STATE"])
else:
    raise SystemExit(f"unexpected endpoint: {endpoint}; args={args!r}")
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
            "FAKE_MAIN_RULESET": str(tmp_path / "main.json"),
            "FAKE_TAG_RULESET": str(tmp_path / "tag.json"),
            "FAKE_PYPI_POLICIES": str(tmp_path / "pypi-policies.json"),
            "FAKE_PYPI_ENVIRONMENT": str(tmp_path / "pypi-environment-live.json"),
            "FAKE_ACTIONS_PERMISSIONS": str(tmp_path / "actions.json"),
            "FAKE_WORKFLOW_PERMISSIONS": str(tmp_path / "workflow.json"),
            "FAKE_PRIVATE_REPORTING": str(tmp_path / "private-reporting.json"),
            "FAKE_SECURITY_FIXES": str(tmp_path / "security-fixes.json"),
            "FAKE_VULNERABILITY_ALERTS": "1" if vulnerability_alerts_enabled else "0",
            "FAKE_DEFAULT_BRANCH": default_branch or "__HIDDEN__",
            "FAKE_PAGES_STATE": "workflow|pinghue.com|true|approved",
            "FAKE_API_FAILURE_STATUSES": json.dumps(api_failure_statuses or {}),
            "FAKE_API_FAILURE_BODIES": json.dumps(api_failure_bodies or {}),
        }
    )
    if allow_hidden_bypass_actors:
        environment["PINGHUE_ALLOW_HIDDEN_BYPASS_ACTORS"] = "1"
    if allow_hidden_admin_settings:
        environment["PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS"] = "1"

    return subprocess.run(
        ["bash", "scripts/check-github-hardening.sh", "example/project"],
        cwd=Path.cwd(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _hosted_rulesets() -> tuple[dict[str, object], dict[str, object]]:
    main_ruleset = json.loads(read(".github/repo-settings/main-ruleset.json"))
    tag_ruleset = json.loads(read(".github/repo-settings/release-tag-ruleset.json"))
    main_ruleset["bypass_actors"] = []
    tag_ruleset["bypass_actors"] = []
    status_rule = next(
        rule for rule in main_ruleset["rules"] if rule["type"] == "required_status_checks"
    )
    for check in status_rule["parameters"]["required_status_checks"]:
        check["integration_id"] = 15368
    return main_ruleset, tag_ruleset


def test_repository_hardening_check_rejects_wrong_status_check_source(tmp_path: Path) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    status_rule = next(
        rule for rule in main_ruleset["rules"] if rule["type"] == "required_status_checks"
    )
    status_rule["parameters"]["required_status_checks"][0]["integration_id"] = 99999

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
    )

    assert result.returncode == 1
    assert "status" in result.stderr.lower()


def test_repository_hardening_check_rejects_untracked_rules_and_parameters(
    tmp_path: Path,
) -> None:
    base_main, tag_ruleset = _hosted_rulesets()

    extra_rule = json.loads(json.dumps(base_main))
    extra_rule["rules"].append({"type": "creation"})
    extra_rule_result = _run_repository_hardening_check(
        tmp_path / "extra-rule",
        main_ruleset=extra_rule,
        tag_ruleset=tag_ruleset,
    )

    extra_check = json.loads(json.dumps(base_main))
    status_rule = next(
        rule
        for rule in extra_check["rules"]
        if rule["type"] == "required_status_checks"
    )
    status_rule["parameters"]["required_status_checks"].append(
        {"context": "untracked check", "integration_id": 15368}
    )
    extra_check_result = _run_repository_hardening_check(
        tmp_path / "extra-check",
        main_ruleset=extra_check,
        tag_ruleset=tag_ruleset,
    )

    changed_parameter = json.loads(json.dumps(base_main))
    pull_request_rule = next(
        rule
        for rule in changed_parameter["rules"]
        if rule["type"] == "pull_request"
    )
    pull_request_rule["parameters"]["require_code_owner_review"] = True
    changed_parameter_result = _run_repository_hardening_check(
        tmp_path / "changed-parameter",
        main_ruleset=changed_parameter,
        tag_ruleset=tag_ruleset,
    )

    assert extra_rule_result.returncode == 1
    assert "rule" in extra_rule_result.stderr.lower()
    assert extra_check_result.returncode == 1
    assert "status check" in extra_check_result.stderr.lower()
    assert changed_parameter_result.returncode == 1
    assert "require_code_owner_review" in changed_parameter_result.stderr


def test_repository_hardening_check_rejects_bypass_actors(tmp_path: Path) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    main_ruleset["bypass_actors"] = [
        {"actor_id": 1, "actor_type": "RepositoryRole", "bypass_mode": "always"}
    ]

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
    )

    assert result.returncode == 1
    assert "bypass_actors" in result.stderr


def test_repository_hardening_check_fails_closed_when_bypass_data_is_hidden(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    main_ruleset.pop("bypass_actors")
    tag_ruleset.pop("bypass_actors")

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
    )

    assert result.returncode == 1
    assert "bypass_actors" in result.stderr


def test_scheduled_hardening_check_explicitly_warns_when_bypass_data_is_hidden(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    main_ruleset.pop("bypass_actors")
    tag_ruleset.pop("bypass_actors")

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        allow_hidden_bypass_actors=True,
    )

    assert result.returncode == 0, result.stderr
    assert "warning" in result.stderr.lower()
    assert "bypass_actors" in result.stderr


def test_repository_hardening_check_requires_the_sole_exact_pypi_tag_policy(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    extra_policy = _run_repository_hardening_check(
        tmp_path / "extra",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        pypi_policies=[
            {"id": 301, "name": "v*.*.*", "type": "tag"},
            {"id": 302, "name": "*", "type": "branch"},
        ],
    )
    wrong_type = _run_repository_hardening_check(
        tmp_path / "wrong-type",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        pypi_policies=[{"id": 303, "name": "v*.*.*", "type": "branch"}],
    )

    assert extra_policy.returncode == 1
    assert "deployment" in extra_policy.stderr.lower()
    assert wrong_type.returncode == 1
    assert "deployment" in wrong_type.stderr.lower()


def test_repository_hardening_check_rejects_pypi_administrator_bypass(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        can_admins_bypass=True,
    )

    assert result.returncode == 1
    assert "administrator bypass" in result.stderr.lower()


def test_repository_hardening_check_rejects_missing_pypi_admin_bypass(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        admin_bypass_available=False,
        allow_hidden_admin_settings=True,
    )

    assert result.returncode == 1
    assert "can_admins_bypass" in result.stderr


def test_repository_hardening_check_rejects_reviewer_substitution(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        pypi_reviewers=[{"type": "User", "id": 999999}],
    )

    assert result.returncode == 1
    assert "reviewer identities" in result.stderr


def test_actions_permission_baselines_are_least_privilege_and_enforced(
    tmp_path: Path,
) -> None:
    actions = json.loads(read(".github/repo-settings/actions-permissions.json"))
    workflow = json.loads(read(".github/repo-settings/actions-workflow-permissions.json"))
    main_ruleset, tag_ruleset = _hosted_rulesets()

    assert actions == {
        "enabled": True,
        "allowed_actions": "all",
        "sha_pinning_required": True,
    }
    assert workflow == {
        "default_workflow_permissions": "read",
        "can_approve_pull_request_reviews": False,
    }

    wrong_sha_policy = dict(actions)
    wrong_sha_policy["sha_pinning_required"] = False
    actions_result = _run_repository_hardening_check(
        tmp_path / "actions",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        actions_permissions=wrong_sha_policy,
    )
    wrong_workflow_policy = dict(workflow)
    wrong_workflow_policy["default_workflow_permissions"] = "write"
    workflow_result = _run_repository_hardening_check(
        tmp_path / "workflow",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        workflow_permissions=wrong_workflow_policy,
    )

    assert actions_result.returncode == 1
    assert "actions" in actions_result.stderr.lower()
    assert workflow_result.returncode == 1
    assert "workflow" in workflow_result.stderr.lower()


def test_repository_hardening_check_rejects_disabled_security_features(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    private_reporting = _run_repository_hardening_check(
        tmp_path / "private-reporting",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        private_vulnerability_reporting={"enabled": False},
    )
    alerts = _run_repository_hardening_check(
        tmp_path / "alerts",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        vulnerability_alerts_enabled=False,
    )
    fixes = _run_repository_hardening_check(
        tmp_path / "fixes",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        automated_security_fixes={"enabled": True, "paused": True},
    )

    assert private_reporting.returncode == 1
    assert "private vulnerability" in private_reporting.stderr.lower()
    assert alerts.returncode == 1
    assert "vulnerability alerts" in alerts.stderr.lower()
    assert fixes.returncode == 1
    assert "automated security fixes" in fixes.stderr.lower()


def test_repository_hardening_check_rejects_default_branch_drift(tmp_path: Path) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        default_branch="develop",
    )

    assert result.returncode == 1
    assert "default branch" in result.stderr.lower()


def test_scheduled_hardening_check_rejects_missing_fields_in_successful_responses(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        actions_permissions={},
        workflow_permissions={},
        private_vulnerability_reporting={},
        automated_security_fixes={},
        allow_hidden_admin_settings=True,
    )

    assert result.returncode == 1
    assert "fields are unavailable" in result.stderr.lower()


def test_scheduled_hardening_check_only_tolerates_admin_http_403(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    admin_endpoints = (
        "repos/example/project/actions/permissions",
        "repos/example/project/actions/permissions/workflow",
        "repos/example/project/private-vulnerability-reporting",
        "repos/example/project/vulnerability-alerts",
        "repos/example/project/automated-security-fixes",
    )

    permission_denied = _run_repository_hardening_check(
        tmp_path / "permission-denied",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        allow_hidden_admin_settings=True,
        api_failure_statuses={endpoint: 403 for endpoint in admin_endpoints},
        api_failure_bodies={
            endpoint: {"message": "Resource not accessible by integration"}
            for endpoint in admin_endpoints
        },
    )
    server_error = _run_repository_hardening_check(
        tmp_path / "server-error",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        allow_hidden_admin_settings=True,
        api_failure_statuses={admin_endpoints[0]: 500},
    )
    disabled_alerts = _run_repository_hardening_check(
        tmp_path / "disabled-alerts",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        allow_hidden_admin_settings=True,
        api_failure_statuses={admin_endpoints[3]: 404},
    )
    unreadable_default_branch = _run_repository_hardening_check(
        tmp_path / "default-branch",
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        default_branch=None,
        allow_hidden_admin_settings=True,
    )

    assert permission_denied.returncode == 0, permission_denied.stderr
    assert permission_denied.stderr.count("HTTP 403") == len(admin_endpoints)
    assert server_error.returncode == 1
    assert "HTTP 500" in server_error.stderr
    assert disabled_alerts.returncode == 1
    assert "disabled (HTTP 404)" in disabled_alerts.stderr
    assert unreadable_default_branch.returncode == 1
    assert "default branch" in unreadable_default_branch.stderr.lower()


def test_scheduled_hardening_check_rejects_non_permission_403_responses(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()
    endpoint = "repos/example/project/actions/permissions"

    for name, body in (
        ("rate-limit", {"message": "API rate limit exceeded"}),
        ("secondary-limit", {"message": "You have exceeded a secondary rate limit."}),
        ("generic", {"message": "Forbidden"}),
        ("malformed", "not-json"),
    ):
        result = _run_repository_hardening_check(
            tmp_path / name,
            main_ruleset=main_ruleset,
            tag_ruleset=tag_ruleset,
            allow_hidden_admin_settings=True,
            api_failure_statuses={endpoint: 403},
            api_failure_bodies={endpoint: body},
        )

        assert result.returncode == 1, name
        assert "HTTP 403" in result.stderr


def test_scheduled_hardening_check_does_not_hide_visible_admin_drift(
    tmp_path: Path,
) -> None:
    main_ruleset, tag_ruleset = _hosted_rulesets()

    result = _run_repository_hardening_check(
        tmp_path,
        main_ruleset=main_ruleset,
        tag_ruleset=tag_ruleset,
        actions_permissions={"enabled": False},
        allow_hidden_admin_settings=True,
    )

    assert result.returncode == 1
    assert "actions repository permissions" in result.stderr.lower()
    assert "drifted" in result.stderr.lower()


def test_hosted_hardening_workflow_uses_documented_reduced_visibility_mode() -> None:
    workflow = read(".github/workflows/repository-hardening.yml")
    documentation = read("docs/repository-hardening.md")
    check_step = workflow.split("- name: Check hosted repository hardening", 1)[1]

    assert 'PINGHUE_ALLOW_HIDDEN_BYPASS_ACTORS: "1"' in check_step
    assert workflow.count("PINGHUE_ALLOW_HIDDEN_BYPASS_ACTORS") == 1
    assert "PINGHUE_ALLOW_HIDDEN_BYPASS_ACTORS" in documentation
    assert "fail closed" in documentation.lower()
    assert "bypass_actors" in documentation
    assert 'PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS: "1"' in check_step
    assert workflow.count("PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS") == 1
    assert "PINGHUE_ALLOW_HIDDEN_ADMIN_SETTINGS" in documentation
    assert "actions: read" in workflow


def test_security_feature_policy_is_enabled_and_consumed_by_apply_helper() -> None:
    policy = json.loads(read(".github/repo-settings/security-features.json"))
    apply_script = read("scripts/apply-github-hardening.sh")

    assert policy == {
        "private_vulnerability_reporting": {"enabled": True},
        "vulnerability_alerts": {"enabled": True},
        "automated_security_fixes": {"enabled": True, "paused": False},
    }
    assert "security-features.json" in apply_script
    assert "validate_security_feature_policy" in apply_script


def test_repository_hardening_docs_list_default_branch_once() -> None:
    documentation = read("docs/repository-hardening.md")

    assert documentation.count("- Default branch: `main`") == 1


def test_readme_asset_tapes_do_not_interpolate_the_executable_directory() -> None:
    script = read("scripts/gen-readme-assets.sh")
    tape_bodies = re.findall(
        r'cat > "\$\{tmp\}/(?:demo|shot)\.tape" <<TAPE\n(.*?)\nTAPE',
        script,
        re.DOTALL,
    )

    assert len(tape_bodies) == 2
    assert all("${bindir}" not in body for body in tape_bodies)
    assert re.search(
        r'(?:env\s+)?PATH="\$\{bindir\}:\$\{PATH\}"\s+vhs "\$\{tmp\}/demo\.tape"',
        script,
    )
    assert re.search(
        r'(?:env\s+)?PATH="\$\{bindir\}:\$\{PATH\}"\s+vhs "\$\{tmp\}/shot\.tape"',
        script,
    )


def test_pages_workflow_cannot_be_dispatched_from_an_arbitrary_ref() -> None:
    workflow = read(".github/workflows/pages.yml")

    assert "workflow_dispatch:" not in workflow


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
    version = package_version()
    pyproject = read("pyproject.toml")
    readme = read("README.md")
    example = json.loads(read("examples/pinghue-output-example.json"))
    hero = read("docs/assets/pinghue-hero.svg")
    formula = read("packaging/homebrew/pinghue.rb")

    # The demo (GIF) and screenshot (PNG) are real TUI captures, so the version
    # they show is rendered from the package itself and needs no text surface here.
    assert f'version = "{version}"' in pyproject
    assert f"Current version: `{version}`." in readme
    assert example["pinghue_version"] == version
    assert f"v{version}" in hero
    assert f"pinghue-{version}.tar.gz" in formula
    assert "pinghue-2.1.0.tar.gz" not in formula


def test_release_text_surfaces_do_not_reference_stale_current_version() -> None:
    version = package_version()
    text_surfaces = [
        "pyproject.toml",
        "README.md",
        "SECURITY.md",
        "security-best-practices-report.md",
        "CONTRIBUTING.md",
        "examples/pinghue-output-example.json",
        "docs/index.html",
        "docs/assets/pinghue-hero.svg",
        "docs/assets/pinghue-favicon.svg",
        "packaging/homebrew/pinghue.rb",
    ]
    current_version_patterns = [
        re.compile(r'version = "(\d+\.\d+\.\d+)"'),
        re.compile(r"Current version: `(\d+\.\d+\.\d+)`"),
        re.compile(r'"pinghue_version": "(\d+\.\d+\.\d+)"'),
        re.compile(r"pinghue-(\d+\.\d+\.\d+)"),
        re.compile(r"`pinghue (\d+\.\d+\.\d+)`"),
        re.compile(r"pinghue v(\d+\.\d+\.\d+)"),
        re.compile(r"v(\d+\.\d+\.\d+)"),
    ]

    for path in text_surfaces:
        text = read(path)
        for pattern in current_version_patterns:
            for match in pattern.finditer(text):
                assert match.group(1) == version, f"{path} still contains {match.group(0)}"


def test_security_policy_matches_stable_support_line() -> None:
    major = package_version().split(".", 1)[0]
    security = read("SECURITY.md")
    supported_major_lines = re.findall(r"`(\d+)\.x`\s+\|\s+Yes", security)

    assert f"`{major}.x` | Yes" in security
    assert f"`<{major}.0` | No" in security
    assert supported_major_lines == [major]
