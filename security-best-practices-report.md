# pinghue Security Best-Practices Report

## Executive summary

No open critical or high-risk runtime vulnerability was found in this pass. `pinghue` is a local Python CLI/TUI rather than a web service, so the available framework-specific security references for Django, FastAPI, and Flask do not directly apply. The current code follows the important secure defaults for this project shape: no shell execution from runtime inputs, no unsafe deserialization, finite and bounded CLI inputs, descriptor-validated host-file reads, sanitized terminal/JSON/doctor diagnostic evidence, no-clobber JSON output by default, guarded output-path handling, unprivileged ICMP guidance, async probe containment, bounded DNS resolution, and hardened release workflows with hosted drift checks.

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
| BP-001 | Terminal or downstream JSON control-character injection | Controlled | `sanitize_display` escapes controls and non-ASCII characters in `src/pinghue/display.py:7`; no-TUI output, TUI cells, JSON export, and doctor DNS diagnostics use it before rendering operator-controlled strings. |
| BP-002 | Release workflow supply-chain exposure | Controlled | Publish workflow pins actions, verifies release tags against `pyproject.toml`, installs build tooling from hash-pinned `requirements-build.txt`, keeps package checking outside the artifact-producing build job, uses OIDC, and generates attestations. |
| BP-003 | Evidence loss or temp-path symlink redirection on JSON write | Controlled | No-TUI mode installs SIGINT/SIGTERM handlers and records interrupted exits; JSON writes use a randomized `NamedTemporaryFile` (O_EXCL, mode 0600), reject symlink/special-device replacement paths by default, refuse to replace existing regular files by default, and require explicit `--overwrite` for replacement. |
| BP-004 | Non-finite or oversized operator inputs | Controlled | CLI validation rejects non-finite float values, empty targets, targets above 253 characters, diagnostic resolve names above 253 characters, and host labels above 128 characters in `src/pinghue/cli.py:143` and `src/pinghue/cli.py:178`. |
| BP-005 | Host-file symlink and path validation races | Controlled | Host files are opened with `O_NOFOLLOW` where available, validated with `fstat()` on the opened descriptor, capped at 1 MiB, capped at 5,000 lines, and target-capped at 253 characters in `src/pinghue/hostfile.py:15` and `src/pinghue/hostfile.py:54`. |
| BP-006 | Resolver and probe-loop instability | Controlled | DNS resolution is throttled with an event-loop-scoped semaphore and bounded lookup timeout, failed DNS targets retry after a cooldown, unexpected resolver/probe exceptions are converted to target/sample failures, and working addresses are prioritized. |

### Low and residual

| ID | Issue | Risk | Recommendation |
| --- | --- | --- | --- |
| BP-007 | Operator-selected output path can overwrite an existing writable file. | Closed by default no-clobber behavior; replacement now requires explicit `--overwrite`. | Keep overwrite coverage in export and CLI tests. |
| BP-008 | Hosted GitHub rulesets and PyPI environment state can drift. | Controlled by `scripts/check-github-hardening.sh`, scheduled `.github/workflows/repository-hardening.yml`, and live verification on 2026-05-31. The drift check now validates active branch/tag ruleset internals against the checked-in policy files. | Keep the scheduled workflow visible before release and re-run manually after migrations. |

## Secure defaults observed

- CLI validation covers finite interval, timeout, duration, and jitter values; port, count, concurrency, and fail threshold ranges; and target, resolve-name, and host-label lengths in `src/pinghue/cli.py:143` and `src/pinghue/cli.py:178`.
- `--numeric` requires IP literals and uses automatic family mode for mixed IPv4/IPv6 literals in `src/pinghue/cli.py:134`.
- Host files must be non-symlink regular files, are validated by descriptor, capped at 1 MiB, capped at 5,000 lines, and reject targets above 253 characters in `src/pinghue/hostfile.py:15`.
- DNS resolution uses OS resolver APIs without shell execution in `src/pinghue/probes.py:100`.
- TCP probing uses `asyncio.open_connection` with timeout and closed writers in `src/pinghue/probes.py:103`.
- ICMP probing uses `icmplib` unprivileged mode in `src/pinghue/probes.py:144`.
- Probe concurrency is bounded through `asyncio.Semaphore`, and DNS resolution has its own lower semaphore cap plus a bounded lookup timeout.
- JSON host metadata defaults to the non-identifying `local` label through `--host-label` in `src/pinghue/cli.py:111`.
- JSON output is written through a randomized `NamedTemporaryFile` (O_EXCL, mode 0600) in the destination directory, rejects symlink/special-device output paths by default, and refuses to replace existing regular files unless `--overwrite` is set.
- Doctor guidance recommends group-specific `ping_group_range` or TCP mode instead of file capabilities on Python launcher scripts in `src/pinghue/doctor.py:152`.
- CI uses read-only repository permissions and non-persisted checkout credentials in `.github/workflows/ci.yml:9`.
- Publish workflow uses pinned action SHAs, tag/version verification, hash-pinned build tooling, scoped publish permissions, artifact attestations, and release concurrency in `.github/workflows/publish.yml:11`.
- Dependency audit runs weekly and on relevant PRs in `.github/workflows/dependency-audit.yml:1`; hosted repository hardening drift is checked weekly in `.github/workflows/repository-hardening.yml:1`.
- `MANIFEST.in` excludes `.github` and `packaging` from published sdists while retaining the launcher script, user-facing docs, examples, schemas, and release reports in `MANIFEST.in:1`.
- Homebrew resources are SHA256-pinned, and the formula test verifies a real local TCP success path with `--fail-on-down` in `packaging/homebrew/pinghue.rb:20` and `packaging/homebrew/pinghue.rb:106`.

## Best-practice checklist

| Area | Status | Notes |
| --- | --- | --- |
| Input validation | Pass | CLI values and host-file targets are finite, range-checked, and length-bounded before runtime scheduling. |
| Terminal output safety | Pass | Display sanitization is centralized and used by no-TUI, TUI, JSON export, and doctor DNS diagnostic paths. |
| Local file handling | Pass | Host-file reads use descriptor validation and JSON writes use randomized atomic temp files with default no-clobber behavior. |
| Async stability | Pass | Resolver and probe failures are contained per target/sample, and DNS work is throttled and timeout-bounded separately from probe concurrency. |
| Privilege minimization | Pass | Runtime avoids root requirements; Linux ICMP guidance prefers group-specific unprivileged sockets. |
| Shell/code execution | Pass | No runtime `subprocess`, `os.system`, `eval`, or `exec` sink was found. |
| Serialization | Pass | JSON export uses structured objects and schema tests; no pickle/YAML/XML parser surface was found. |
| Dependency hygiene | Pass | Runtime ranges are narrow; Dependabot and `pip-audit` workflow are present. |
| Release hardening | Pass | Pinned actions, OIDC trusted publishing, attestations, protected ruleset templates, hosted ruleset-content drift checks, and sdist pruning are present. |
| Secrets handling | Pass | No static PyPI tokens, GitHub tokens, passwords, or API keys were found in reviewed files. |

## Validation

- Repository sink search: reviewed matches for shell execution, dynamic evaluation, serialization, file I/O, sockets, secrets, privilege commands, and release workflow permissions.
- Security diff scan of modified runtime, workflow, site, packaging, and hardening files: no issues identified.
- `.venv/bin/ruff check .`: all checks passed.
- `.venv/bin/mypy src`: no issues in 14 source files.
- `.venv/bin/pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=80`: 199 passed with 87.81% coverage, above the configured 80% floor.
- `node --test tests/site_pages.test.mjs`: site contract test passed.
- `.venv/bin/pip-audit --skip-editable .`: no known vulnerabilities found.
- `SOURCE_DATE_EPOCH=0 .venv/bin/python -m build --no-isolation`: built `pinghue-3.0.1.tar.gz` and `pinghue-3.0.1-py3-none-any.whl`.
- `.venv/bin/twine check` on the built and published artifacts: wheel and sdist passed.
- `.venv/bin/python -m pinghue --version`: `pinghue 3.0.1`.
- `.venv/bin/pinghue --version`: `pinghue 3.0.1`.
- `ruby -c packaging/homebrew/pinghue.rb`: formula syntax passed.
- `scripts/check-github-hardening.sh inxbit/pinghue`: hosted hardening checks passed.

## Recommended follow-up

- Keep `pip-audit`, Dependabot, and repository-hardening drift checks visible before every release.
- Re-open the file-handling threat model if `pinghue` is ever executed by a privileged wrapper, daemon, or scheduled service.
