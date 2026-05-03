# MegaRAID Dashboard

MegaRAID Dashboard is a web dashboard and email alerter for LSI MegaRAID controllers, JSON-driven via `storcli`, intended as a sustainable replacement for the unmaintained MegaRAID Storage Manager (MSM).

## Why

MSM has been unmaintained since 2018 and is broken on modern Linux kernels. `storcli` is supported by Broadcom and is stable across kernel and OS upgrades.

## Hardware Tested

- LSI MegaRAID SAS 9270CV-8i (chip SAS 2208)
- Ubuntu 24.04
- Kernel 6.8
- `megaraid_sas` driver

## Requirements

- Python 3.12
- `storcli64` in `PATH`
- MegaRAID controller accessible to the host
- `sudo` with a whitelist of `storcli` commands when write operations are enabled

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m megaraid_dashboard
```

The local development UI is served at <http://127.0.0.1:8090/>.

## Development

```bash
ruff check .
ruff format .
mypy src
pytest
```

## Database

SQLite stores controller, virtual drive, physical drive, and cache vault snapshots. Physical
drive snapshots are retained at full 5-minute resolution for 30 days, then downsampled into
`pd_metrics_hourly` for 1 year, then into `pd_metrics_daily` indefinitely. Events and audit
logs are retained forever.

### Operations

Production service units should run `scripts/preflight.sh` with systemd `ExecStartPre`
before starting FastAPI. The script runs `alembic upgrade head` and probes SQLite
writability by creating and dropping a temporary `_preflight` table. If `DATABASE_URL` is not
set by the service environment, it falls back to `sqlite:///./megaraid.db`.

See `deploy/megaraid-dashboard.service` for a sample unit fragment that wires the preflight
script into startup. The sample unit sets a production default of
`sqlite:////var/lib/megaraid-dashboard/megaraid.db`, which is covered by its systemd
`ReadWritePaths` hardening rule.

Reverse proxy: see `docs/PROXY-SETUP.md` and `deploy/nginx/megaraid.conf.sample`.

Install: see `scripts/install.sh` for the current installer scaffold. The installer creates
the application virtual environment at `/opt/megaraid-dashboard/.venv`, copies the source
checkout into `/opt/megaraid-dashboard/src`, and installs that copied tree as an editable
package. `INSTALL.md` is forthcoming.

### Log retention

Logs go to systemd journald. To cap this unit's journal at 200 MB on disk, copy
`deploy/journald-megaraid.conf` to a journald drop-in directory and add
`LogNamespace=megaraid-dashboard` to the unit's `[Service]` section. See
`deploy/journald-megaraid.conf` header comments for exact paths. Inspect:
`journalctl --namespace=megaraid-dashboard --since=today`.

### History Aggregation

Drive detail graphs read history from three layers: raw `pd_snapshots` joined to
`controller_snapshots.captured_at`, hourly `pd_metrics_hourly`, and daily
`pd_metrics_daily`. The UI merges them with raw points taking priority over hourly buckets,
and hourly buckets taking priority over daily buckets, so overlapping windows are shown once
at the highest available resolution.

For a selected drive, history normally matches enclosure, slot, and the latest serial number.
If a replacement occurred inside the requested window, the loader falls back to enclosure and
slot matching and returns a replacement marker for the graph. During the first 30 days of
operation, 30-day charts may contain only raw data. During the first year, 365-day charts may
contain raw plus hourly data before daily buckets exist. Those partial-layer cases are
expected and still use the same merge priority.

## Background Collector

When enabled, the background collector runs `storcli` every `metrics_interval_seconds`
(default 300), persists a new snapshot, and emits events for controller alarm changes,
virtual drive state changes, physical drive state changes, error counter increases, SMART
alerts, temperature threshold transitions, drive replacement, CacheVault state changes, and
CacheVault capacitance degradation. Retention runs daily at 03:30 UTC.

FastAPI lifespan uses `COLLECTOR_LOCK_PATH` to acquire a process-wide file lock before
starting APScheduler, so multi-worker deployments keep a single active collector.

### Temperature Thresholds

Physical drive temperature warning, critical, and hysteresis thresholds default to 55 C,
60 C, and 5 C. Configure them with `TEMP_WARNING_CELSIUS`, `TEMP_CRITICAL_CELSIUS`, and
`TEMP_HYSTERESIS_CELSIUS`.

RoC temperature warning, critical, and hysteresis thresholds default to 95 C, 105 C, and
5 C. Configure them with `ROC_TEMP_WARNING_CELSIUS`, `ROC_TEMP_CRITICAL_CELSIUS`, and
`ROC_TEMP_HYSTERESIS_CELSIUS`. After the heatsink mod, expected thresholds are 80 C and
95 C.

## Web UI

The read-only server-side rendered UI has no frontend build step and no npm dependency.
Routes:

- `/` renders the Overview page with the latest controller, virtual drive, CacheVault,
  and physical drive snapshot.
- `/partials/overview` renders only the Overview data block used by HTMX refreshes.
- `/drives` renders a physical drive list with links to drive detail pages.
- `/drives/{enclosure_id}/{slot_id}` renders the Drive Detail page with current drive
  attributes, temperature history, and error counter history.
- `/drives/{enclosure_id}/{slot_id}/charts` renders only the chart fragment used by the
  Drive Detail range selector.
- `/events` renders the read-only events log page.
- `/audit` redirects to `/events?category=operator_action` for the operator-action audit log.
- `/partials/events` renders the events fragment used by HTMX auto-refresh and Load more
  pagination.
- `/health` returns the health JSON used by smoke checks.
- `/healthz` returns JSON liveness; 200 ok / 503 degraded; whitelisted from auth.

Events are retained indefinitely while raw controller snapshots are pruned after 30 days.
That means the Events page can show event history older than the oldest retained raw
snapshot.

Static assets are mounted separately at `/static` with far-future cache headers:

- `src/megaraid_dashboard/static/css/app.css` contains the vanilla CSS.
- `src/megaraid_dashboard/static/vendor/htmx.min.js` vendors HTMX 2.0.x from the
  official release. The file is local, so CDN loading and SRI are deliberately not used.
- `src/megaraid_dashboard/static/vendor/chart.min.js` vendors the Chart.js 4.x UMD
  minified release from the official `chartjs/Chart.js` GitHub release asset. It is loaded
  only by the Drive Detail page, not the global layout, so overview pages avoid the parse
  cost.

Template asset URLs include a content-derived `v` query so far-future caches are refreshed
after CSS or vendored JS changes.

## Reverse Proxy

The UI supports deployment behind a path prefix such as
`https://server.alexbomber.com/raid/`. An ASGI middleware reads `X-Forwarded-Prefix`
and assigns it to `scope["root_path"]` before FastAPI handles the request. Templates
generate links and static asset paths through `request.url_for`, so they render with
`/raid` in production and without a prefix locally. FastAPI keeps its default trailing
slash behavior.

Example nginx location:

```nginx
location = /raid {
    return 301 /raid/;
}

location /raid/ {
    proxy_pass http://127.0.0.1:8090/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Prefix /raid;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
}
```

The `proxy_set_header X-Forwarded-Prefix /raid` line overwrites any client-supplied
`X-Forwarded-Prefix` value and sets the trusted prefix server-side.

## Project Layout

```text
.
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- migrations/
|   |-- env.py
|   `-- versions/
|       |-- 0001_initial.py
|       `-- 0002_pd_temp_state.py
|-- src/
|   `-- megaraid_dashboard/
|       |-- __init__.py
|       |-- __main__.py
|       |-- app.py
|       |-- config.py
|       |-- db/
|       |   |-- __init__.py
|       |   |-- base.py
|       |   |-- dao.py
|       |   |-- engine.py
|       |   |-- models.py
|       |   `-- retention.py
|       |-- services/
|       |   |-- __init__.py
|       |   |-- collector.py
|       |   |-- drive_history.py
|       |   |-- event_detector.py
|       |   |-- events.py
|       |   |-- overview.py
|       |   `-- scheduler.py
|       |-- static/
|       |   |-- css/
|       |   |   `-- app.css
|       |   `-- vendor/
|       |       |-- chart.min.js
|       |       `-- htmx.min.js
|       |-- storcli/
|       |   |-- __init__.py
|       |   |-- exceptions.py
|       |   |-- models.py
|       |   |-- parser.py
|       |   `-- runner.py
|       |-- templates/
|       |   |-- layouts/
|       |   |   `-- base.html
|       |   |-- pages/
|       |   |   |-- drive_detail.html
|       |   |   |-- drives.html
|       |   |   |-- events.html
|       |   |   `-- overview.html
|       |   `-- partials/
|       |       |-- drive_charts.html
|       |       |-- events_data.html
|       |       |-- events_table.html
|       |       |-- physical_drive_table.html
|       |       `-- overview_data.html
|       `-- web/
|           |-- __init__.py
|           |-- middleware.py
|           |-- routes.py
|           |-- static.py
|           `-- templates.py
|-- tests/
|   |-- fixtures/
|   |   `-- storcli/
|   |       |-- redact.py
|   |       `-- redacted/
|   |           |-- bbu_show_all.json
|   |           |-- c0_show_all.json
|   |           |-- cv_show_all.json
|   |           |-- eall_sall_show_all.json
|   |           `-- vall_show_all.json
|   |-- test_db/
|   |   |-- __init__.py
|   |   |-- test_alembic.py
|   |   |-- test_dao.py
|   |   |-- test_models.py
|   |   `-- test_retention.py
|   |-- test_services/
|   |   |-- __init__.py
|   |   |-- test_collector.py
|   |   |-- test_drive_history.py
|   |   |-- test_event_detector.py
|   |   |-- test_overview.py
|   |   `-- test_scheduler.py
|   |-- test_storcli/
|   |   |-- __init__.py
|   |   |-- test_parser.py
|   |   |-- test_redactor.py
|   |   `-- test_runner.py
|   |-- test_web/
|   |   |-- test_routes.py
|   |   `-- test_templates.py
|   |-- __init__.py
|   |-- conftest.py
|   |-- test_config.py
|   `-- test_smoke.py
|-- .env.example
|-- .gitignore
|-- AGENTS.md
|-- alembic.ini
|-- CLAUDE.md
|-- LICENSE
|-- README.md
`-- pyproject.toml
```

## Alerts (email)

### Configuration

| Variable | Type | Default | Description |
| --- | --- | --- | --- |
| `ALERT_SMTP_HOST` | str | required | SMTP submission host. |
| `ALERT_SMTP_PORT` | int | required | SMTP submission port (typically 587 for STARTTLS). |
| `ALERT_SMTP_USER` | str | required | SMTP username. |
| `ALERT_SMTP_PASSWORD` | str | required | SMTP password or token. Loaded from `.env`, never commit. |
| `ALERT_FROM` | str | required | Envelope and header `From` address. |
| `ALERT_TO` | str | required | Default recipient address. |
| `ALERT_SMTP_USE_STARTTLS` | bool | `true` | If true, the transport runs STARTTLS over the submission port after the initial EHLO. |
| `ALERT_SEVERITY_THRESHOLD` | str | `critical` | Lowest severity that triggers an alert. One of `info`, `warning`, `critical`. Consumed by the notifier. |
| `ALERT_SUPPRESS_WINDOW_MINUTES` | int | `60` | Minutes to suppress duplicate alerts for the same `(severity, category, subject)`. Consumed by the notifier. |
| `ALERT_THROTTLE_PER_HOUR` | int | `20` | Soft cap on alerts sent per trailing hour. The notifier logs a warning when exceeded but continues sending. |
| `DISK_WARNING_FREE_MB` | int | `500` | Warning threshold for free space on the partition containing `DATABASE_URL`; must be positive and above critical. |
| `DISK_CRITICAL_FREE_MB` | int | `100` | Critical threshold for free space on the partition containing `DATABASE_URL`; must be positive and below warning. |
| `DISK_CHECK_INTERVAL_MINUTES` | int | `60` | Minutes between free-space probes. Each probe can emit at most one `disk_space` event. |
| `ROC_TEMP_WARNING_CELSIUS` | int | `95` | RoC warning threshold in C; must be in `[40, 130]` and below critical. |
| `ROC_TEMP_CRITICAL_CELSIUS` | int | `105` | RoC critical threshold in C; must be in `[40, 130]` and above warning. |
| `ROC_TEMP_HYSTERESIS_CELSIUS` | int | `5` | RoC hysteresis in C; must be at least 1 and below warning. |

After the RoC heatsink mod, set `ROC_TEMP_WARNING_CELSIUS=80` and
`ROC_TEMP_CRITICAL_CELSIUS=95`.

### CLI

```
python -m megaraid_dashboard.alerts test
python -m megaraid_dashboard.alerts test --to other@example.com
```

The `test` command sends one fixed test message synchronously and exits. It bypasses
any future suppression window or throttle, so operators can verify SMTP credentials,
DNS records (SPF, DKIM, DMARC), and outbound network access without waiting for a
real event.

### Notifier

The notifier runs inside the same APScheduler instance as the metrics collector and
fires on a 60-second interval whenever `COLLECTOR_ENABLED=true`. Each cycle:

- selects pending events whose `severity` is at or above `ALERT_SEVERITY_THRESHOLD`
  (severities are ordered `info` < `warning` < `critical`, so `warning` includes both
  `warning` and `critical`) and whose `occurred_at` is within the trailing
  `ALERT_SUPPRESS_WINDOW_MINUTES` window;
- skips an event when a prior event with the same `(severity, category, subject)` was
  already notified within that window — the current event is still marked notified so
  it does not re-appear in the next cycle;
- sends one plain-text email per remaining event to `ALERT_TO` via the SMTP transport,
  marks the event notified, and commits once at the end of the cycle;
- counts notifications already sent in the trailing hour and emits a single
  `notifier_throttle_warning` log line when that count exceeds
  `ALERT_THROTTLE_PER_HOUR`. The cap is soft: dedup is the primary defence and sends
  continue regardless;
- isolates per-event SMTP failures (events with a failed `transport.send` keep
  `notified_at = NULL` and are retried on the next cycle).

A file lock at `/tmp/megaraid-dashboard-notifier.lock` prevents overlapping cycles when
a slow SMTP send pushes one cycle past 60 seconds; the next cycle logs
`notifier_overlap_skipped` and returns early.

The overview page shows a compact alert-status row with the last alert sent timestamp,
pending alert count, sent-in-last-hour count, and a notifier-health badge for quick
operator checks.

### Example .env block

```
ALERT_SMTP_HOST=smtp.protonmail.ch
ALERT_SMTP_PORT=587
ALERT_SMTP_USE_STARTTLS=true
ALERT_SMTP_USER=alert@yourdomain.example
ALERT_SMTP_PASSWORD=<proton-smtp-token>
ALERT_FROM=alert@yourdomain.example
ALERT_TO=ops@yourdomain.example
ALERT_SEVERITY_THRESHOLD=critical
ALERT_SUPPRESS_WINDOW_MINUTES=60
ALERT_THROTTLE_PER_HOUR=20
```

### Risk

SMTP credentials live in `.env`, which must remain gitignored. The transport blocks the
calling thread for up to 30 seconds on network IO and must NOT be invoked from a FastAPI
request handler; the notifier runs from an APScheduler executor. STARTTLS over port 587
is the only encryption mode supported; SMTPS over port 465 (implicit TLS) is out of
scope. The CLI `test` command bypasses the suppression window and throttle and sends
unconditionally. `ALERT_SEVERITY_THRESHOLD` and `ALERT_SUPPRESS_WINDOW_MINUTES` are now
consumed by the notifier cycle; `ALERT_THROTTLE_PER_HOUR` acts as a soft cap (warn on
exceed, continue sending) so a multi-event incident is not dropped when dedup already
collapses repeats.

## Roadmap

1. [x] Skeleton and CI.
2. [x] `storcli` wrapper with JSON parsing and pydantic models.
3. [x] SQLite schema and migrations.
4. [x] Background metrics collector.
5. [x] Read-only web dashboard.
6. Email alerts via SMTP.
7. Basic auth.
8. Maintenance mode for locate LED, alarm, patrol read, and consistency check.
9. Destructive mode for drive replace workflow.
10. Production deployment with systemd and nginx.

## Status

Active development, not production-ready yet.
