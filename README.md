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

## Web UI

The read-only server-side rendered UI has no frontend build step and no npm dependency.
Routes:

- `/` renders the Overview page with the latest controller, virtual drive, CacheVault,
  and physical drive snapshot.
- `/partials/overview` renders only the Overview data block used by HTMX refreshes.
- `/drives` is a placeholder route that currently redirects to `/`.
- `/events` renders a Coming soon empty state.
- `/health` returns the health JSON used by smoke checks.

Static assets are mounted separately at `/static` with far-future cache headers:

- `src/megaraid_dashboard/static/css/app.css` contains the vanilla CSS.
- `src/megaraid_dashboard/static/vendor/htmx.min.js` vendors HTMX 2.0.x from the
  official release. The file is local, so CDN loading and SRI are deliberately not used.

## Reverse Proxy

The UI supports deployment behind a path prefix such as
`https://server.alexbomber.com/raid/`. An ASGI middleware reads `X-Forwarded-Prefix`
and assigns it to `scope["root_path"]` before FastAPI handles the request. Templates
generate links and static asset paths through `request.url_for`, so they render with
`/raid` in production and without a prefix locally. FastAPI keeps its default trailing
slash behavior.

Example nginx location:

```nginx
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
|       |   |-- event_detector.py
|       |   |-- overview.py
|       |   `-- scheduler.py
|       |-- static/
|       |   |-- css/
|       |   |   `-- app.css
|       |   `-- vendor/
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
|       |   |   |-- events.html
|       |   |   `-- overview.html
|       |   `-- partials/
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

## Roadmap

1. [x] Skeleton and CI.
2. [x] `storcli` wrapper with JSON parsing and pydantic models.
3. [x] SQLite schema and migrations.
4. [x] Background metrics collector.
5. Read-only web dashboard.
6. Email alerts via SMTP.
7. Basic auth.
8. Maintenance mode for locate LED, alarm, patrol read, and consistency check.
9. Destructive mode for drive replace workflow.
10. Production deployment with systemd and nginx.

## Status

Active development, not production-ready yet.
