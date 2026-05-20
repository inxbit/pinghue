# Changelog

All notable changes to this project are documented here.

Starting with `1.0.0`, this project follows semantic versioning. CLI flags and JSON output are compatibility contracts; breaking JSON changes increment `schema_version`.

## [Unreleased]

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
