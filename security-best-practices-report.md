# PingHue Security Best-Practices Report

## Status

Review date: 2026-07-10

No open critical or high-severity vulnerability was found in the local
CLI/runtime code. The reviewed implementation is a local, unprivileged
ICMP/TCP monitoring tool: it opens no inbound service, handles no credentials,
uses no runtime shell execution, and has no authentication, database, template,
or unsafe-deserialization surface.

One release-blocking hosted-hardening finding remains open. Live read-only
verification found several GitHub settings weaker than their checked-in
baselines. The code now detects these conditions fail-closed, but the hosted
settings have not been changed by this repository review.

## Scope

Reviewed:

- All runtime modules under `src/pinghue`.
- Launcher, release, repository-hardening, artifact-normalization, and asset
  generation scripts.
- GitHub Actions workflows and checked-in repository setting baselines.
- Python package metadata, dependency locks, sdist manifest, schema, examples,
  static site, Homebrew formula, and security/release documentation.
- Existing and newly added tests, including race, interruption, cancellation,
  memory-bound, packaging, and hardening-contract coverage.

The companion `pinghue-threat-model.md` records assets, trust boundaries,
attacker capabilities, abuse paths, residual risk, and review triggers.

## Open findings and residual risk

### BP-001 — Hosted release/security settings drift (Medium, open)

The 2026-07-10 live read found:

- GitHub Actions full-SHA enforcement disabled.
- Private vulnerability reporting disabled.
- Dependency vulnerability alerts disabled.
- Automated security fixes disabled.
- Administrator bypass enabled on the protected `pypi` environment.
- The hosted `main` ruleset does not yet require the newly checked-in
  reproducibility and Python 3.10/3.13 dependency-audit contexts with their
  GitHub Actions source bindings.

Impact: these settings reduce defense in depth around dependency visibility,
workflow dependency policy, vulnerability intake, and protected publishing.
The checked-in workflow still uses full action commit SHAs and the publish path
still verifies signed tag identity and exact commit state, so this is not
evidence of a compromised artifact. It is nevertheless release-blocking drift.

Repository remediation completed:

- Desired state is represented in `.github/repo-settings`.
- `scripts/check-github-hardening.sh` permits reduced visibility only for the
  exact `Resource not accessible by integration` HTTP 403 response; rate
  limits, malformed/missing fields, HTTP 404/5xx, network failure, and visible
  drift fail closed.
- The check verifies `can_admins_bypass: false` and fails when bypass is
  enabled.
- The protected environment's reviewer type and numeric GitHub ID are tracked
  exactly, so reviewer substitution is detected and reconciled.
- `scripts/apply-github-hardening.sh` can reconcile every automatable drift
  above, including the required-check inventory.
  GitHub exposes administrator bypass for reads, but its supported REST and
  GraphQL update inputs do not accept the field, so that setting remains a
  manual UI operation.

Required closure: apply the hosted baseline with explicit authorization,
disable `pypi` administrator bypass in GitHub's settings UI, and rerun the
fail-closed check successfully before release.

### BP-002 — Some output installation paths are not crash-atomic (Low, accepted)

Default JSON output remains no-clobber and prepares a complete randomized
temporary file. It atomically hard-links that file into place when supported.
On filesystems without hardlinks it uses an exclusive-create copy;
`--overwrite` of an existing single-link regular file uses a
descriptor-verified in-place rewrite so a same-user path race cannot swap in a
symlink, socket, FIFO, or device node.

Impact: a process crash, power loss, or forced termination during either the
exclusive-copy fallback or an in-place overwrite can leave a partial report.
Handled errors and interrupts clean up the fallback, but cannot repair a
process killed mid-write.

Operational guidance: accept output only after PingHue exits successfully and
the JSON validates, then rotate it separately. The README, changelog, and threat
model state both tradeoffs explicitly.

### BP-003 — New target cap requires a major release (Compatibility gate)

Runs now reject more than 5,000 unique targets and retain at most 100,000 recent
samples across all targets. The bounds prevent unbounded task/history growth,
but the new target limit is an incompatible CLI behavior. The changelog invokes
the narrowly scoped resource-safety exception to the deprecation window and
requires the next major release; it must not ship as a patch or minor version.

## Resolved findings

| Area | Resolution |
| --- | --- |
| TCP evidence accuracy | Records connect latency before close, suppresses teardown resets after success, and preserves the primary-address failure when all failovers fail. |
| Cancellation | Absolute duration and signal deadlines include startup work, cancel active resolution/probe work, preserve ordered placeholders/sample budgets, and print probes completed just before shutdown. |
| ICMP concurrency | Uses a bounded reusable daemon worker pool with capacity recovery instead of serial one-thread-per-call behavior. |
| Long-run state | Maintains whole-run counters/statistics, latches loss and peak jitter, and keeps consecutive-failure classification beyond the retained tail. |
| Resource bounds | Caps unique targets at 5,000, per-target retained samples at 1,000, and run-wide retained samples at 100,000. |
| DNS behavior | Uses monotonic cooldown timing and refreshes stale results after repeated all-address failures. |
| Output paths | Refuses symlinks, sockets, unsupported nodes, and multiply linked regular files; verifies direct character-device/FIFO descriptors, restores blocking FIFO writes, sanitizes resolved addresses, and cleans fallbacks on handled interrupts. |
| Launcher metadata | Validates editable-install metadata and local URLs, handles malformed/non-UTF-8 metadata cleanly, and removes failed fallback paths. |
| Doctor guidance | Reports the actual ICMP datagram-socket check and gives platform-specific, least-privilege remediation. |
| TUI/site consistency | Keeps whole-run intermittent state, loss/refusal history, legends, thresholds, example percentages, and copy feedback synchronized. |
| Release identity | Verifies signed annotated tag name, direct commit target, event SHA, protected-main ancestry, and package version before building. |
| Exact-commit release validation | Reruns Ruff, Mypy, and coverage-gated tests against the exact tagged commit before publishing. |
| Tag race | Revalidates tag name/signature/target after attestation immediately before PyPI and again before `gh release create --verify-tag`. |
| Dependency integrity | Uses universal hash-pinned development/build/audit locks; required Python 3.10/3.13 jobs audit all three exact files before merge and publish. |
| Artifact reproducibility | Incrementally bounds/validates sdist members and canonicalizes bytes; required CI builds from two independent source trees with different mtimes and compares wheel/sdist bytes. |
| Artifact execution | The tag workflow installs and runs both exact distributions against localhost TCP and blocks when the staged Homebrew version/SHA differs from the built sdist. |
| Sdist smoke build | Installs the exact hash-pinned build backend before the no-build-isolation source-distribution smoke test. |
| Hosted hardening | Fails closed on unclassified API errors, missing fields, reviewer substitution, bypasses, rule/status inventory drift, and status-check source changes. |
| Sdist contract | Excludes repository-only hardening/normalizer tests and maintainer-only contribution/release/hardening documents whose tooling is intentionally absent. |

## Secure defaults observed

- Runtime inputs are finite, range checked, and length bounded before task
  creation.
- Host-file reads use no-follow/descriptor validation, reject special files,
  and enforce byte, line, target-length, and merged-target limits.
- DNS and network work use bounded concurrency and timeouts; per-target failures
  are contained instead of terminating the run.
- Operator-controlled terminal and JSON strings are centrally sanitized.
- Reports default to owner-only mode and no-clobber creation.
- Runtime code has no `subprocess`, `os.system`, `eval`, `exec`, pickle,
  unsafe YAML, XML, SQL, HTTP server, or inbound-listener sink.
- CI uses read-only default permissions and non-persisted checkout credentials.
- External actions are pinned to full 40-character commit SHAs.
- PyPI OIDC/attestation capability and GitHub repository-write capability are
  isolated in separate jobs.
- Build backend versions and CI/release dependencies are hash pinned.
- The static website is self-contained and deploy permissions are scoped to the
  deploy job.

## Verification evidence

- Full tests outside the sandbox: 325 passed; localhost TCP and Unix-socket
  cases executed; total branch-aware coverage was 88.15% against an 80% floor.
- Ruff: all files passed.
- Mypy strict mode: no issues across 14 runtime modules plus the sdist
  normalizer script.
- Python bytecode compilation: all runtime and script modules passed.
- Static site contract: 2 Node tests passed.
- Real-browser validation: desktop accessibility structure rendered, copy
  feedback reached `Copied`, console had zero errors/warnings, and a 390 px
  viewport had no horizontal overflow.
- Semgrep explicit Python, security-audit, and secrets rules: 0 findings across
  the tracked repository and explicit new-file scan.
- Bandit recursive runtime/script scan: 0 findings.
- Gitleaks current working tree and 70-commit history scans: 0 leaks.
- `pip-audit --strict --disable-pip`: no known vulnerabilities in
  `requirements.txt`, `requirements-build.txt`, or
  `requirements-audit.txt`.
- Fresh temporary-environment installation accepted every applicable
  `--require-hashes` entry.
- Two builds from independent source trees with different mtimes produced byte-identical
  normalized sdists and byte-identical wheels with `SOURCE_DATE_EPOCH=0`.
- The final normalized sdist and wheel passed `twine check`; all 243 tests
  shipped in the unpacked sdist passed; fresh wheel and sdist installs both
  reported `pinghue 4.0.0` through the installed launcher.
- ShellCheck and Bash syntax checks passed for shell scripts; Homebrew formula
  Ruby syntax passed.
- The live hosted-hardening check correctly failed on current drift; it is not
  reported as passing.

## Release decision

Local code, tests, locks, and deterministic artifact controls passed the
release-preparation checks. Publishing remains blocked until BP-001 is
corrected and the breaking target cap is assigned a new major version.

Revisit this report if PingHue gains a listener, privileged wrapper, remote
inventory source, credential handling, plugin execution, subprocess probes, a
new serialization format, or additional release principals.
