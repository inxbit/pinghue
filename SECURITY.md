# Security Policy

## Supported Versions

Security fixes are provided for the latest stable release line.

| Version | Supported |
| --- | --- |
| `3.x` | Yes |
| `<3.0` | No |

## Reporting a Vulnerability

Please report security issues privately through GitHub Security Advisories:

https://github.com/inxbit/pinghue/security/advisories/new

If advisories are unavailable, open a minimal public issue that says a private security report is needed, without publishing exploit details.

Include:

- affected version
- platform and Python version
- exact command or workflow involved
- expected impact
- minimal reproduction details

Do not include secrets, customer hostnames, private IP inventories, or production maintenance data in public issues.

## Security Model

`pinghue` is a local operator tool. It does not run a server, expose a remote API, store credentials, or require secrets.

Security-sensitive areas are:

- terminal rendering of operator-provided hostnames and OS error strings
- Linux ICMP privilege configuration
- local host file reads and JSON output writes
- trusted publishing and release workflow integrity

See `pinghue-threat-model.md` for the repository threat model.
