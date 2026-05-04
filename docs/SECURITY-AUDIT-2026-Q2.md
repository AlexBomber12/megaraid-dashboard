# Security Audit 2026-Q2

PR: PR-054

Scope: internal pass-1 security audit covering code review, configuration review, dependency
scan setup, and documented remediation planning. Active exploitation and penetration testing
are intentionally deferred to PR-055.

## Methodology

This audit used static code review and local SAST commands against the repository state on
2026-05-04. The review focused on the security model documented in `AGENTS.md`, the
architecture described in `docs/ARCHITECTURE.md`, the reverse-proxy deployment in
`docs/PROXY-SETUP.md`, the installer and systemd unit, and the threat model in
`docs/THREAT-MODEL.md`.

Reviewed areas:

- Authentication and rate limiting in `src/megaraid_dashboard/web/auth.py` and
  `src/megaraid_dashboard/web/rate_limit.py`.
- CSRF protection in `src/megaraid_dashboard/web/csrf.py` and frontend request helpers.
- FastAPI route input validation and write-operation gates in
  `src/megaraid_dashboard/web/routes.py`.
- `storcli` command construction and invocation in
  `src/megaraid_dashboard/services/drive_actions.py` and
  `src/megaraid_dashboard/storcli/runner.py`.
- SQL access through `src/megaraid_dashboard/db/dao.py` and route-level query builders.
- Template rendering and custom HTML filters in
  `src/megaraid_dashboard/web/templates.py` and `src/megaraid_dashboard/templates/`.
- Deployment configuration in `scripts/install.sh`, `scripts/preflight.sh`,
  `deploy/megaraid-dashboard.service`, and `deploy/nginx/megaraid.conf.sample`.
- Dependency scanning workflow through the new `scripts/security-scan.sh`.

Local SAST command run before this report:

```bash
.venv/bin/ruff check --select S --output-format concise src/ || true
```

Observed Ruff security output:

```text
src/megaraid_dashboard/config.py:41:32: S108 Probable insecure usage of temporary file or directory: "/tmp/megaraid-dashboard-collector.lock"
src/megaraid_dashboard/config.py:45:30: S108 Probable insecure usage of temporary file or directory: "/tmp/megaraid-dashboard-metrics.lock"
src/megaraid_dashboard/services/notifier.py:26:22: S108 Probable insecure usage of temporary file or directory: "/tmp/megaraid-dashboard-notifier.lock"
src/megaraid_dashboard/web/csrf.py:92:9: S112 try-except-continue detected, consider logging the exception
src/megaraid_dashboard/web/routes.py:1262:21: S105 Possible hardcoded password assigned to: "token"
src/megaraid_dashboard/web/templates.py:78:16: S704 Unsafe use of markupsafe.Markup detected
src/megaraid_dashboard/web/templates.py:86:12: S704 Unsafe use of markupsafe.Markup detected
```

At the start of the audit branch, `pip-audit` and `bandit` were not installed in the local
virtual environment. This PR adds them to the `dev` extra and adds `scripts/security-scan.sh`
so the quarterly scan has one repeatable entry point.

Observed `pip-audit --strict` output after installing the updated dev extra:

```text
ERROR:pip_audit._cli:megaraid-dashboard: Dependency not found on PyPI and could not be audited: megaraid-dashboard (0.1.0)
```

This is expected for the local editable project package. Future quarterly runs should review
third-party vulnerability output separately from this local-package notice.

## Codebase Review

### SQL Injection

No SQL injection finding was identified in the reviewed application code. Database access is
through SQLAlchemy ORM queries, SQLAlchemy Core expressions, or fixed SQL text without
user-controlled fragments.

Examples reviewed:

- Event and snapshot DAO functions use `select()`, `.where()`, `.limit()`, `delete()`, and
  SQLite upsert APIs in `src/megaraid_dashboard/db/dao.py`.
- Web route filters constrain severity, category, pagination, drive slots, and chart ranges
  before building queries.
- Raw `text()` usage in rebuild marker locking uses a fixed PostgreSQL advisory-lock
  statement with bound parameters.
- `scripts/preflight.sh` creates a temporary SQLite table name from process id and UUID,
  then quotes it before executing SQL. The table name is not user supplied.

Residual risk: future report-style pages should keep query fields allowlisted and avoid
string-built SQL for sorting or filtering.

### Command Injection

No shell command injection finding was identified in the `storcli` execution path. Every
runtime `storcli` invocation reviewed goes through `run_storcli()` in
`src/megaraid_dashboard/storcli/runner.py`.

Controls reviewed:

- `run_storcli()` appends JSON mode through `_with_json_flag()`.
- `_validate_command()` rejects empty args and args containing whitespace.
- `_validate_command()` checks the joined command against exact allowlist regular
  expressions.
- `asyncio.create_subprocess_exec()` is called with argv elements and no shell.
- Drive action command builders validate enclosure, slot, disk group, array, and row as
  bounded integers before returning argv.

The main operational issue is not command injection but sudoers drift: the Python wrapper
allows locate and replacement commands that the installer sudoers fragment does not yet
whitelist.

### Path Traversal

No user-controlled filesystem path traversal finding was identified. Static files are served
from the package static directory. Templates are loaded from the package templates directory.
The UI exposes slot, event, and chart parameters, not file paths.

Deployment scripts write to operator-controlled install paths and system locations while
running as root. Those scripts validate key paths such as `STORCLI_PATH` before sudoers
creation, including absolute path, regular file, executable bit, non-symlink status, root
ownership, and no group/other write permissions on parent directories.

### XSS in Templates

No exploitable XSS was identified in the reviewed templates. Jinja autoescape is enabled for
HTML and XML. Event summaries, drive attributes, serial numbers, and labels are rendered
through normal escaped template expressions.

The custom `slot_link` filter intentionally returns `Markup` to add links around slot
references inside event text. The implementation escapes the input text before and after the
link, escapes the link label, and escapes the generated URL. Ruff flags this as S704 because
manual `Markup` use is inherently sensitive. This audit records it as an informational
finding for continued review rather than a vulnerability.

### Auth Bypass

No direct auth bypass was identified for protected UI routes. `BasicAuthMiddleware` applies
to HTTP requests except whitelisted paths, validates the Basic auth scheme, base64 token, and
username, and verifies passwords with bcrypt. The authenticated username is placed in
`scope["user_username"]` for audit records.

Whitelisted paths:

- `/healthz`
- `/favicon.ico`
- `/static/` assets

The `/metrics` endpoint is served by a separate Starlette app and is unauthenticated by
design. It binds to `127.0.0.1` by default and must remain network-restricted.

The nginx sample also exposes `/raid/healthz` without Basic auth, which matches the app
whitelist and supports health checks.

### CSRF

Protected methods require a matching CSRF cookie and `X-CSRF-Token` header. The token is
generated with `secrets.token_bytes(32)`, base64url encoded, and stored in a `__Host-csrf`
cookie with `Path=/`, `SameSite=Strict`, and `Secure`.

The middleware protects `POST`, `PUT`, `DELETE`, and `PATCH`. It does not protect `GET`,
which is expected. Reviewed `GET` routes are read-only or health/static endpoints.

Residual risk: CSRF does not protect against stolen Basic auth credentials or a compromised
operator browser.

### Write and Destructive Operations

Reviewed write paths:

- Maintenance start and stop.
- Locate LED start and stop.
- Drive replacement offline, missing, insert, and rebuild-status observation.

Destructive replacement steps require `maintenance_mode` and `destructive_mode`, validate
the typed serial against the latest snapshot, re-query live drive identity through `storcli`
before execution, and avoid echoing canonical serials on mismatch. Failed destructive
commands are audited when the database is available.

Locate LED operations are not destructive and do not require maintenance mode. They are
audited, but audit persistence failure is logged without failing the locate operation. This
is acceptable for low-risk LED state but should stay limited to non-destructive commands.

## Configuration Audit

### Environment File

The installer creates `/etc/megaraid-dashboard/env` with mode `0600`, owner `root`, and group
`raid-monitor`. This prevents the service account from reading the file directly through
filesystem permissions; systemd loads it into the service environment.

Required operational checks:

```bash
stat -c "%a %U:%G %n" /etc/megaraid-dashboard/env
sudo systemctl show megaraid-dashboard.service -p EnvironmentFiles
```

Expected stat output:

```text
600 root:raid-monitor /etc/megaraid-dashboard/env
```

### Sudoers Narrow Rule

The installer validates the `storcli64` target before writing sudoers. It requires an
absolute path, a regular executable file, no symlink, root ownership, and no group/other
write permission for the binary and its parent directories.

The generated sudoers fragment currently whitelists read-only commands:

- `/c0 show all J`
- `/c0/vall show all J`
- `/c0/eall/sall show all J`
- `/c0/cv show all J`
- `/c0/bbu show all J`

The application wrapper also allows drive show, rebuild show, locate, offline, missing, and
insert commands. That mismatch is a medium finding because intended UI workflows can fail in
production and operators may be tempted to broaden sudoers manually.

### Systemd Hardening

The sample unit runs as `raid-monitor`, sets `NoNewPrivileges=true`, `PrivateTmp=true`,
`ProtectSystem=strict`, `ProtectHome=true`, and restricts writable paths to
`/var/lib/megaraid-dashboard`.

Open hardening opportunities:

- Bind the FastAPI listener to loopback in the sample unit and rely on nginx for external
  exposure.
- Consider `RuntimeDirectory=megaraid-dashboard` and lock files under `/run`.
- Consider additional systemd restrictions after hardware access is verified, such as
  `ProtectKernelTunables`, `ProtectControlGroups`, `RestrictSUIDSGID`, and syscall filters.

### SQLite ACLs

The installer creates `/var/lib/megaraid-dashboard` as `raid-monitor:raid-monitor` with mode
`0750`. The database must remain writable by the service account. The systemd unit grants
write access only to this data path through `ReadWritePaths`.

Required operational checks:

```bash
stat -c "%a %U:%G %n" /var/lib/megaraid-dashboard
stat -c "%a %U:%G %n" /var/lib/megaraid-dashboard/megaraid.db
```

Expected ownership is `raid-monitor:raid-monitor`. The exact database mode may vary based on
SQLite creation umask, but it should not be group/world writable.

### nginx Perimeter

The nginx sample terminates TLS, redirects HTTP to HTTPS, sets HSTS and common response
hardening headers, rate limits `/raid/`, forwards prefix headers, and proxies static and
health paths directly.

The sample must be paired with host firewall rules if Uvicorn continues to bind to
`0.0.0.0`.

## Dependency Audit

This PR adds the required scan tooling to the `dev` extra:

- `pip-audit>=2.7`
- `bandit>=1.7`

The quarterly command is:

```bash
bash scripts/security-scan.sh
```

Expected behavior:

- Ruff security rules print S-series findings for `src/` and do not fail the script.
- `pip-audit --strict` checks installed packages against known vulnerability databases and
  does not fail the script.
- Bandit scans `src/` at medium-or-higher severity and does not fail the script.
- The environment-file permission probe prints the production stat line when present, or a
  development-only missing-file message.

Because this repository currently uses version ranges rather than a lockfile, `pip-audit`
results are environment-dependent. Reproducible build work should include a lockfile or hash
pinning strategy before treating dependency audit output as fully reproducible.

The 2026-Q2 local scan produced only the expected local editable package notice for
`megaraid-dashboard==0.1.0`; no third-party vulnerable package table was emitted.

## Findings

### Finding 2026-Q2-001

- Severity: medium
- Component: `scripts/install.sh:471`, `src/megaraid_dashboard/storcli/runner.py:24`
- Description: The `storcli` wrapper allowlist includes drive show, rebuild show, locate,
  offline, missing, and insert commands, but the installer-generated sudoers fragment
  whitelists only the read-only collector commands. Production write workflows that require
  sudo can fail, and manual operator fixes may over-broaden sudo access.
- Recommendation: Add the exact additional `storcli64` commands required by the supported
  UI workflows to the sudoers template and tests. Keep the sudoers list synchronized with
  `_ALLOWED_COMMAND_PATTERNS`.
- Status: follow-up MICRO PR pending.

### Finding 2026-Q2-002

- Severity: low
- Component: `deploy/megaraid-dashboard.service:15`
- Description: The sample service binds Uvicorn to `0.0.0.0`. The intended security
  perimeter is nginx plus host firewalling, but direct exposure of port 8090 would bypass
  nginx TLS, nginx Basic auth, nginx rate limiting, and nginx response headers.
- Recommendation: Change the sample service to bind Uvicorn to `127.0.0.1` unless there is
  a documented operational reason to expose it directly.
- Status: follow-up MICRO PR pending.

### Finding 2026-Q2-003

- Severity: low
- Component: `src/megaraid_dashboard/config.py:41`,
  `src/megaraid_dashboard/config.py:45`, `src/megaraid_dashboard/services/notifier.py:26`
- Description: Ruff S108 flags default lock-file paths under `/tmp`. The lock files do not
  contain secrets, but `/tmp` is a shared namespace and is weaker than a service-owned runtime
  directory.
- Recommendation: Add a systemd runtime directory and move default lock paths to
  `/run/megaraid-dashboard/`.
- Status: follow-up MICRO PR pending.

### Finding 2026-Q2-004

- Severity: low
- Component: `src/megaraid_dashboard/web/auth.py:111`,
  `src/megaraid_dashboard/config.py:30`
- Description: A malformed bcrypt hash is handled during request authentication, but it is
  not rejected at settings load. This can leave the service running with all login attempts
  failing until the first request discovers the invalid hash.
- Recommendation: Add settings validation for accepted bcrypt hash prefixes and malformed
  hash handling, with tests for invalid configuration.
- Status: follow-up MICRO PR pending.

### Finding 2026-Q2-005

- Severity: info
- Component: `src/megaraid_dashboard/web/templates.py:75`
- Description: The custom `slot_link` filter returns `Markup` to inject anchor tags around
  slot references. The implementation escapes all user-controlled text and generated URL
  values, but manual safe-HTML construction is a sensitive pattern and is flagged by Ruff
  S704.
- Recommendation: Keep the filter covered by focused tests and consider adding a local Ruff
  ignore with a short security comment after review.
- Status: documented risk; no remediation PR required unless the filter expands.

### Finding 2026-Q2-006

- Severity: info
- Component: `src/megaraid_dashboard/web/metrics.py:210`,
  `src/megaraid_dashboard/app.py:170`
- Description: The metrics endpoint is unauthenticated by design and can expose controller
  and drive serial labels if `METRICS_LISTEN_ADDRESS` is changed from loopback.
- Recommendation: Keep the loopback default, document firewall requirements, and avoid
  exposing the metrics listener outside trusted monitoring paths.
- Status: documented operational constraint.

## Remediation Plan

1. MICRO PR: synchronize sudoers with the wrapper allowlist for supported locate and
   replacement workflows.
2. MICRO PR: bind the sample Uvicorn service to `127.0.0.1` and update docs if needed.
3. MICRO PR: move default lock paths to a service runtime directory and update the systemd
   sample.
4. MICRO PR: validate `ADMIN_PASSWORD_HASH` at settings load.
5. Keep `slot_link` tests in place and document any local S704 ignore if one is added later.
6. Continue quarterly `scripts/security-scan.sh` runs and record output in future audit docs.

## Risk

This PR touches the security model documentation and adds security scan tooling. It does not
change authentication behavior, `storcli` command behavior, sudoers behavior, database schema,
or runtime write-operation gates.
