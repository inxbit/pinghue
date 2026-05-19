# pinghue Security Best-Practices Report

## Executive summary

No open critical or high-risk runtime vulnerability was found in this pass. `pinghue` is a local Python CLI/TUI rather than a web service, so the available framework-specific security references for Django, FastAPI, and Flask do not directly apply. The current code follows the important secure defaults for this project shape: no shell execution from runtime inputs, no unsafe deserialization, bounded host-file reads, sanitized terminal/JSON evidence, unprivileged ICMP guidance, and hardened release workflows.

## Scope

Reviewed:

- Runtime package under `src/pinghue`.
- CLI/TUI/no-TUI behavior, host files, DNS/TCP/ICMP probes, JSON export, and doctor diagnostics.
- Packaging and release controls in `.github`, `MANIFEST.in`, `pyproject.toml`, and `packaging/homebrew/pinghue.rb`.
- Security tests and repository-hardening tests under `tests/`.

Reference coverage:

- No Python CLI-specific reference was available in the review reference set.
- The review used general Python secure-coding practice plus repository-specific threat model evidence.

## Findings by severity

### Critical

None.

### High

None.

### Medium

None currently open.

Previously relevant medium-risk classes are now controlled:

| ID | Class | Status | Evidence |
| --- | --- | --- | --- |
| BP-001 | Terminal or downstream JSON control-character injection | Controlled | `sanitize_display` escapes C0/C1 controls in `src/pinghue/display.py:6`; no-TUI output uses it in `src/pinghue/runner.py:184`; TUI cells use it in `src/pinghue/ui.py:217`; JSON export uses it in `src/pinghue/export.py:25`. |
| BP-002 | Release workflow supply-chain exposure | Controlled | Publish workflow pins actions, separates build and publish jobs, uses OIDC, and generates attestations in `.github/workflows/publish.yml:11` and `.github/workflows/publish.yml:38`. |
| BP-003 | Evidence loss or temp-path symlink redirection on JSON write | Controlled | No-TUI mode installs SIGINT/SIGTERM handlers and records interrupted exits in `src/pinghue/runner.py:121` and `src/pinghue/runner.py:210`; JSON writes use a randomized `NamedTemporaryFile` (O_EXCL, mode 0600) followed by atomic replace in `src/pinghue/export.py:92`. |

### Low and residual

| ID | Issue | Risk | Recommendation |
| --- | --- | --- | --- |
| BP-004 | Operator-selected output path can overwrite an existing writable file. | Low, because the same local user supplies the path and no privilege boundary is crossed. | Keep documentation clear that `--output` writes the selected path; re-review if `pinghue` is ever wrapped by a privileged service. |
| BP-005 | Host-file validation has a normal path/stat/read race. | Low, because the path is local operator-controlled and the tool is unprivileged. | If future packaging adds privilege, switch to descriptor-based open/stat/read handling. |
| BP-006 | Hosted GitHub rulesets and PyPI environment state cannot be proven from local files. | Closed in hosted repo as of 2026-05-19: live GitHub API checks showed active `protect main` and `protect release tags` rulesets, a protected `pypi` environment, and a `v*.*.*` tag deployment policy. Re-verify after any repository migration or owner change. | Keep the script as the source of truth; re-run after migrations and audit ruleset diffs before each release. |

## Secure defaults observed

- CLI range validation covers interval, timeout, port, count, duration, concurrency, and fail threshold in `src/pinghue/cli.py:151`.
- `--numeric` requires IP literals and uses automatic family mode for mixed IPv4/IPv6 literals in `src/pinghue/cli.py:134`.
- Host files must be regular files, are capped at 1 MiB, and abort above 5,000 lines in `src/pinghue/hostfile.py:9`.
- DNS resolution uses OS resolver APIs without shell execution in `src/pinghue/probes.py:53`.
- TCP probing uses `asyncio.open_connection` with timeout and closed writers in `src/pinghue/probes.py:103`.
- ICMP probing uses `icmplib` unprivileged mode in `src/pinghue/probes.py:144`.
- Probe concurrency is bounded through `asyncio.Semaphore` in `src/pinghue/runner.py:216`.
- JSON host metadata defaults to the non-identifying `local` label through `--host-label` in `src/pinghue/cli.py:111`.
- JSON output is written through a randomized `NamedTemporaryFile` (O_EXCL, mode 0600) in the destination directory and atomically replaced in `src/pinghue/export.py:92`.
- Doctor guidance recommends group-specific `ping_group_range` before capability-based fallback in `src/pinghue/doctor.py:152`.
- CI uses read-only repository permissions and non-persisted checkout credentials in `.github/workflows/ci.yml:9`.
- Publish workflow uses pinned action SHAs, scoped publish permissions, artifact attestations, and release concurrency in `.github/workflows/publish.yml:11`.
- Dependency audit runs weekly and on relevant PRs in `.github/workflows/dependency-audit.yml:1`.
- `MANIFEST.in` excludes `.github` and `scripts` from published sdists while retaining user-facing docs and schemas in `MANIFEST.in:1`.
- Homebrew resources are SHA256-pinned, and the formula test verifies a real local TCP success path with `--fail-on-down` in `packaging/homebrew/pinghue.rb:20` and `packaging/homebrew/pinghue.rb:106`.

## Best-practice checklist

| Area | Status | Notes |
| --- | --- | --- |
| Input validation | Pass | CLI values and host files are validated before runtime scheduling. |
| Terminal output safety | Pass | Display sanitization is centralized and used by no-TUI, TUI, and JSON export paths. |
| Local file handling | Pass with residual note | Host-file reads and JSON writes are bounded for the current unprivileged CLI model. |
| Privilege minimization | Pass | Runtime avoids root requirements; Linux ICMP guidance prefers group-specific unprivileged sockets. |
| Shell/code execution | Pass | No runtime `subprocess`, `os.system`, `eval`, or `exec` sink was found. |
| Serialization | Pass | JSON export uses structured objects and schema tests; no pickle/YAML/XML parser surface was found. |
| Dependency hygiene | Pass | Runtime ranges are narrow; Dependabot and `pip-audit` workflow are present. |
| Release hardening | Pass | Pinned actions, OIDC trusted publishing, attestations, protected ruleset templates, and sdist pruning are present. |
| Secrets handling | Pass | No static PyPI tokens, GitHub tokens, passwords, or API keys were found in reviewed files. |

## Validation

- Repository sink search: reviewed matches for shell execution, dynamic evaluation, serialization, file I/O, sockets, secrets, privilege commands, and release workflow permissions.
- `.venv/bin/python -m pytest`: 69 passed.
- `.venv/bin/pip-audit --cache-dir /tmp/pinghue-pip-audit-cache`: no known vulnerabilities found.

## Recommended follow-up

- Re-verify hosted GitHub branch rulesets, release-tag ruleset, and `pypi` environment approval after repository migrations or manual settings changes.
- Keep `pip-audit` and Dependabot visible before every release.
- Re-open the file-handling threat model if `pinghue` is ever executed by a privileged wrapper, daemon, or scheduled service.
