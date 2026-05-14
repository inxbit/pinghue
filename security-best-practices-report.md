# pinghue Security Best-Practices Report

## Summary

No open critical or high-risk runtime vulnerabilities were found in this pass. Two practical hardening issues were fixed during the review:

- Terminal control characters in operator-visible target and error text are now escaped before TUI/no-TUI rendering.
- Release workflows now use pinned action SHAs, disable persisted checkout credentials, pin publish build tooling, split build from publish permissions, and include Dependabot tracking.

## Remediated Findings

### Terminal control-character injection

Status: remediated.

Target names can come from CLI arguments or host files. Error strings can come from OS/socket libraries. Those values were rendered back to the terminal, which could let a malicious or copied host list include escape sequences that clear the screen, recolor output, or make logs misleading.

Controls added:

- `src/pinghue/display.py` escapes C0/C1 terminal control characters.
- `src/pinghue/runner.py` uses the sanitizer for no-TUI target and error output.
- `src/pinghue/ui.py` uses the sanitizer for TUI host/address cells.
- `tests/test_display_safety.py` covers no-TUI and TUI rendering paths.

### Release workflow supply-chain hardening

Status: remediated.

The publish workflow has valuable permissions because it publishes to PyPI through trusted publishing and creates GitHub releases. Mutable action references and unpinned build tooling are unnecessary release-integrity risk.

Controls added:

- `.github/workflows/ci.yml` and `.github/workflows/publish.yml` pin third-party actions to exact commit SHAs.
- Checkout steps use `persist-credentials: false`.
- Publish build tooling is pinned and release builds use `python -m build --no-isolation`.
- Release publishing is split into a build job with read-only permissions and a publish job with `id-token: write` and `contents: write`.
- `.github/dependabot.yml` tracks GitHub Actions and Python dependency updates.
- `.github/repo-settings` and `scripts/apply-github-hardening.sh` document and apply branch, tag, and PyPI environment protections.

## Residual Recommendations

- Apply and verify repository rulesets after the first GitHub push.
- Verify the `pypi` environment has the intended reviewer before trusted publishing runs.
- Keep the PyPI trusted-publisher configuration scoped to the publish workflow and `pypi` environment.
- If host files are expected from untrusted sources later, add explicit target-count and file-size limits.
- If pinghue is ever packaged as a privileged helper, re-review host file reads and output path writes under the new privilege boundary.

## Validation

- `python -m pytest`: 36 passed.
- `ruff check .`: passed.
- `mypy src`: passed.
- Workflow YAML parse check: passed.
- `python -m build --no-isolation`: built sdist and wheel.
- `twine check dist/*`: passed.
- Wheel install smoke test: passed.
- `pinghue -p 1 127.0.0.1 -c 1 --no-tui --output /private/tmp/pinghue-smoke.json`: completed with exit code 0.
- JSON schema validation for the smoke output: passed.
