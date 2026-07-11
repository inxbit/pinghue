# PingHue Threat Model

## Executive summary

PingHue is a local, unprivileged Python CLI/TUI that performs outbound ICMP or
TCP probes and optionally writes a JSON report. It does not expose a listener,
remote API, authentication system, database, or multi-tenant service boundary.
The principal security concerns are trustworthy operator-visible evidence,
bounded processing of target inventories, safe local-file handling, least-
privilege ICMP guidance, and integrity of the release path.

The runtime controls the highest-value abuse paths by sanitizing rendered and
exported text, bounding target and retained-sample counts, containing resolver
and probe failures, opening input/output paths by descriptor, defaulting JSON
reports to no-clobber mode, and keeping release credentials narrowly scoped.
One intentional output tradeoff remains: explicit overwrite and the
no-hardlink exclusive-copy fallback are type-safe but not crash-atomic.

## Scope and assumptions

In scope:

- Runtime code under `src/pinghue`.
- CLI, TUI, no-TUI, doctor, host-file, DNS, ICMP/TCP, and JSON-output paths.
- Packaging, CI, dependency audit, release, Pages, and repository-hardening
  controls represented in this repository.
- User and maintainer security guidance.

Out of scope:

- The security of remote hosts selected by the operator.
- Organization-level GitHub, PyPI, DNS, and Homebrew settings that are not
  represented in the repository.
- Local virtual environments, caches, and previously built artifacts.
- Decisions about whether an operator is authorized to probe a target.

Assumptions:

- PingHue runs as the invoking local user, not as root, setuid, or a daemon.
- Targets and paths are selected by the operator, although target lists may
  contain text copied from semi-trusted operational sources.
- The operator has normal filesystem permissions for selected input/output
  paths.
- Before release, maintainers apply and verify the checked-in GitHub hardening
  baselines with an administration-readable token.
- Release security requires administrators to be manually prevented from
  bypassing the protected `pypi` environment. GitHub exposes the field for
  verification but not through its supported update inputs. The 2026-07-10
  live check found bypass enabled, so publishing is blocked until corrected.

Open questions:

- Whether future integrations will ingest target inventories from untrusted
  customer or Internet-facing sources.
- Whether future packaging will add a privileged wrapper, background service,
  or remotely callable interface.

## System and trust boundaries

| Boundary | Data crossing it | Main controls |
| --- | --- | --- |
| Operator shell -> CLI parser | Targets, timing, ports, concurrency, output path, labels | Type/range/finite checks; 253-character target limit; 5,000 unique-target cap |
| Local file -> host parser | UTF-8 target list | `O_NOFOLLOW` when available; descriptor type check; 1 MiB and 5,000-line limits; BOM/error handling |
| Target -> OS resolver | Hostname or numeric address | Numeric-only mode; bounded/cancellable resolution; DNS concurrency cap and cooldown |
| Resolved address -> network | ICMP datagram or TCP connection | Outbound-only probes; bounded concurrency/timeouts; per-target exception containment |
| Probe/library errors -> terminal | Target, address, status, error text | Central display sanitization; literal escaping of controls and non-ASCII text |
| Run state -> JSON path | Metadata, statistics, recent samples | 100,000-sample run-wide retention cap; private mode; descriptor-verified path handling; schema tests |
| Git tag -> release workflow | Source commit and package artifacts | Signed annotated-tag identity/target checks; main ancestry; exact-commit validation; protected environment; OIDC; attestations |
| Dependency metadata -> package indexes | Locked requirements and advisory queries | Hash-enforced installs; narrow runtime ranges; Dependabot and scheduled audits |
| Docs source -> GitHub Pages | Static HTML/CSS/JS and assets | Pinned actions; read-only build job; scoped deploy permissions; static contract tests |

## Assets and objectives

| Asset | Objective |
| --- | --- |
| Terminal output and captured logs | Prevent control-sequence injection and misleading target/error rendering |
| JSON maintenance evidence | Preserve schema validity, completeness, provenance context, and safe path behavior |
| Operator workstation | Avoid privilege expansion, unintended file replacement, and unbounded local resource use |
| Monitoring availability | Degrade individual targets to explicit failures instead of crashing the run |
| Package artifacts | Bind published wheel/sdist files to the intended reviewed commit and signed tag |
| Hosted release controls | Detect drift in branch, tag, environment, Actions, security, and Pages settings |

## Attacker model

An attacker or mistake may:

- Influence a hostname, host-file line, DNS answer, or socket/library error.
- Supply an oversized or malformed local inventory through an operator workflow.
- Race an output path as the same local user.
- Cause timeouts, refused connections, partial address failures, or resolver
  failures intended to distort status evidence.
- Compromise a dependency or attempt to exploit weak release/tag permissions.
- Convince an operator to run as root or apply overly broad ICMP privileges.

The current product does not give an attacker a remotely reachable PingHue
service, an authentication bypass, a command shell, unsafe deserializer, or
privilege boundary. A same-user attacker who can freely modify the operator's
files or process environment already shares that user's authority.

## Primary abuse paths

### 1. Terminal or report evidence manipulation

An influenced target or OS error could contain escape sequences, bidirectional
controls, zero-width characters, or confusable Unicode. PingHue routes target,
host-label, address, and error strings through display sanitization before
terminal rendering or JSON export. New output paths must preserve that invariant.

### 2. Inventory or history resource exhaustion

Large target sets can multiply resolver tasks, probe workers, and retained
samples. Host files are size/line bounded, the merged CLI inventory is capped
at 5,000 unique targets, probe and resolver concurrency are bounded, ICMP work
uses a reusable daemon pool, and retained history is limited to 100,000 samples
across the run. Whole-run counters/statistics do not retain every sample.

### 3. Host-file path confusion

A symlink, FIFO, device, socket, oversized file, or invalid UTF-8 file could
block or redirect parsing. The parser opens without following symlinks where
supported, verifies the opened descriptor is a regular file, performs bounded
reads, and reports encoding errors without a traceback.

### 4. Output-path race or partial evidence

Default output uses a randomized same-directory temporary file and a no-clobber
install. It atomically hard-links the complete report when the filesystem
supports that operation. Otherwise it uses an exclusive create and copy;
handled failures clean up, but an abrupt termination can leave a partial new
file. Symlinks, sockets, unsupported special nodes, and multiply linked regular
files are not replaced. Existing character devices and FIFOs are written only
after the opened descriptor matches the observed node.

Explicit `--overwrite` of an existing single-link regular file keeps the
verified inode and rewrites it in place. This closes replacement races against
special nodes, but an abrupt process or system failure during the final write
can leave a partial report. Evidence-preserving workflows should accept output
only after a successful exit and JSON validation, then rotate it separately.

### 5. Misleading network classification

DNS replicas and multi-address hosts can fail inconsistently. PingHue retains
the primary error when all failover addresses fail, refreshes stale DNS after
repeated failures, prioritizes working addresses, distinguishes TCP refusal
from timeout, computes statistics over the whole run, and latches observed
loss/high-jitter excursions for final classification. External DNS and routing
state remain outside the tool's control.

### 6. Unsafe ICMP privilege remediation

Operators may reach for root, broad `ping_group_range`, or capabilities on a
shared interpreter. Doctor guidance is platform-specific, tests the actual ICMP
datagram-socket path, recommends a group-specific Linux range, and offers TCP
mode without special privileges.

### 7. Release-path compromise

A tag-triggered workflow can publish trusted artifacts. The build job verifies
the package version, annotated tag name, cryptographic verification, direct
commit target, event SHA, and protected-main ancestry before building. A
separate job reruns Ruff, Mypy, and coverage-gated tests on the exact tagged
commit, while a Python 3.10/3.13 matrix audits all three hash locks. Publishing
uses a protected environment, OIDC, and artifact attestations without
repository-write permission, and revalidates the live tag identity and target
immediately before PyPI. Both exact artifacts are installed and exercised
against localhost TCP, and the staged Homebrew source version/SHA must match the
built sdist. The later GitHub-release job revalidates the tag again and uses
`--verify-tag` before receiving `contents: write`. Source distributions are
normalized into a canonical safe archive, and a required CI job builds from two
independent pristine source trees with different mtimes before comparing both
wheel and sdist bytes.

Checked-in ruleset and environment files are desired-state templates, not proof
of current hosted state. The manual pre-release hardening check is fail-closed.
The scheduled read-only check has explicitly documented reduced visibility and
may warn only when an administration endpoint returns HTTP 403 with the exact
`Resource not accessible by integration` response. Missing fields, rate limits,
malformed responses, and other HTTP/network failures remain blocking.

### 8. Dependency or build-tool compromise

Runtime dependencies have constrained ranges. CI/release dependency installs
use hash-pinned lock files, dependency audit covers development, build, and
audit locks, external actions are pinned to full commit SHAs, and build
provenance is attested. Advisory databases and package-index availability are
external dependencies, so fresh audits remain a release gate.

## Risk register

| ID | Threat | Existing controls | Residual risk | Priority |
| --- | --- | --- | --- | --- |
| TM-001 | Terminal/control-character injection | Central sanitization and adversarial rendering tests | A future output caller could omit sanitization | Low |
| TM-002 | Unbounded target/probe/sample use | File, target, concurrency, timeout, and retention caps | 5,000 targets can still be intentionally expensive | Low |
| TM-003 | Host-file symlink/special-file race | Descriptor-based open/type validation and bounded reads | Same-user content modification remains possible | Low |
| TM-004 | Output replacement or partial report | No-clobber default, randomized temp, inode/type/link checks | Explicit overwrite and no-hardlink copy fallback are not crash-atomic | Medium |
| TM-005 | Misclassification from DNS/socket behavior | Failover, cooldown refresh, ordered errors, whole-run stats | Remote routing/DNS state is inherently external | Low |
| TM-006 | Overbroad ICMP privileges | Unprivileged design and platform-specific doctor guidance | Operators can ignore guidance | Medium |
| TM-007 | Unauthorized or wrong-commit release | Signed tag checks, exact-commit validation, OIDC, attestations, split permissions | Hosted settings can drift; administrator-bypass setting is manual | Medium |
| TM-008 | Vulnerable/malicious dependency | Hash locks, narrow ranges, Dependabot, three-lock audit | New advisories and index compromise remain external | Medium |
| TM-009 | Static-site supply-chain change | Static assets, contract test, pinned actions, scoped deploy job | Compromised maintainer review remains possible | Low |

## Required security invariants

- PingHue remains unprivileged and opens no inbound listener.
- Every operator-controlled rendered/exported string is sanitized.
- Target, file, resolver, probe, and retained-sample bounds stay enforced.
- Host input and existing output nodes are validated by opened descriptor.
- Existing output files remain no-clobber by default.
- Release actions remain full-SHA pinned and checkout credentials are not
  persisted.
- PyPI OIDC and repository-write capability remain in separate jobs.
- The exact tagged commit passes the release validation suite before publish.
- Hosted hardening is manually checked with adequate read permission before
  every release.
- The `pypi` environment's administrator bypass is disabled manually and
  verified before release; its exact required-reviewer identity also matches
  the checked-in baseline. Current live drift must be corrected first.

## Review triggers

Revisit this model before adding a service/listener, remote inventory ingestion,
credentials, privileged execution, plugins, subprocess-based probes, a new
serialization format, broader output-file replacement, new package registries,
additional maintainers, or material release-workflow changes.
