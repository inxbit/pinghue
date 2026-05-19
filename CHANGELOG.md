# Changelog

All notable changes to this project are documented here.

This project follows semantic versioning once it reaches `1.0.0`. Before `1.0.0`, CLI flags and JSON output may change; breaking JSON changes increment `schema_version`.

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
