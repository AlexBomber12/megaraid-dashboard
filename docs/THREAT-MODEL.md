# MegaRAID Dashboard Threat Model

Date: 2026-Q2

This document describes the pass-1 threat model for MegaRAID Dashboard, a single-host,
single-controller homelab monitoring and operations service. The model assumes the production
deployment described in `INSTALL.md`, `docs/PROXY-SETUP.md`, and `docs/RUNBOOK.md`.

## System Summary

MegaRAID Dashboard runs as a FastAPI application behind nginx. The app collects controller
state by invoking `storcli64` with JSON output, validates that output into pydantic models,
stores current and historical state in SQLite, renders a server-side HTML UI, emits Prometheus
metrics on a loopback exporter, and sends email alerts for critical events.

The application is intentionally scoped to one MegaRAID controller on one Ubuntu host. It is
not designed as a multi-tenant application, an internet-facing SaaS service, or a generic
storage management platform.

## Assets

### RAID Array Availability

The primary asset is the integrity and availability of the physical RAID array. Write
operations can alter drive state, start replacement workflows, or influence rebuild behavior.
A mistaken or malicious write can cause degraded redundancy, data loss, or extended rebuild
exposure.

### Controller State

Controller state includes the firmware version, driver version, cache vault state, alarm
state, virtual drive state, physical drive state, drive serial numbers, SAS addresses,
temperature readings, and error counters. This data is operationally sensitive because it
reveals storage topology and failure modes.

### SQLite Database

The database stores snapshots, event history, alert deduplication state, maintenance state,
and operator-action audit events. The database is the source of truth for the UI and for
alert suppression. Tampering can hide hardware faults, forge operator actions, or trigger
false alerts.

### Environment File

`/etc/megaraid-dashboard/env` contains SMTP credentials, the Basic auth username and bcrypt
password hash, the `storcli` path, mode flags, and runtime thresholds. Read access leaks
secrets. Write access can enable destructive mode, redirect `storcli`, disable collection, or
alter thresholds.

### Sudoers Fragment

`/etc/sudoers.d/megaraid-dashboard` grants the unprivileged service account passwordless
access to whitelisted `storcli64` commands. The fragment must remain exact and narrow because
it is the main privilege boundary between the web app and root-owned hardware access.

### Audit Trail

Operator actions are recorded as events in the database. The audit trail supports incident
reconstruction for locate LED actions, maintenance-mode changes, destructive replacement
steps, and rebuild completion observations.

### Alert Channel

SMTP credentials and alert delivery behavior are assets because attackers can suppress,
forge, or flood alerts. The alert channel is also a signal path for hardware failures.

### Metrics Endpoint

The Prometheus exporter exposes operational labels such as controller model, controller
serial, physical drive model, physical drive serial, and slot mapping. It is unauthenticated
by design and should remain bound to loopback or otherwise network-restricted.

## Trust Boundaries

### Browser to nginx

The browser communicates with nginx over HTTPS in the intended production deployment. nginx
terminates TLS, applies HTTP Basic auth, sets security headers, rate limits the login path,
and forwards requests to the local FastAPI process.

### nginx to FastAPI

FastAPI listens on the host and receives proxied requests from nginx. The sample systemd unit
currently binds the app to `0.0.0.0`; the expected perimeter is still nginx plus host
firewalling. `X-Forwarded-Prefix` is trusted only when nginx overwrites the header.

### FastAPI to SQLite

The app uses SQLAlchemy ORM and SQLite. The service account must write the database directory,
but the web request surface should not expose arbitrary SQL or filesystem paths.

### FastAPI to storcli

All controller access crosses the dedicated wrapper in `src/megaraid_dashboard/storcli/`.
The wrapper appends JSON mode, validates command arguments against exact allowlist patterns,
and uses `asyncio.create_subprocess_exec` without a shell.

### FastAPI to sudo

When `STORCLI_USE_SUDO=true`, subprocess execution crosses from the unprivileged service
account to root through sudo. The sudoers rule must whitelist only expected `storcli64`
argument vectors. Drift between the wrapper allowlist and sudoers command list is a security
and availability risk.

### FastAPI to SMTP

Alert delivery crosses from local application code to the configured SMTP server. Credentials
come from the environment file. Failures are logged, and sent events are marked in the
database.

### FastAPI to Prometheus

The metrics exporter runs as a separate loopback listener by default. If exposed beyond
loopback, the network boundary must be enforced outside the app because `/metrics` has no app
authentication.

## Actors

### Intended Operator

The intended operator is the single homelab administrator with shell access to the host and
Basic auth credentials for the UI. The operator can read dashboard state and perform
maintenance actions when the required mode flags are enabled.

### LAN Attacker

A LAN attacker can send HTTP requests to any exposed app or metrics port, attempt credential
guessing, exploit request parsing bugs, abuse CSRF if the operator is logged in, or read
unauthenticated metrics if firewall rules are missing.

### Compromised Browser Session

An attacker with access to the operator's browser or Basic auth credentials can issue valid
UI requests. CSRF protects cross-site writes, but it does not protect a fully compromised
browser, stolen credentials, or local malware.

### Compromised Service Account

An attacker who gains code execution as `raid-monitor` can read or write the SQLite database,
read installed source, call any sudoers-whitelisted `storcli` command, and connect to local
app ports. Systemd hardening limits broader host writes but does not make the service account
untrusted-safe.

### Local Privilege Escalation Attacker

A local user on the host may attempt to modify `storcli64`, parent directories, the sudoers
fragment, the environment file, the database, or lock files to influence privileged
execution.

### Supply Chain Attacker

A malicious or vulnerable Python package, vendored JavaScript file, GitHub Action, or system
package can compromise runtime behavior or CI. This project currently relies on pip package
resolution from PyPI and does not pin hashes.

## STRIDE Threats

| Category | Threat | Current mitigations | Open risk |
| --- | --- | --- | --- |
| Spoofing | LAN attacker guesses Basic auth credentials. | nginx and app-side rate limiting, bcrypt password hash, no default password in code. | Password strength is operational; app does not validate bcrypt hash format at config load. |
| Spoofing | Client spoofs `X-Forwarded-For` to evade rate limits. | App trusts forwarded addresses only from configured trusted proxy CIDRs. | Misconfigured `TRUSTED_PROXY_IPS` can weaken rate limiting. |
| Spoofing | Client spoofs `X-Forwarded-Prefix` to poison generated URLs. | Prefix is syntax-normalized; nginx sample overwrites the header. | Direct app exposure can still trust a syntactically valid client-supplied prefix. |
| Tampering | User input alters SQL queries. | SQLAlchemy ORM/select expressions are used; no user-controlled raw SQL was found in routes. | Search and filter inputs should stay constrained to allowlists. |
| Tampering | User input injects shell metacharacters into `storcli`. | `create_subprocess_exec` is used without shell; wrapper rejects whitespace args and allowlists exact commands. | Sudoers currently covers fewer commands than the wrapper, causing operational drift. |
| Tampering | Attacker changes `/etc/megaraid-dashboard/env`. | Installer creates root-owned `0600` env file. | Group is `raid-monitor` but group has no read bit; operations must preserve `0600`. |
| Tampering | Attacker swaps `storcli64` or a parent directory. | Installer validates root ownership and no group/other write before writing sudoers. | Ongoing drift after install is not continuously checked. |
| Repudiation | Operator denies destructive action. | Destructive routes record operator-action audit events with username, command context, result, and serial. | Locate LED audit failure is logged but does not fail the operation. |
| Repudiation | Attacker edits SQLite audit history. | Service uses a dedicated account and systemd write paths. | SQLite audit rows are mutable by the service account; no append-only external log exists. |
| Information disclosure | Metrics reveal drive serials and topology. | Metrics bind to `127.0.0.1` by default; README warns to firewall external exposure. | If `METRICS_LISTEN_ADDRESS` is widened, app authentication is not applied to metrics. |
| Information disclosure | XSS exposes Basic auth session or CSRF token. | Jinja autoescape is enabled; custom slot-link filter escapes text and URLs. | Inline `Markup` use should remain narrowly reviewed. |
| Information disclosure | Error responses leak live drive serials. | Serial mismatch responses intentionally avoid echoing canonical serials. | Some successful and dry-run responses include typed serial and command args for operator clarity. |
| Denial of service | Repeated auth attempts consume CPU through bcrypt. | Rate limiter reserves a slot before auth verification. | In-memory limiter resets on process restart and is per worker. |
| Denial of service | Slow or hung `storcli` blocks request or collector work. | Wrapper enforces a timeout and raises typed errors. | Hardware or sudo failures can still degrade health and alert fidelity. |
| Elevation of privilege | Web layer reaches root through sudo. | Narrow sudoers command alias, no shell, wrapper allowlist, unprivileged service user. | Sudoers must be updated in lockstep with any new write command. |
| Elevation of privilege | Lock file path in `/tmp` is manipulated. | Lock acquisition uses file locks, not trust in file contents. | Bandit/ruff flag `/tmp` defaults; production should prefer `/run/megaraid-dashboard`. |

## Mitigations

### Authentication

The app enforces Basic auth middleware for non-whitelisted paths. `/healthz`, favicon, and
static assets are whitelisted. The intended production perimeter also uses nginx Basic auth.
Failed auth attempts are rate limited by client address.

### CSRF Protection

State-changing HTTP methods require a double-submit CSRF token. The cookie uses the
`__Host-` prefix, `Path=/`, `SameSite=Strict`, and `Secure`. JavaScript copies the cookie
value into the `X-CSRF-Token` header for HTMX and fetch requests.

### Command Safety

The `storcli` wrapper appends JSON mode, rejects whitespace in individual arguments, checks
the full command string against exact regular expressions, and uses
`asyncio.create_subprocess_exec` without `shell=True`.

### Destructive Operation Gating

Destructive replacement operations require both `maintenance_mode` and `destructive_mode`,
plus typed serial confirmation. The route rechecks live drive identity with `storcli` before
executing destructive commands.

### Audit Logging

Maintenance-mode changes, locate operations, replacement steps, insert operations, and
rebuild completion observations are recorded as operator-action events. Failed destructive
commands are recorded before returning errors when the database is available.

### Database Access

FastAPI routes use service functions and SQLAlchemy query builders. User-controlled filters
are constrained to allowlisted severity, category, pagination, drive slot, and range values.

### Deployment Hardening

The systemd unit runs as `raid-monitor`, sets `NoNewPrivileges=true`, enables `PrivateTmp`,
sets `ProtectSystem=strict`, sets `ProtectHome=true`, and grants writes only to the data
directory. nginx adds HSTS, `X-Frame-Options`, `X-Content-Type-Options`, and a referrer
policy.

### Dependency Review

Quarterly pass-1 review runs `scripts/security-scan.sh`, which executes Ruff security rules,
`pip-audit`, Bandit, and an environment-file permission probe. Findings are tracked in
`docs/SECURITY-AUDIT-2026-Q2.md` and remediated through separate MICRO PRs.

## Open Risks

1. The FastAPI app binds to `0.0.0.0` in the sample systemd unit. The expected deployment is
   still protected by nginx and host firewalling, but direct port exposure would bypass nginx
   rate limiting and headers.
2. The sudoers fragment generated by the installer currently includes read-only `storcli`
   commands. The wrapper includes locate and replacement commands, so write workflows can
   fail until sudoers is expanded deliberately.
3. `/tmp` lock-file defaults are flagged by SAST tooling. They do not currently carry secret
   content, but `/run/megaraid-dashboard` would be a tighter production default.
4. Metrics are unauthenticated by design. They must remain loopback-only or protected by a
   firewall and Prometheus scrape allowlist.
5. Audit history is stored in mutable SQLite rows. For higher assurance, operator-action
   events should also be sent to an append-only log target.
6. Python dependencies are version-ranged rather than locked with hashes. Reproducible build
   work is tracked separately in the roadmap.
