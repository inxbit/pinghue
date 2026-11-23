# PingHue Security Best-Practices Report

## Status

Review date: 2026-07-11; audited again on 2026-11-19; reviewed again on 2026-11-23; verification evidence refreshed for 8.0.2 on 2026-11-23

No open critical or high-severity vulnerability was found in the local
CLI/runtime code. The reviewed implementation is a local, unprivileged
ICMP/TCP monitoring tool: it opens no inbound service, handles no credentials,
uses no runtime shell execution, and has no authentication, database, template,
or unsafe-deserialization surface.

No release-blocking hosted-hardening finding remains open. The checked-in
baseline was applied to GitHub, administrator bypass was disabled manually on
the protected `pypi` environment, and the administration-readable fail-closed
check passed on 2026-07-11.

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

## Findings and residual risk

### BP-001 — Hosted release/security settings drift (Medium, resolved 2026-07-11)

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

Closure evidence: the tracked baseline was applied with explicit authorization,
the `pypi` environment's administrator bypass was disabled in GitHub's settings
UI, and `scripts/check-github-hardening.sh inxbit/pinghue` passed with
repository-administration visibility on 2026-07-11.

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

### BP-003 — Target cap required a major release (Compatibility gate, shipped in 5.0.0)

Runs reject more than 5,000 unique targets and retain at most 100,000 recent
samples across all targets. The bounds prevent unbounded task/history growth,
but the target limit was an incompatible CLI behavior. The changelog invoked
the narrowly scoped resource-safety exception to the deprecation window, and
the cap shipped in the 5.0.0 major release rather than a patch or minor version.

### BP-004 — An inherited macOS ACL outlasts the private report mode (Low, accepted 2026-11-21)

`--output-mode private` sets the report's permission bits to `0600`. On macOS a
directory can carry an inheritable access control list; a report created there
inherits it, and an entry that grants other users read access is not removed
by the mode. Found in the 2026-11 audit and measured: a report created under a
directory with `everyone allow read,file_inherit` showed mode `0600` and the
inherited entry. Reading it from a second account was not tested.

Linux is not affected in the same way: there the mode is the mask of a POSIX
ACL, so an inherited entry grants nothing to a `0600` file. A test, run in CI
on Linux, creates a private report under a directory whose inheritable ACL
gives another account read access: that account can read a file created
beside it with group permission bits, and cannot read the report. Network
filesystems with their own ACL model (NFSv4, SMB) were not tested.

Impact: only where the operator, or whoever controls the directory, configured
such an ACL; default macOS directories do not grant inherited read access.

Operational guidance: keep evidence in a directory that only the operator
controls, ACL included (`ls -le` shows the entries). The README and the threat
model state this.

Decision: the owner accepted this risk on 2026-11-21. It stays documented; the
code neither removes nor refuses such an ACL. Removing it would add
macOS-only code for a setting the directory's owner chose, and refusing the
directory would break scripts that write there.

### BP-005 — A descriptor opened before `--overwrite` keeps reading the report (Low, documented)

`--overwrite` rewrites the existing file in place: the inode is kept, its mode
is set to `0600` in private mode, and the new report is written into it. The
mode is checked when a file is opened, not when it is read. A process that
opened the file earlier, while its mode allowed that, keeps its descriptor and
reads the new report. Found in the second review of 2026-11-23 and measured
with one account: a descriptor opened on a `0644` file read the new JSON after
the rewrite, with the file at mode `0600` and the same inode. Reading through a
descriptor held by a second account was not tested; it follows from the same
rule.

Impact: only when a private report is written over a file that other users
could read before, for example one created with `--output-mode umask`, and
one of them still holds it open. A report pinghue created in private mode was
never readable by others, so overwriting it does not expose anything.

Operational guidance: before writing a private report to a path whose file
other users could ever read, remove that file instead of overwriting it. The
README and the threat model state this.

Status: documented in 8.0.2, with no change in behavior. Refusing to overwrite
a file that group or other users can read would stop runs that work today, so
it is a decision for the owner and has not been made.

## Resolved findings

| Area | Resolution |
| --- | --- |
| TCP evidence accuracy | Records connect latency before close (on Linux, the kernel handshake RTT unless the SYN was retransmitted), closes the socket without waiting for teardown after success, and preserves the primary-address failure when all failovers fail. |
| Cancellation | Absolute duration and signal deadlines include startup work, cancel active resolution/probe work, preserve ordered placeholders/sample budgets, and print probes completed just before shutdown. |
| ICMP concurrency | Runs every ICMP probe on the event loop over a few shared non-blocking datagram sockets per address family (no per-probe threads or file descriptors); replies must match sequence, source address, and a random per-probe token, so other ICMP traffic on the host cannot mark a down target up. |
| Long-run state | Maintains whole-run counters/statistics, latches loss and peak jitter, and keeps consecutive-failure classification beyond the retained tail. |
| Resource bounds | Caps unique targets at 5,000, per-target retained samples at 1,000, and run-wide retained samples at 100,000. |
| DNS behavior | Uses monotonic cooldown timing and refreshes stale results after repeated all-address failures. |
| Output paths | Refuses symlinks, sockets, unsupported nodes, and multiply linked regular files; verifies direct character-device/FIFO descriptors, restores blocking FIFO writes, sanitizes resolved addresses, and cleans fallbacks on handled interrupts. |
| Output ownership | Refuses FIFOs and `--overwrite` targets that are not owned by the effective user, as observed and as opened, so a path pre-created by another local user in a shared sticky directory cannot receive the report; in the default `private` output mode also refuses a FIFO whose permission bits give group or other users access (such as the `0644` that `mkfifo` creates under umask `022`), except a pipe named by a macOS `/dev/fd/N` path, such as process substitution passes, while a named FIFO behind such a path is judged by its own bits; character devices remain allowed except the driverless Linux `0:0` whiteout device, the only one an unprivileged user can create. The CLI applies the same rules before probing starts, so a refused path fails at the start of the run, not at exit. |
| Host-file lines | Splits only on `\n`, `\r\n`, or a lone `\r`, so control and Unicode line separators stay inside the value for target validation instead of creating extra targets or skewing line numbers and the line limit. |
| Target strings | Refuses a target that contains a space, on the command line and in a host file, so target text cannot read as extra fields of a line-output line; control characters were already refused and every other non-ASCII character is shown escaped. |
| Doctor output | Escapes OS release metadata, the interpreter path, and ICMP socket and probe errors before printing them. |
| Workflow scripts | Workflow `run:` steps receive values through environment variables only; tests reject inline `${{ }}` expressions in scripts, privileged pull-request/workflow-run triggers, dependency caches, and non-read default token permissions. The hosted-hardening check keeps API responses in a private temporary directory. |
| Launcher metadata | Validates editable-install metadata and local URLs, handles malformed/non-UTF-8 metadata cleanly, and removes failed fallback paths. |
| Doctor guidance | Reports the actual ICMP datagram-socket check and gives platform-specific, least-privilege remediation. |
| Parser messages | Usage errors that argparse words itself (unknown or ambiguous options) are escaped like PingHue's own messages, so an option carrying terminal control characters does not reach stderr as typed. |
| Standard streams | A stdout closed at startup, a stdout that cannot encode the doctor report, and a file that refuses a write end with the documented status instead of a traceback or status 120; `EIO` on a regular file is reported as a write error, while a hung-up terminal is still a reader that went away. |
| Output modes and names | The no-hardlink fallback applies the report mode after creating the file, so the private mode does not depend on the umask; an empty path is a usage error, and a name the filesystem cannot hold is refused before probing. |
| IPv6 zones | A link-local target's zone reaches the ICMP socket as its scope id, and a zone the resolver reports is kept, so such hosts are probed instead of always timing out. |
| TUI failure | A TUI that ends on an unhandled exception exits `1` and records `exit_reason` `error`, even when a deadline, a completed `--count`, or a signal finishes the run afterwards. |
| TUI/site consistency | Keeps whole-run intermittent state, loss/refusal history, legends, thresholds, example percentages, and copy feedback synchronized. |
| Release identity | Verifies signed annotated tag name, direct commit target, event SHA, protected-main ancestry, and package version before building. |
| Exact-commit release validation | Reruns Ruff, Mypy, and coverage-gated tests against the exact tagged commit before publishing. |
| Tag race | Revalidates tag name/signature/target after attestation immediately before PyPI and again before `gh release create --verify-tag`. |
| Dependency integrity | Uses universal hash-pinned development/build/audit locks; required Python 3.11/3.13 jobs audit all three exact files before merge and publish. |
| Artifact reproducibility | Incrementally bounds/validates sdist members and canonicalizes bytes; required CI builds from two independent source trees with different mtimes and compares wheel/sdist bytes. |
| Artifact execution | The tag workflow installs and runs both exact distributions against localhost TCP and blocks before PyPI when the staged Homebrew version/SHA differs from the built sdist or the sdist's changelog has no section for the version. |
| Artifact identity | The build job publishes the SHA-256 of both distributions as job outputs; the check, publish, and release jobs compare what they downloaded against them before using it. |
| Sdist smoke build | Installs the exact hash-pinned build backend before the no-build-isolation source-distribution smoke test. |
| Hosted hardening | Fails closed on unclassified API errors, missing fields, reviewer substitution, bypasses, rule/status inventory drift, and status-check source changes; the tracked baseline and manual administrator-bypass control were reconciled and verified live on 2026-07-11. |
| Sdist contract | Excludes repository-only hardening/normalizer tests and maintainer-only contribution/release/hardening documents whose tooling is intentionally absent. |

## Secure defaults observed

- Runtime inputs are finite, range checked, and length bounded before task
  creation.
- Host-file reads use no-follow/descriptor validation, reject special files,
  and enforce byte, line, target-length, and merged-target limits.
- DNS and network work use bounded concurrency and timeouts; per-target failures
  are contained instead of terminating the run.
- Operator-controlled terminal and JSON strings are centrally sanitized.
- Reports default to owner-only mode and no-clobber creation, and are never
  written into a FIFO or rewritten file owned by another user, nor, in the
  default private mode, into a FIFO that other users can open.
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

Refreshed for the 8.0.2 release candidate on 2026-11-23 (the 8.0.1 evidence
this section previously recorded is superseded):

- Audit of 2026-11-19: two independent reads of `src/` and the scripts (an
  OpenAI Codex read-only audit split into four areas, and a separate review),
  each finding reproduced before it was accepted. No critical or high finding.
  19 defects were fixed test-first in 7.1.2, one more in 8.0.0, and the last
  two that needed no decision in 8.0.1. The inherited ACL that BP-004
  documents was accepted by the owner.
- Second review of 2026-11-23: one more full read of the 15 runtime modules,
  the launcher, the workflows and the sdist normalizer, made before reading
  the earlier findings, then about 45 hermetic hand runs on macOS. No critical
  or high finding. Three results, all in this release. A stop signal repeated
  while the report was built lost the report: measured on 8.0.1 with 1,000
  hosts, a second SIGINT 0.03 to 0.15 s after the first gave exit status 130,
  a traceback, and no report; it is now held back for the first five seconds
  of writing the report. The TUI search could not find an IPv6 literal target
  written in a long form. And BP-005, documented only. A Codex pass over the
  fix diff found that the first version of the signal fix, which ignored the
  signals only for report files, could not be interrupted when a FIFO
  appeared in between or a filesystem stalled; the grace period replaced it
  before merge. The final version was checked by its tests, by hand runs and
  by CI, not by a second Codex pass. Not closed: a signal in the instant
  between the end of probing and the start of the report, and one after the
  report is written, while the process exits; the changelog names both.
  Linux-only paths were read, not run by hand; CI runs the suite on Linux.
- Fuzzing with fixed harness seeds, rerun on this release, which changes no
  parser: 778,464 datagrams and 173,494 Linux error-queue messages through an
  in-memory socket on both platform branches of the ICMP engine, with no
  probe completed by a datagram that was not its reply or a matching error;
  3,000 host files, 9,000 argument vectors, 9,000 display strings, 3,000
  host-name sets, 3,000 resolver answers, and 1,000 reports checked against
  the schema. No accepted target among the 9,000 vectors contains a space.
- Full tests: 812 passed and 9 skipped (Linux-only kernel features, a
  Linux-only pty hangup, a Linux-only ACL case, and a root-only ownership
  change) under CPython 3.12.16, with total branch-aware coverage of 94.74%
  against the 85% floor; localhost TCP and Unix-socket cases executed. The
  same suite passed under CPython 3.11.15, the supported floor, and 3.14.10,
  each from fresh `--require-hashes` installs. CI ran it on Linux and macOS
  under Python 3.11 to 3.14.
- Ruff: `ruff check` and `ruff format --check` passed for all files.
- Mypy strict mode: no issues across 17 runtime modules plus the sdist
  normalizer script.
- Python bytecode compilation: all runtime and script modules passed.
- Static site contract: 28 Node tests passed. The 8.0.2 site change updates
  the version strings.
- Semgrep 1.186.0 Python, security-audit, and secrets rule packs: 0 findings
  across 88 scanned files.
- Bandit 1.9.4 recursive runtime/script scan: one low-severity B311 note for
  the `random.uniform` jitter on DNS retry delays, which is not a security
  value; ICMP identifiers, sequence numbers, and reply tokens use
  `os.urandom`. No medium or high findings.
- Gitleaks 8.30.1 working-tree and 180-commit history scans: 0 leaks.
- `pip-audit --strict --disable-pip`: no known vulnerabilities in
  `requirements.txt`, `requirements-build.txt`, or
  `requirements-audit.txt`.
- Fresh temporary-environment installation accepted every applicable
  `--require-hashes` entry.
- Two builds from independent source trees with different mtimes produced byte-identical
  normalized sdists and byte-identical wheels with `SOURCE_DATE_EPOCH=0`. The
  same procedure applied to the previous release's tag reproduced the
  published 8.0.1 wheel and sdist byte for byte.
- The final normalized sdist and wheel passed `twine check`; all 713 tests
  shipped in the unpacked sdist passed against its own sources (9 skipped as
  above); fresh wheel and sdist installs both reported `pinghue 8.0.2`
  through the console-script entry point and completed the localhost TCP
  smoke test, choosing line output without `--no-tui` because stdout was a
  pipe.
- ShellCheck and Bash syntax checks passed for shell scripts; Homebrew formula
  Ruby syntax passed.
- The administration-readable hosted-hardening check passed against the
  tracked baseline; this release changes no hosted setting.

## Release decision

The breaking target cap shipped in 5.0.0 and BP-001 is closed. 8.0.2 is a
patch release with two fixes from the second review of 2026-11-23: a stop
signal repeated while the report is written no longer loses the report, and
the TUI search finds an IPv6 literal target written in a long form. BP-004
and BP-005 remain documented only. The release adds no input source, output
path, privilege, or network behavior, and changes no flag, line output, or
JSON content. One behavior changes on purpose: a stop signal in the first
five seconds of writing the report is dropped instead of ending the process.
The runtime dependencies are unchanged. Its release PR changes no workflow or
packaging step. The publish workflow's changelog, artifact-digest, and
staged-formula steps were run locally against the candidate, with the
workflow's own script text, before tagging.
The local candidate passed the security, QA, deterministic-artifact,
installed-artifact, and staged-formula gates. Publishing remains gated on
release-PR review and exact-head CI.

Revisit this report if PingHue gains a listener, privileged wrapper, remote
inventory source, credential handling, plugin execution, subprocess probes, a
new serialization format, or additional release principals.
