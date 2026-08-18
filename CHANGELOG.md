# Changelog

All notable changes to this project are documented here.

Starting with `1.0.0`, this project follows semantic versioning. CLI flags and JSON output are compatibility contracts; breaking JSON changes increment `schema_version`.

## [Unreleased]

### Changed

- The `pinghue` command is now a standard console-script entry point (`pinghue.cli:main`) instead of the `scripts/pinghue` bootstrap file; `scripts/pinghue` remains in the repository as a development launcher for editable checkouts.
- `--help` now describes every option and shows the default for `--interval`, `--timeout`, `--concurrency`, `--jitter-threshold`, `--fail-threshold`, `--history-style`, and `--host-label`.
- Documented `--history-style sparkline` as an alias of `bar`; the two styles have always rendered the same glyphs.
- No-TUI mode now runs the same per-target probe loops as the TUI instead of lockstep batches: a slow or timing-out target no longer delays the other targets' probes, and each per-probe line prints as soon as its result lands, so line order across targets follows completion order.

### Deprecated

- Starting the TUI while stdout is not a terminal now prints a warning on stderr. A future major release will default to `--no-tui` in that case; pass `--no-tui` explicitly in scripts and cron jobs.

## 5.0.0 - 2026-07-11

### Breaking

- Limited each run to 5,000 unique targets and reduced per-target sample tails uniformly when needed to keep retained history within 100,000 samples total; whole-run statistics remain unbounded counters. The target cap invokes the documented resource-safety exception to the deprecation window and requires the next major release.
- Changed `--overwrite` to refuse multiply linked regular files and perform a descriptor-verified, non-crash-atomic in-place rewrite instead of replacing the inode; workflows that require crash-atomic evidence rotation should write a new report and rotate it only after PingHue exits successfully.

### Security

- Hardened the release path so only a verified signed annotated tag for the checked-out commit, reachable from protected `main`, can publish; release checks now install Twine from the hash-pinned development lock.
- Expanded hosted-hardening drift checks and apply support to cover ruleset bypasses and status-check sources, exact PyPI deployment/reviewer policies, repository security features, GitHub Actions permissions, and the default branch; unclassified 403s and missing fields now fail closed.
- Hash-pinned the universal development/CI lock and made source distributions deterministic through safe archive normalization plus a required two-build byte comparison.
- Preserved symlinks, FIFOs, Unix sockets, and other special output paths even when `--overwrite` is requested; direct character-device/FIFO writes now verify the opened inode.
- Made output cleanup cover interrupts and documented that an explicit overwrite of an existing single-link regular file is a verified in-place rewrite, not a crash-atomic replacement.
- Documented that no-clobber creation falls back to a non-crash-atomic exclusive copy on filesystems without hardlink support; handled failures still remove the incomplete path.
- Validated editable-install metadata structure and local `file://` URLs before adding fallback import paths.

### Fixed

- Restored real ICMP concurrency with bounded reusable daemon workers, including safe capacity recovery when worker startup fails.
- Made `--duration` an absolute run deadline that includes startup work in both modes, and made SIGINT/SIGTERM cancel active no-TUI resolution and probe batches promptly while preserving requested targets in final output.
- Preserved line output for probes that completed just before a no-TUI interruption or deadline.
- Kept consecutive-failure classification correct when `--fail-threshold` exceeds the retained sample window, and kept any observed loss intermittent even when its percentage rounds to `0.0`.
- Refreshed stale DNS results after repeated all-address failures on a consistent cooldown that no longer depends on machine uptime.
- Preserved the first-address failure when all failover addresses are down, kept successful TCP connect latency when close teardown resets, and measured latency before teardown.
- Corrected TCP-refused history styling, static-site JSON field examples, and clipboard failure feedback.
- Kept FIFO report writes blocking after a verified nonblocking open so large JSON documents cannot be truncated at pipe capacity.
- Sanitized scoped resolved addresses in JSON evidence just like target/error text.
- Made the editable launcher reject malformed nested metadata without a traceback, clean up failed fallback paths, and preserve missing-dependency errors.

### Changed

- CI installs only hash-pinned dependency locks before installing the local project without dependency resolution, and dependency audit runs against the exact CI/build locks.
- Release publishing now reruns the coverage-gated validation suite on the exact tagged commit, audits all hash locks across supported marker boundaries, installs/smoke-tests both exact artifacts, checks the staged Homebrew SHA, and revalidates the protected signed tag immediately before both PyPI publication and GitHub release creation.
- Changed the fixed history slow-latency threshold from 300 ms to 100 ms; exactly 100 ms remains in the green `▅` bucket and values above it are amber.
- Release documentation now requires merging the release PR before signing and pushing a tag from the merged public `main` commit.

## 4.0.0 - 2026-07-08

### Breaking

- `--output -` now requires `--no-tui` and exits `2` (usage error) otherwise. The TUI owns stdout while it runs, so the combination could not produce machine-readable output; the `3.0.1` stderr warning for it is removed along with the behavior.

## 3.0.1 - 2026-07-08

### Changed

- Combining `--output -` with the TUI now prints a stderr warning pointing at `--no-tui`, since the JSON document lands on stdout after the TUI exits.

### Fixed

- `--no-tui --output -` no longer interleaves per-probe lines with the JSON document on stdout; the per-probe lines move to stderr so stdout stays machine-parseable.

## 3.0.0 - 2026-07-02

### Breaking

- `--fail-on-any-down`/`--fail-on-all-down` now exit `3` instead of `2`, so a down target is distinguishable from an argparse usage error (which also exits `2`). Automation checking for exit `2` must be updated. Requires a major version bump.
- Removed the `-4`/`-6` short aliases; use `--ipv4`/`--ipv6`. Options that look like negative numbers made argparse swallow negative values (`-c -3` reported "expected one argument" instead of the real validation message), so the aliases cannot be kept even as deprecated stubs. Targets starting with `-` are now rejected as invalid, so stale `-4`/`-6` usage fails fast instead of being probed as a hostname. Requires a major version bump.

### Security

- Capped DNS failover to the first 8 resolved addresses per target, so a hostname resolving to many dead addresses can no longer stretch one probe cycle to `addresses × timeout` while holding a concurrency slot.
- Split the GitHub release step of the publish workflow into its own job, so no single job holds PyPI trusted-publishing (`id-token`) and repository `contents: write` together.
- Hash-pinned the dependency-audit toolchain (`requirements-audit.txt`, installed with `--require-hashes`), matching the build pipeline.

### Fixed

- `pinghue --check` no longer hangs forever when the DNS resolver blackholes queries; the diagnostic lookup now times out after 5 seconds and reports a warning.
- The process no longer stalls on exit (up to 5 minutes) when a DNS lookup is stuck past its 5-second budget; resolver calls run on abandonable daemon threads instead of asyncio's default executor.
- Host files with a UTF-8 BOM (common for Windows-authored files) parse correctly instead of failing with a control-character error; invalid UTF-8 now reports a clear error instead of a raw codec message.
- `python -m pinghue` works (added `__main__.py`).
- Corrected Linux ICMP remediation guidance: `setcap cap_net_raw` on the `pinghue` launcher script is a no-op because Linux ignores file capabilities on interpreter scripts; doctor, README, and Homebrew caveats now recommend `ping_group_range` or TCP mode.

### Changed

- `--output -` writes the JSON run summary to stdout instead of creating a literal file named `-`.

## 2.1.0 - 2026-05-31

### Security

- Sanitized the `--numeric` non-IP-literal error, host-file parse errors, and the top-level `OSError` message so operator- and host-file-supplied strings can no longer inject raw terminal control sequences via stderr.
- Rejected control characters in command-line targets and trimmed surrounding whitespace, matching host-file target handling.
- Stopped the host-file parser from blocking indefinitely when `--file` points at a FIFO; non-regular files are now rejected promptly.
- Stopped `--output` from following a symlink at the output path to a character device or FIFO, and closed the stat/write race with an `O_NOFOLLOW` open plus `fstat` re-check.

### Changed

- `jitter_ms` is now RFC 3550 interarrival jitter (smoothed mean absolute difference between consecutive latencies) instead of the latency standard deviation. Reported values differ from earlier releases. `--jitter-threshold` is interpreted against this metric.
- ICMP probing reports `unreachable` distinctly from `timeout` by reading ICMP destination-unreachable/time-exceeded replies through low-level sockets instead of `icmplib.ping`, which swallowed them.
- A target that never responds is classified `down` even when the run is shorter than `--fail-threshold`, so unreachable hosts can no longer exit `0` under `--fail-on-any-down`/`--fail-on-all-down`.
- `--numeric` now errors when combined with a conflicting `-4`/`-6` flag instead of silently ignoring the forced family.
- Added `--output-mode` to control `--output` file permissions: `private` (`0600`, owner-only; default) or `umask` (honor the process umask). Report files default to owner-only `0600`.
- TCP `refused` history cells render red to match the `down` state badge.
- `--count` and `--duration` are documented as intentionally unbounded.

### Fixed

- ICMP thread-pool shutdown no longer blocks the event loop on in-flight probes when quitting the TUI or interrupting `--no-tui`.
- The TUI "probe now" burst wakes the existing per-target loop instead of starting a concurrent probe that double-counted samples.
- The per-target TUI probe loop subtracts probe duration from the interval so cadence no longer drifts when `--timeout` is large.
- `--check` now verifies IPv6 ICMP capability (socket plus `::1` loopback) separately from IPv4.
- `--output` writes succeed on filesystems without hardlink support (e.g. exFAT, some FUSE mounts) while still refusing to clobber an existing file.

### Release and packaging

- The publish workflow fails when the pushed tag does not match the `pyproject.toml` version.
- The build toolchain is hash-pinned via `requirements-build.txt` and installed with `pip --require-hashes`; CI builds with the same toolchain, `--no-isolation`, and `SOURCE_DATE_EPOCH`.
- The publish package-check job now runs separately from the artifact-producing build job, so the build/upload path uses only the hash-pinned build toolchain.
- The checked-in Homebrew formula now targets the 2.1.0 sdist and refreshed runtime resources.
- The hosted repository hardening drift check now validates active branch/tag ruleset internals against the checked-in policy files, not just ruleset names.
- The dependency-audit workflow pins its bootstrap `setuptools`/`wheel` instead of installing them unpinned.
- GitHub releases are created with the built-in `gh` CLI instead of a third-party action.
- Added a fully-pinned `requirements.txt` lockfile so dependency (SCA) scanners can resolve the full package tree; it does not change the version ranges in `pyproject.toml`.

## 2.0.1 - 2026-05-27

### Security and stability

- Sanitized environment-doctor DNS diagnostic output so configured names and resolver errors cannot render terminal control sequences.
- Fixed TUI `--count` and `--duration` handling so bounded runs exit as requested.
- Fixed TUI reset actions so cleared targets recompute their status and stale errors are removed.

## 2.0.0 - 2026-05-26

### Breaking

- `--output PATH` no longer replaces an existing regular file by default. Existing reports are preserved and the command exits with a clear error. Add `--overwrite` to scripts that intentionally reuse the same JSON path.

### Security and stability

- Added strict finite-value validation for timing and jitter options.
- Added target, diagnostic resolve-name, host-label, and host-file target length caps.
- Hardened host-file reads with descriptor-based regular-file validation, symlink rejection, byte caps, line caps, and target-length caps.
- Escaped operator-visible controls and non-ASCII characters in terminal and JSON output to reduce terminal injection and Unicode confusable risks.
- Added DNS lookup throttling, bounded DNS lookup timeouts, DNS retry cooldowns, working-address prioritization, and per-target resolver/probe exception containment.
- Added a hosted repository hardening drift check script and scheduled workflow.
- Updated the security policy and threat model for the `2.x` support line.

## 1.0.2 - 2026-05-25

- Fixed TUI navigation so up/down arrow keys remain functional after mouse interactions anywhere in the terminal.
- Restored robust launcher entrypoint handling for editable installs by shipping a dedicated `scripts/pinghue` bootstrap.
- Added explicit handling for empty DNS resolution results in probe/diagnostic paths to avoid false-positive success states on no-address responses.
- Expanded test coverage for the launcher, DNS-empty response behavior, and address-resolution edge cases.

## 1.0.1 - 2026-05-20

- Fixed TUI arrow-key navigation after mouse clicks on non-table chrome.

## 1.0.0 - 2026-05-20

- Declared macOS and Linux as the 1.0 supported platforms.
- Documented the CLI and JSON v1 stability contract, including the deprecation window for incompatible CLI changes.
- Documented the bounded sample window used by JSON evidence and TUI statistics.
- Added TUI integration coverage and CI coverage enforcement.
- Added release gates for dependency auditing and hosted hardening re-verification.
- Set the package maturity classifier to Production/Stable.

## 0.3.0 - 2026-05-20

- Added bounded target history with running packet and latency statistics to keep long TUI sessions responsive.
- Moved TUI DNS resolution and manual probe bursts off the startup/action path so the interface remains responsive during slow network operations.
- Added a dedicated ICMP thread pool sized by `--concurrency` to avoid default executor saturation at high probe counts.
- Added explicit `--fail-on-any-down` and `--fail-on-all-down` exit modes while keeping `--fail-on-down` as a compatibility alias.
- Improved host-file inline comment parsing, IPv6-capable DNS diagnostics, wide-terminal history layout, display sanitization, and special-device JSON output handling.

## 0.2.1 - 2026-05-19

- Hardened JSON export writes against predictable temp-path symlink redirection by using randomized same-directory temporary files before atomic replace.
- Documented the ICMP-mode interaction with the asyncio default thread pool in `--concurrency` help text and README.
- Verified hosted GitHub branch, release-tag, and PyPI environment hardening after applying the repository ruleset templates.
- Updated release documentation to clean generated artifacts before local package builds and avoid stale release-version commands.

## 0.2.0 - 2026-05-15

- Hardened JSON export by sanitizing target, host, and error strings before writing reports.
- Made JSON output writes atomic and preserved existing report files on write failures.
- Added host-file guardrails for regular files, a 1 MiB size cap, and a 5,000-line cap.
- Improved no-TUI interruption handling so interrupted runs can still write JSON evidence.
- Fixed no-TUI probe cadence drift and parallelized target resolution.
- Improved mixed IPv4/IPv6 `--numeric` handling and added DNS address failover.
- Added `--host-label`, `--resolve-name`, and `--fail-on-down`.
- Hardened release automation with artifact attestations, publish concurrency, dependency audit, Python 3.13 CI, and stricter repository ruleset templates.
- Updated Homebrew packaging guidance and smoke testing.
- Refreshed the threat model and security best-practices report.

## 0.1.0 - 2026-05-14

- Initial public release candidate.
- Added concurrent ICMP and TCP probing.
- Added Textual TUI with Slate + Signal colors.
- Added no-TUI mode for scripting and package smoke tests.
- Added `--check` environment doctor for macOS/Linux ICMP readiness.
- Added schema-versioned JSON export.
- Added terminal display sanitization for operator-visible target and error text.
- Added pinned GitHub Actions release workflow for PyPI trusted publishing.
