<p align="center">
  <img src="https://raw.githubusercontent.com/inxbit/pinghue/main/docs/assets/pinghue-hero.svg" alt="pinghue - colored concurrent ICMP/TCP ping monitor for maintenance windows" width="920">
</p>

<p align="center">
  <a href="https://pypi.org/project/pinghue/"><img alt="PyPI" src="https://img.shields.io/pypi/v/pinghue?color=58a6ff"></a>
  <a href="https://pypi.org/project/pinghue/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/pinghue?color=7ee787"></a>
  <a href="https://github.com/inxbit/pinghue/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/inxbit/pinghue/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/inxbit/homebrew-tap"><img alt="Homebrew tap" src="https://img.shields.io/badge/homebrew-inxbit%2Ftap-f2cc60"></a>
  <a href="https://github.com/inxbit/pinghue/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/inxbit/pinghue?color=f2cc60"></a>
</p>

<p align="center">
  <a href="#install">Install</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#modes">Modes</a> ·
  <a href="#cli-reference">CLI Reference</a> ·
  <a href="#json-output">JSON Output</a> ·
  <a href="#security-model">Security</a>
</p>

`pinghue` is a colored, concurrent ICMP/TCP ping monitor for maintenance windows. It gives operators a dense terminal view for many hosts at once and can also write structured JSON for reports, cron jobs, and CI checks.

Current version: `3.0.0`. The command-line interface and JSON output are stable public interfaces. JSON output uses `schema_version: 1`; breaking JSON changes require a new schema version.

<p align="center">
  <img src="https://raw.githubusercontent.com/inxbit/pinghue/main/docs/assets/pinghue-demo.gif" alt="real pinghue terminal recording: healthy hosts with green history bars, a TCP-refused host with amber markers, and unreachable hosts down in red" width="920">
</p>

## Why pinghue

<table>
  <tr>
    <td width="33%" valign="top"><b>Dense at-a-glance view</b><br><br>Watch many hosts in one readable terminal table — per-host latency, loss, jitter, and a live history bar.</td>
    <td width="33%" valign="top"><b>ICMP &amp; TCP</b><br><br>Default ICMP, or TCP connect checks with <code>-p</code>. Up to 1024 concurrent probes.</td>
    <td width="33%" valign="top"><b>Evidence-ready JSON</b><br><br><code>--output</code> writes a stable <code>schema_version&nbsp;1</code> summary for reports, cron, and CI.</td>
  </tr>
  <tr>
    <td valign="top"><b>Whole-run accuracy</b><br><br>Host states reflect everything that happened in the window, not just the most recent probes.</td>
    <td valign="top"><b>Scriptable</b><br><br><code>--no-tui</code>, <code>--count</code>, <code>--duration</code>, and fail-on-down exit codes for automation.</td>
    <td valign="top"><b>Local &amp; safe</b><br><br>No server, no daemon, no credentials. Runs on macOS and Linux, Python 3.10–3.13.</td>
  </tr>
</table>

## Install

Recommended isolated installs from PyPI:

```sh
uv tool install pinghue
```

or:

```sh
pipx install pinghue
```

Plain pip also works inside a virtual environment:

```sh
python -m pip install pinghue
```

Homebrew is available through the `inxbit/tap` tap:

```sh
brew install inxbit/tap/pinghue
```

The tap repository is `inxbit/homebrew-tap`; Homebrew exposes it as `inxbit/tap`.

For contributors, install from a local clone in editable mode:

```sh
git clone https://github.com/inxbit/pinghue.git
cd pinghue
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

The `dev` extra installs the local package plus the test, type-checking, linting, build, and schema-validation tools used by the repository.

## Quick Start

```sh
pinghue 1.1.1.1 8.8.8.8 example.com
pinghue -f hosts.txt
pinghue -p 443 example.com
pinghue -p 1 127.0.0.1 -c 1 --no-tui
pinghue --output maintenance.json 1.1.1.1 example.com
pinghue --output maintenance.json --overwrite 1.1.1.1 example.com
pinghue --host-label maintenance-window --output maintenance.json 1.1.1.1
```

Host files are plain text. Blank lines and `#` comments are ignored. Inline comments
on host lines are also ignored (`host.example  # comment`).
Host files must be non-symlink regular files, at most 1 MiB, and at most 5,000
lines. Each target string is capped at 253 characters.

```text
# edge and core checks
1.1.1.1
8.8.8.8
example.com
internal-db.corp
```

<p align="center">
  <img src="https://raw.githubusercontent.com/inxbit/pinghue/main/docs/assets/pinghue-screenshot.png" alt="real pinghue screenshot: a dense maintenance-window table monitoring sixteen hosts with addresses, latency columns, loss, per-host history bars, a legend, and footer keybindings" width="920">
</p>

## What This Is

- A focused terminal monitor for maintenance windows, migrations, and quick reachability checks.
- A concurrent ICMP/TCP probe runner with a readable Textual TUI.
- A scriptable probe tool with `--no-tui`, `--count`, `--duration`, and `--output`.
- A JSON-producing report helper for post-maintenance evidence.
- A local operator tool designed for macOS and Linux.

## What This Is Not

- Not a Prometheus, Smokeping, Zabbix, or NMS replacement.
- Not a long-running metrics database or alerting system.
- Not a privileged daemon.
- Not a packet capture or traceroute tool.
- Not a service that accepts remote network requests.

## Supported Platforms

The 3.0 support contract is macOS and Linux on Python 3.10 through 3.13. CI runs on both macOS and Linux for every supported Python version.

The TUI assumes an ANSI-capable terminal with Unicode glyph support. Windows and other POSIX platforms are outside the declared 3.0 support scope unless explicitly added later.

## Stability Policy

`pinghue` treats CLI flags and JSON exports as compatibility contracts. `schema_version: 1` is the JSON v1 contract: additive fields are non-breaking, but removing fields, changing field types, changing enum values, or changing required-field behavior requires a new schema version.

CLI removals, flag renames, and incompatible behavior changes are deprecated for at least one minor release before removal. Deprecated flags continue to parse during that window and release notes identify the replacement. Patch releases do not intentionally break CLI or JSON consumers.

Starting with `1.0.0`, release versions follow semantic versioning: patch releases are bug fixes, minor releases may add compatible behavior, and major releases are reserved for breaking CLI or JSON changes.

`2.0.0` changes the default `--output` behavior: existing regular files are preserved unless `--overwrite` is passed. Scripts that intentionally reuse the same JSON path should add `--overwrite`.

## Modes

`pinghue` defaults to ICMP mode:

```sh
pinghue 1.1.1.1 example.com
```

TCP mode is enabled by passing a port:

```sh
pinghue -p 443 example.com api.internal
```

Use no-TUI mode for scripts, cron, CI, and package smoke tests:

```sh
pinghue -p 443 example.com -c 3 --no-tui
```

Example no-TUI output:

```text
2026-05-14T18:32:11.420000+00:00 1.1.1.1 ok latency=9.20ms
2026-05-14T18:32:11.421000+00:00 example.com ok latency=14.08ms
2026-05-14T18:32:12.420000+00:00 api.internal timeout latency=-
```

## CLI Reference

```text
pinghue [OPTIONS] [TARGET ...]
```

| Option | Default | Description |
| --- | --- | --- |
| `TARGET ...` | none | Hostnames or IP addresses to probe, up to 253 characters each. Required unless `--check` is used. |
| `-f, --file PATH` | none | Read targets from a non-symlink plain-text host file. Blank lines and full-line/inline `#` comments are ignored. |
| `-p, --port PORT` | ICMP | Enable TCP connect checks against `PORT`. Valid range: `1-65535`. |
| `--ipv4` | off | Force IPv4 resolution/probing. |
| `--ipv6` | off | Force IPv6 resolution/probing. |
| `-n, --numeric` | off | Skip DNS and require IP literals. |
| `-i, --interval SEC` | `1.0` | Seconds between probes. Minimum: `0.1`. |
| `--timeout SEC` | interval | Per-probe timeout in seconds. Must be greater than `0`. |
| `-c, --count N` | continuous | Stop after `N` probes per target. Intentionally has no upper bound. |
| `--duration SEC` | continuous | Stop after elapsed seconds. Intentionally has no upper bound. |
| `--no-tui` | off | Print one line per probe instead of launching the TUI. |
| `--output PATH` | none | Write a JSON run summary on exit (`-` for stdout). Existing regular files are not replaced unless `--overwrite` is set. |
| `--overwrite` | off | Allow `--output` to replace an existing regular file. |
| `--output-mode {private,umask}` | `private` | Permissions for the `--output` file: `private` (`0600`, owner only) or `umask` (honor the process umask). |
| `--no-samples` | off | Omit per-probe samples from JSON output. |
| `--concurrency N` | `64` | Maximum concurrent probes, `1-1024`; ICMP mode uses a dedicated thread pool sized to this limit. |
| `--jitter-threshold MS` | `50.0` | Mark jitter as attention-worthy above this RFC 3550 interarrival jitter, in milliseconds. |
| `--fail-threshold COUNT` | `3` | Classify a host as down after this many consecutive failed probes. |
| `--fail-on-any-down` | off | Exit `3` when any target finishes down. |
| `--fail-on-all-down` | off | Exit `3` only when all targets finish down. `--fail-on-down` remains a compatibility alias. |
| `--history-style STYLE` | `bar` | One of `bar`, `dots`, `sparkline`, or `none`. |
| `--check` | off | Run the environment doctor and exit. |
| `--resolve-name HOST` | `example.com` | With `--check`, resolve this host for DNS diagnostics, up to 253 characters. Defaults to the first target when provided. |
| `--quiet` | off | With `--check`, suppress output and use only the exit code. |
| `--host-label LABEL` | `local` | Operator-controlled host label written to JSON output, up to 128 characters. |
| `-v, --version` | none | Print the installed version. |
| `-h, --help` | none | Print help. |

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Run completed (and no `--fail-on-*` condition triggered). Interrupting a run with Ctrl-C still evaluates the fail conditions, matching classic `ping` behavior; the JSON `exit_reason` field records `interrupted`. |
| `1` | Runtime error (for example the `--output` file could not be written), or `--check` found the environment not ICMP-ready. |
| `2` | Usage error: unknown flag or invalid value. |
| `3` | `--fail-on-any-down` / `--fail-on-all-down` condition triggered. |

## TUI Controls

| Key | Action |
| --- | --- |
| `q` | Quit. |
| `a` | Show or hide resolved addresses. |
| `r` | Reset the selected host. |
| `R` | Reset all hosts. |
| `b` | Probe the selected host immediately. |
| `B` | Probe all hosts immediately. |

## History Legend

The default history style is a fixed-scale colored bar:

- Green bar: successful probe.
- Amber bar: successful probe at or above the slow-latency threshold.
- Red `.` / `·`: timeout, loss, or down state.
- Amber `!`: TCP refused.

Successful latency uses `▁▂▃▄▅▆▇█`.

The fixed mapping keeps rows comparable:

| Latency | Glyph |
| --- | --- |
| `<=1ms` | `▁` |
| `<=3ms` | `▂` |
| `<=10ms` | `▃` |
| `<=30ms` | `▄` |
| `<=100ms` | `▅` |
| `<=300ms` | `▆` |
| `<=1000ms` | `▇` |
| `>1000ms` | `█` |

Use `--history-style dots`, `--history-style sparkline`, or `--history-style none` when a terminal font or workflow needs a simpler display.

## Host States

`pinghue` classifies each host from the **whole run**, not just the most recent probes. `down` requires `--fail-threshold` consecutive failures; any packet loss, or jitter above `--jitter-threshold`, marks a host `intermittent`. Because loss and jitter are cumulative over the run, a host that blips once and then recovers stays `intermittent` (not `healthy`) until you reset it (`r` or `R` in the TUI). This is intentional: a maintenance-window report should reflect everything that happened, not only the final moments.

## Slate + Signal Palette

`pinghue` uses a low-glare Slate + Signal palette designed for long maintenance windows: dark structure, high-contrast text, and saturated status colors that make the affected metric stand out quickly.

| Role | Hex | Used for |
| --- | --- | --- |
| Background | `#101418` | Terminal body and empty space. |
| Panel | `#151b22` | Table surface and primary content areas. |
| Header | `#1b2630` | Title bars and footer bands. |
| Border | `#2a313a` | Table outlines and separators. |
| Text | `#e6edf3` | Normal readable values. |
| Muted | `#8ea0b8` | Labels, secondary text, and inactive chrome. |
| Green | `#7ee787` | Healthy state, successful probes, and normal history bars. |
| Amber | `#f2cc60` | Slow latency, high jitter, intermittent state, and TCP refused markers. |
| Red | `#ff7b72` | Loss, down state, timeouts, and failed history markers. |
| Selection Blue | `#58a6ff` | Focus accents and selected-row treatment. |

## Doctor

Run the doctor before relying on ICMP mode:

```sh
pinghue --check
```

Linux may block unprivileged ICMP sockets. TCP mode does not need special privileges:

```sh
pinghue -p 443 example.com
```

If `pinghue --check` reports that your GID is outside `net.ipv4.ping_group_range`, the preferred fix is to allow only your current group:

```sh
gid="$(id -g)"
sudo sysctl -w "net.ipv4.ping_group_range=${gid} ${gid}"
echo "net.ipv4.ping_group_range=${gid} ${gid}" \
  | sudo tee /etc/sysctl.d/99-pinghue.conf
```

The broader range `0 2147483647` also works, but enables unprivileged ICMP for every local group on the system.

`setcap cap_net_raw` does not work for pip or Homebrew installs: the `pinghue` command is a Python launcher script, and Linux ignores file capabilities on interpreter scripts. Use the `ping_group_range` fix above, or TCP mode. Do not set capabilities on a shared Python interpreter.

## JSON Output

`--output PATH` writes one JSON document per run. Existing regular files are preserved by default; add `--overwrite` when replacing a known report path is intentional. This is the breaking `2.0.0` CLI change for scripts that previously reused the same output file. The schema lives at `schemas/output-v1.schema.json`, and an example lives at `examples/pinghue-output-example.json`.

```sh
pinghue -f hosts.txt --duration 180 --output maintenance.json
pinghue -f hosts.txt --duration 180 --output maintenance.json --overwrite
```

Use `--no-samples` when you only need final per-target statistics.

Every output document includes:

- `schema_version`
- `pinghue_version`
- run metadata (including `samples_window`)
- probe configuration
- ordered target results
- per-target stats
- optional per-probe samples

Per-target `stats` (sent, received, loss, latency, jitter) are computed over **every** probe in the run, so `stats.sent` reflects the whole run. The per-target `samples` array retains only the most recent `run.samples_window` probes (currently 1000). On long runs `stats.sent` therefore exceeds `len(samples)` — that is expected windowing, not a truncated file. When reconciling evidence, treat `samples` as the recent tail and `stats` as authoritative for the full run; `--no-samples` omits the array entirely.

The `run.host` field defaults to `local` to avoid leaking workstation hostnames. Use `--host-label` when a report needs an operator-selected system or maintenance-window label. Operator-visible target, host-label, and error text is escaped outside printable ASCII so terminal controls and visually deceptive Unicode are represented literally.

## Security Model

`pinghue` is a local CLI/TUI. It does not run a server, accept remote requests, store credentials, or require secrets. The security-sensitive areas are:

- terminal rendering of operator-supplied hostnames and OS error strings
- Linux ICMP privilege configuration
- local output paths selected by the operator
- release workflow integrity

See `pinghue-threat-model.md`, `security-best-practices-report.md`, and `SECURITY.md`.

## Release Channels

Published release channels:

1. GitHub repository: `inxbit/pinghue`
2. GitHub Actions CI on Linux and macOS
3. GitHub Release with sdist and wheel artifacts
4. PyPI trusted publishing through a protected `pypi` environment
5. Homebrew tap: `inxbit/tap` from `inxbit/homebrew-tap`

The release workflow is tag-driven:

```sh
git tag -s vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

## Development

```sh
pytest
pytest --cov=pinghue --cov-report=term-missing --cov-fail-under=80
ruff check .
mypy src
pip-audit
SOURCE_DATE_EPOCH=0 python -m build --no-isolation
twine check dist/*
```

Run the development commands from a clone installed with `python -m pip install -e ".[dev]"`. The `pytest --cov` line reports line and branch coverage; CI enforces a coverage floor.

## License

MIT. See `LICENSE`.
