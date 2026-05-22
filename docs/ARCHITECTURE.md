# MegaRAID Dashboard Architecture

```
                +-------------+
                |  storcli64  |
                +------+------+
                       |
           [ collector job, 300 s interval ]
                       |
                +------v------+        +-------------+
                |  parser     +------->|  SQLite DB  |
                +------+------+        +------+------+
                       |                      |
                +------v------+               |
                |   event     +---------------+
                |  detector   |
                +------+------+               |
                       |                      |
                +------v------+        +------v------+
                |   notifier  |        |   web app   |
                +------+------+        +------+------+
                       |                      |
                       v                      v
                  Proton SMTP            nginx /raid/
                                              |
                                      operator browser
```

MegaRAID Dashboard is a single-host FastAPI application that samples one LSI MegaRAID
controller through `storcli64 -J`, stores validated snapshots in SQLite, detects state
changes, sends email alerts, and renders a server-side dashboard for operators. The system is
intentionally small: `storcli` remains the hardware boundary, SQLite is the durable local
state, and nginx owns the public HTTP boundary.

## Components

### Storcli Wrappers

All controller reads and writes go through `src/megaraid_dashboard/storcli/`. The wrapper
executes `storcli64` with JSON output, optionally through sudo, then validates the returned
payload into pydantic models. Application code does not parse textual tables and does not
call `storcli64` directly.

The wrapper is the stability boundary for the project. Kernel and OS upgrades can change
legacy ioctl behavior, but the application depends on Broadcom's JSON CLI contract instead.

Command builders for controller and drive operations live in
`src/megaraid_dashboard/services/drive_actions.py`. They construct explicit argv lists for
locate LED, replacement, rebuild status, patrol read, consistency check, foreign config,
controller buzzer, and advanced drive actions. Route handlers choose a builder, validate the
requested state transition, then call `run_storcli`; no route should assemble a free-form
`storcli` string or call subprocess directly.

### Collector

The collector is the scheduled job that turns controller state into database rows. On each
cycle it reads controller, virtual drive, physical drive, CacheVault, and optional BBU data
from `storcli64`, parses the payloads, stores a snapshot, and asks the event detector to
compare the new snapshot with the previous one.

The default interval is controlled by `METRICS_INTERVAL_SECONDS`. Production deployments use
a process-wide file lock so only one collector runs when the ASGI app has multiple workers.

### Parser

Parser functions convert raw `storcli -J` payloads into typed controller, virtual drive,
physical drive, CacheVault, and BBU objects. Parsing happens before persistence so the rest
of the application sees normalized fields such as drive state, serial number, temperature,
error counters, and controller firmware details.

Raw JSON can be stored for diagnostics when `STORE_RAW_SNAPSHOT_PAYLOAD` is enabled, but the
normal application path uses the validated fields.

### Event detector

The detector compares the latest stored snapshot with the new snapshot. It records events for
controller alarm changes, virtual drive state changes, physical drive state changes, drive
replacement, SMART alerts, error counter increases, drive temperature thresholds, controller
RoC temperature thresholds, CacheVault state changes, CacheVault capacitance degradation, disk
space checks, and collection recovery.

Temperature state uses hysteresis so warning and critical events clear only after temperature
falls below the configured clear threshold.

### Notifier

The notifier scans unsent events, applies severity filtering, deduplication, suppression
windows, per-category overrides, throttling warnings, and maintenance-mode skips, then sends
email through the configured SMTP transport. Delivery marks events with `notified_at` so the
same event is not repeatedly sent.

Maintenance mode suppresses notification delivery without suppressing event recording. That
keeps the audit trail intact during noisy repair work.

### Web app

The web app is a FastAPI application with server-rendered Jinja templates and small local
JavaScript helpers. It renders the Overview, Drives, Drive Detail, Events, maintenance, drive
locate, replacement, rebuild polling, and health routes. Routes stay thin by loading view
models from service modules and delegating write command construction to drive-action helpers.

The production HTTP boundary is nginx. nginx provides the public `/raid/` path, HTTP basic
auth, TLS, and trusted forwarding headers. FastAPI also includes auth, CSRF, rate limiting,
and path-prefix handling for defense in depth and local deployments.

### View Model Layer

The redesigned UI uses per-page view models instead of route-local template dictionaries.
The main page is assembled by `services/overview.py`, the controller detail page by
`services/controller_detail.py`, and the drive detail page by `services/drive_detail.py`.
Each service loads database state, derives display-ready labels and URLs, and returns frozen
dataclasses consumed by Jinja templates.

This keeps FastAPI routes thin: routes parse path/query/body values, call one service
function, and render a template or JSON response. Business rules such as drive-state labels,
temperature severity, backplane tile severity, active-operation summaries, and chart series
selection belong in service modules where they can be unit tested without a browser.

### HTMX Polling

The main dashboard page uses HTMX polling for live updates. Initial page load renders the
full `pages/main.html` template, including the header, page chrome, status bar, and the
refreshable main-page content.

The refreshable region requests `GET /partials/main-page` every 30 seconds with
`hx-swap="innerHTML"`. That route is authenticated like the full page, sets
`Cache-Control: no-cache`, reloads the same main-page view model, and renders only
`partials/main_page_refresh.html`. The partial contains the controller summary card, drive
grid, and recent activity timeline; it deliberately excludes page chrome and maintenance
controls.

This keeps live updates on the stable server-rendered HTML path without adding SSE or
WebSocket state. Polling continues while the browser tab is in the background; tab visibility
pausing is a future optimization if the request volume becomes a problem.

The polling interval is 30 seconds by default. It is intentionally a snapshot refresh, not a
real-time hardware stream; operators should expect UI state to lag the controller until the
collector and the next HTMX refresh complete.

### State Machine

Drive write routes enforce state transitions before running `storcli` commands. Replacement
actions require the expected step order: set offline, set missing, physically replace the
drive, insert the replacement, and then poll rebuild status. The missing step is accepted
only from `Offln`, while the offline step is limited to known controller states where the
operation is meaningful.

Advanced drive actions use smaller validators:

- `Mark UBad` accepts only a current `UGood` drive.
- `Mark UGood` accepts only a current `UBad` drive.
- `Spin Down` accepts `Onln`, `UGood`, or `UBad`.
- `Make Hot Spare` accepts `UGood` and requires that the requested disk group exists.

The route layer validates both the latest stored snapshot and the live drive returned by
`storcli /c0/eN/sN show all J`. This prevents a stale UI snapshot from authorizing an
operation against a different live drive state.

### Retention

Retention is part of the scheduled service. Raw physical-drive snapshots are retained at full
resolution for the configured raw window, downsampled into hourly buckets for the configured
hourly window, and then downsampled into daily buckets for long-term history. Events and audit
logs are retained indefinitely.

The drive detail page merges raw, hourly, and daily layers with higher-resolution data taking
priority where windows overlap.

### Exporter

Prometheus exporter work is planned as the monitoring extension point. It should read already
persisted and validated application state, not call `storcli64` itself. Keeping the exporter
behind the database boundary prevents monitoring scrapes from adding controller load or
creating a second hardware access path.

## Database Schema

`controller_snapshots`
: One row per collector sample. Key columns include `captured_at`, controller model and
serial, firmware, BIOS, driver, alarm state, CacheVault/BBU presence, RoC temperature, and
optional raw JSON.

`vd_snapshots`
: Virtual drive rows attached to a controller snapshot. Key columns include `snapshot_id`,
`vd_id`, name, RAID level, size, state, access, and cache policy.

`pd_snapshots`
: Physical drive rows attached to a controller snapshot. Key columns include `snapshot_id`,
enclosure, slot, device id, model, serial number, firmware, size, interface, media type,
state, disk group, temperature, error counters, SMART alert, and SAS address.

`cv_snapshots`
: CacheVault rows attached one-to-one to a controller snapshot. Key columns include type,
state, temperature, pack energy, capacitance, replacement-required flag, and next learn cycle.

`pd_temp_states`
: Current temperature alert state by enclosure, slot, and serial number. This table lets the
detector apply hysteresis across collector cycles.

`pd_metrics_hourly`
: Downsampled hourly physical-drive metrics. Key columns include bucket start, enclosure,
slot, serial number, temperature min/max/avg, error counter maxima, and sample count.

`pd_metrics_daily`
: Downsampled daily physical-drive metrics with the same shape as hourly metrics. This table
backs long-range drive history after hourly data ages out.

`events`
: Operator-visible event stream. Key columns include occurrence time, severity, category,
subject, summary, before/after JSON, operator username for operator-action events, and
`notified_at` for email delivery state.

`audit_logs`
: Detailed records for storcli write operations. Key columns include actor, action, target,
command argv, exit code, stdout/stderr tails, duration, and success flag.

`alerts_sent`
: Legacy alert-send tracking table retained by the schema. Event notification state currently
lives on `events.notified_at`.

`system_state`
: Small key-value table for application state such as maintenance mode, expiry, and actor.

Relationships are deliberately simple. Controller snapshots own virtual drive, physical drive,
and CacheVault snapshot rows. Events, audit logs, temperature states, retention buckets, and
system state are independent operational tables.

## External Dependencies

`storcli64`
: Hardware control and telemetry boundary. The configured path is `STORCLI_PATH`, normally
`/usr/local/sbin/storcli64` in production.

`sudo`
: Optional privilege boundary for `storcli64`. Production uses a narrow sudoers file that
allows exact JSON storcli commands for the unprivileged `raid-monitor` user.

SQLite
: Durable local store for snapshots, events, audit logs, maintenance state, and metric
rollups. The production service default is
`sqlite:////var/lib/megaraid-dashboard/megaraid.db`.

SMTP
: Email alert transport. Configuration comes from environment settings such as SMTP host,
port, user, password, sender, recipient, STARTTLS, threshold, and suppression windows.

nginx
: Public reverse proxy. It owns TLS, HTTP basic auth, and the `/raid/` path prefix. See
`docs/PROXY-SETUP.md` for deployment details.

Prometheus
: Planned metrics consumer. It should scrape the application exporter after that component is
added, not scrape `storcli64` directly.

systemd and journald
: Service supervision and logging. The sample unit runs the app as `raid-monitor`, applies
filesystem hardening, runs preflight before startup, and writes logs to a dedicated journald
namespace.

## State Locations

`/var/lib/megaraid-dashboard/megaraid.db`
: Production SQLite database. Owned by `raid-monitor:raid-monitor` and writable by the
service through the systemd `ReadWritePaths` rule.

`/etc/megaraid-dashboard/env`
: Runtime configuration and secrets loaded by pydantic settings through the systemd
`EnvironmentFile`. Owned by root with restricted group access for the service.

`/etc/sudoers.d/megaraid-dashboard`
: Narrow storcli command whitelist for the service user. Owned by root and validated with
normal sudoers tooling.

`/opt/megaraid-dashboard/`
: Application install prefix. Contains the virtual environment, copied source tree, scripts,
and service entry point used by systemd.

`/opt/megaraid-dashboard/.venv`
: Python virtual environment used by the service.

`/opt/megaraid-dashboard/src`
: Installed source checkout used by the editable package install.

`/tmp/megaraid-dashboard-collector.lock`
: Process-wide collector lock unless `COLLECTOR_LOCK_PATH` overrides it. The default keeps
non-installed runs (e.g. README quick start) working without privileged setup. The
production installer overrides the value to `/var/lib/megaraid-dashboard/collector.lock`
inside the data directory (mode `0750`, owned by the service user) so that no other local
user can pre-create the lock file and deny the service its acquisition.

`/tmp/megaraid-dashboard-notifier.lock`
: Notifier lock path used to prevent overlapping notifier cycles.

`/etc/systemd/system/megaraid-dashboard.service`
: Local installed systemd unit, based on `deploy/megaraid-dashboard.service`.

`/etc/systemd/journald.conf.d/megaraid-dashboard.conf`
: Optional journald retention drop-in based on `deploy/journald-megaraid.conf`.

nginx site configuration
: Local path depends on the host's nginx layout. The sample config is
`deploy/nginx/megaraid.conf.sample`.

## Request Flow

1. nginx receives the operator request at `/raid/`.
2. nginx authenticates the operator and forwards to FastAPI with the trusted path prefix.
3. FastAPI route handlers load view models from SQLite through service modules.
4. Templates render HTML fragments or pages.
5. HTMX refreshes partials for overview, events, maintenance state, and rebuild progress.

Write requests follow the same HTTP path, but they also require maintenance-mode state,
destructive-mode state where applicable, typed serial-number confirmation, command building
through service helpers, execution through the storcli wrapper, and audit recording.

## Operational Boundaries

The hardware boundary is `storcli64 -J`. The durable state boundary is SQLite. The public
network boundary is nginx. The privilege boundary is the `raid-monitor` user plus exact
sudoers commands. The code boundary is that routes orchestrate requests while services own
business logic and the storcli wrapper owns subprocess execution.

Those boundaries are what keep the application maintainable: changes to the UI, alerting,
monitoring, or history views do not need to learn how to talk to controller hardware.
