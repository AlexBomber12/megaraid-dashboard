# Security Audit 2026-Q2 Penetration Pass

PR: PR-055

Scope: manual penetration pass against the web application security controls after the
PR-054 internal audit. This pass exercised authentication, CSRF, rate limiting, request
validation, event rendering, static file serving, and write-operation gates.

## Methodology

Date: 2026-05-04

Tools:

- `curl` for raw HTTP requests.
- `jq` for JSON response inspection.
- A clean local test container running the application on `http://target:8090`.
- A separate shell namespace on the same LAN segment acting as the attacking host.

Test environment:

- App installed from the audited branch with `pip install -e ".[dev]"`.
- SQLite database initialized by Alembic startup migration.
- Collector disabled so tests never touched real `storcli` hardware.
- `storcli` write paths exercised through dry-run requests or expected pre-gate failures.
- Admin credentials set to `admin:test-password` for the test deployment only.
- Rate-limit defaults: `AUTH_RATE_LIMIT_PER_MINUTE=5` and `AUTH_RATE_LIMIT_BURST=2`.
- Target alias used below: `target=http://target:8090`.

Reusable shell setup:

```bash
target=http://target:8090
auth="$(printf 'admin:test-password' | base64 | tr -d '\n')"
bad_auth="$(printf 'admin:wrong-password' | base64 | tr -d '\n')"
curl -i -u admin:test-password "$target/" >/tmp/headers.txt
csrf="$(awk -F'[=;]' '/__Host-csrf/ {print $2; exit}' /tmp/headers.txt)"
```

Assumptions:

- nginx is responsible for TLS and external Basic auth in production, but this test also
  validates the application-level Basic auth middleware because it remains a defense in depth.
- `/healthz`, `/favicon.ico`, and `/static/` are intentionally unauthenticated.
- The metrics listener is out of scope for these web UI tests because it binds to loopback by
  default and is documented as unauthenticated for Prometheus scrapes.
- Direct SQLite writes are local-host compromise, not web API behavior.

Known-bad validation:

PR-054 fixed and documented several classes of risk before this pass. A full rollback test
against a tagged vulnerable release was not possible because no known-bad tag exists in this
repository. Where possible, this pass used attacks that would have succeeded against common
missing-control implementations: missing auth, missing CSRF, spoofed `X-Forwarded-For`, SQL
string concatenation, unescaped template output, and shell-built `storcli` commands.

## Test Cases

### Test PEN-001: Auth bypass via missing Authorization header

Command:

```bash
curl -i "$target/"
```

Expected: `401 Unauthorized` with `WWW-Authenticate: Basic realm="megaraid-dashboard"`.

Actual: `401 Unauthorized`; `WWW-Authenticate` header was present.

Finding: none.

### Test PEN-002: Auth bypass via empty Basic credentials

Command:

```bash
curl -i -H "Authorization: Basic Og==" "$target/"
```

Expected: `401 Unauthorized` with `WWW-Authenticate`.

Actual: `401 Unauthorized`; empty `username:password` was rejected.

Finding: none.

### Test PEN-003: Auth bypass via malformed Basic token

Command:

```bash
curl -i -H "Authorization: Basic notbase64" "$target/"
```

Expected: `401 Unauthorized` with `WWW-Authenticate`.

Actual: `401 Unauthorized`; malformed base64 was rejected before credential comparison.

Finding: none.

### Test PEN-004: Auth bypass via wrong password

Command:

```bash
curl -i -H "Authorization: Basic $bad_auth" "$target/"
```

Expected: `401 Unauthorized` with `WWW-Authenticate`.

Actual: `401 Unauthorized`; wrong password was rejected.

Finding: none.

### Test PEN-005: CSRF bypass via authenticated POST without token

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Content-Type: application/json" \
  -d '{"duration_minutes":30,"reason":"pen test"}' \
  "$target/maintenance/start"
```

Expected: `403 Forbidden`; maintenance state unchanged.

Actual: `403 Forbidden` with body `csrf token missing or mismatched`.

Finding: none.

### Test PEN-006: CSRF bypass via mismatched cookie/header pair

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Cookie: __Host-csrf=one" \
  -H "X-CSRF-Token: two" \
  -H "Content-Type: application/json" \
  -d '{"duration_minutes":30,"reason":"pen test"}' \
  "$target/maintenance/start"
```

Expected: `403 Forbidden`; maintenance state unchanged.

Actual: `403 Forbidden` with body `csrf token missing or mismatched`.

Finding: none.

### Test PEN-007: CSRF whitelist bypass attempt through static prefix traversal

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -X POST \
  "$target/static/../maintenance/start"
```

Expected: request must not execute the maintenance route.

Actual: `404 Not Found`; no maintenance change occurred and the request did not reach the
`POST /maintenance/start` handler.

Finding: none.

### Test PEN-008: Rate-limit enforcement for repeated auth failures

Command:

```bash
for i in $(seq 1 100); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "Authorization: Basic $bad_auth" \
    "$target/"
done | sort | uniq -c
```

Expected: initial failures return `401`; requests above the configured burst window return
`429` with `Retry-After: 60`.

Actual: first seven failures returned `401`; subsequent failures returned `429` with
`Retry-After: 60`.

Finding: none.

### Test PEN-009: Rate-limit bypass via spoofed X-Forwarded-For

Command:

```bash
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -H "Authorization: Basic $bad_auth" \
    -H "X-Forwarded-For: 198.51.100.$i" \
    "$target/"
done
```

Expected: spoofed `X-Forwarded-For` from an untrusted peer must not rotate rate-limit buckets.

Actual: the app ignored spoofed `X-Forwarded-For` because the peer was not configured as a
trusted proxy; requests above the local peer limit returned `429`.

Finding: none.

### Test PEN-010: Command injection through locate path parameters

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Cookie: __Host-csrf=$csrf" \
  -H "X-CSRF-Token: $csrf" \
  -X POST \
  "$target/drives/2%3Btouch%20tmp:0/locate/start"
```

Expected: `400` or `404`; no `storcli` call.

Actual: `400 Bad Request`; invalid enclosure text was rejected before command construction.

Finding: none.

### Test PEN-011: Replace insert without prior missing step

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Cookie: __Host-csrf=$csrf" \
  -H "X-CSRF-Token: $csrf" \
  -H "Content-Type: application/json" \
  -d '{"serial_number":"NEW-DRIVE-123","dry_run":true}' \
  "$target/drives/2:0/replace/insert"
```

Expected: `409 Conflict`; no `storcli` call.

Actual: `409 Conflict` with error `must complete replace step missing before insert`.

Finding: none.

### Test PEN-012: Replace flow serial mismatch

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Cookie: __Host-csrf=$csrf" \
  -H "X-CSRF-Token: $csrf" \
  -H "Content-Type: application/json" \
  -d '{"serial_number":"WRONG-SERIAL","dry_run":true}' \
  "$target/drives/2:0/replace/offline"
```

Expected: `409 Conflict`; response must not reveal the canonical drive serial.

Actual: `409 Conflict` with error `serial mismatch`; canonical serial was not echoed.

Finding: none.

### Test PEN-013: Maintenance start with absurd duration

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Cookie: __Host-csrf=$csrf" \
  -H "X-CSRF-Token: $csrf" \
  -H "Content-Type: application/json" \
  -d '{"duration_minutes":999999,"reason":"pen test"}' \
  "$target/maintenance/start"
```

Expected: `422 Unprocessable Entity`; maintenance state unchanged.

Actual: `422 Unprocessable Entity`; pydantic validation enforced the `1..1440` minute range.

Finding: none.

### Test PEN-014: SQL injection through events category filter

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  "$target/events?category=operator_action%27%20OR%201%3D1--"
```

Expected: `200 OK` or empty filtered page; injected text must not broaden query results or
produce SQL errors.

Actual: `200 OK`; no SQL error, no broadened results. The filter is passed as a SQLAlchemy
bound value.

Finding: none.

### Test PEN-015: XSS through stored event message

Command:

```bash
sqlite3 /var/lib/megaraid-dashboard/megaraid.db \
  "insert into events (occurred_at,severity,category,subject,summary) values \
  (datetime('now'),'critical','system','xss','<script>alert(1)</script> PD 2:0');"
curl -s -H "Authorization: Basic $auth" "$target/events" | grep -E "script|&lt;script"
```

Expected: event text rendered escaped; no executable `<script>` tag in HTML.

Actual: the page rendered `&lt;script&gt;alert(1)&lt;/script&gt;` and the slot-link filter
escaped non-slot text before adding the drive link.

Finding: none.

### Test PEN-016: Static path traversal

Command:

```bash
curl -i -H "Authorization: Basic $auth" "$target/static/../etc/passwd"
```

Expected: `404 Not Found`; no host file disclosure.

Actual: `404 Not Found`; static serving remained constrained to the package static directory.

Finding: none.

### Test PEN-017: Audit-log forgery through public API

Command:

```bash
curl -i \
  -H "Authorization: Basic $auth" \
  -H "Cookie: __Host-csrf=$csrf" \
  -H "X-CSRF-Token: $csrf" \
  -H "Content-Type: application/json" \
  -d '{"category":"operator_action","summary":"fake admin action"}' \
  "$target/events"
```

Expected: no public event-write endpoint; request rejected.

Actual: `405 Method Not Allowed`; operator-action events are written only by service code paths
such as maintenance, locate, replace, and rebuild observation.

Finding: none. Direct DB writes can forge audit rows, but that requires local database access and
is covered by host filesystem permissions rather than the web API.

## Findings

| ID | Severity | Status | Details | Remediation |
| --- | --- | --- | --- | --- |
| PEN-F-001 | Info | Open | No tagged known-bad release exists for replay validation. | Create release tags before future quarterly audits. |
| PEN-F-002 | Info | Accepted | Direct SQLite access can forge audit rows by design; this is a local-host trust boundary. | Keep DB path restricted to `raid-monitor` and root. |

No exploitable web findings were identified in this penetration pass. No remediation MICRO PRs
were filed.

## Remediation Status

- MICRO PRs filed: none.
- Follow-up candidate: add a future release/tag discipline so the next audit can replay these
  commands against a known-bad version when a real historical vulnerability exists.

## Quarterly Re-run

Schedule a quarterly reminder for the first Monday of each quarter:

```text
Re-run MegaRAID Dashboard security audits:
- docs/SECURITY-AUDIT-2026-Q2.md process
- docs/SECURITY-AUDIT-2026-Q2-pen.md curl penetration cases
- scripts/security-scan.sh
```
