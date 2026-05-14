# Changelog

All notable changes to this project are documented here.

This project follows semantic versioning once it reaches `1.0.0`. Before `1.0.0`, CLI flags and JSON output may change; breaking JSON changes increment `schema_version`.

## 0.1.0 - 2026-05-14

- Initial public release candidate.
- Added concurrent ICMP and TCP probing.
- Added Textual TUI with Slate + Signal colors.
- Added no-TUI mode for scripting and package smoke tests.
- Added `--check` environment doctor for macOS/Linux ICMP readiness.
- Added schema-versioned JSON export.
- Added terminal display sanitization for operator-visible target and error text.
- Added pinned GitHub Actions release workflow for PyPI trusted publishing.
