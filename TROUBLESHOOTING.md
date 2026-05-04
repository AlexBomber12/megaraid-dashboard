# MegaRAID Dashboard Troubleshooting

This document is the symptom-to-cause index for production deploy and runtime failures.
It assumes the standard Ubuntu 24.04 install from `INSTALL.md`: service user
`raid-monitor`, systemd unit `megaraid-dashboard.service`, SQLite database under
`/var/lib/megaraid-dashboard/`, config in `/etc/megaraid-dashboard/env`, and `storcli64`
at `/usr/local/sbin/storcli64`.

Use the section that matches what the operator sees. Run the diagnostics before applying
fixes so the recovery action matches the failure mode. For incident coordination and
longer-running recovery, use [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Service does not start

### Symptom

`systemctl status megaraid-dashboard.service --no-pager` shows `failed`, `activating`
until systemd times out, or repeated restarts. Recent journal logs usually include a
configuration validation error, bind error, database error, or preflight failure.

### Probable Causes

1. Config validation failed in `/etc/megaraid-dashboard/env`.
2. TCP port 8090, or the configured `APP_PORT`, is already bound by another process.
3. SQLite database path is missing, unreadable, read-only, or owned by the wrong user.

### Diagnostics

```bash
systemctl status megaraid-dashboard.service --no-pager
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 100 --no-pager
sudo systemctl show -p EnvironmentFiles -p ExecStart megaraid-dashboard.service
sudo systemd-run --wait --pipe --collect --uid=raid-monitor --property=EnvironmentFile=/etc/megaraid-dashboard/env /opt/megaraid-dashboard/.venv/bin/python -c 'from megaraid_dashboard.config import get_settings; print(get_settings().model_dump(exclude={"alert_smtp_password","admin_password_hash"}))'
ss -lntp | grep ':8090'
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db 'pragma integrity_check;'
sudo systemd-run --wait --pipe --collect --uid=raid-monitor --property=WorkingDirectory=/opt/megaraid-dashboard/src --property=EnvironmentFile=/etc/megaraid-dashboard/env /opt/megaraid-dashboard/src/scripts/preflight.sh
```

### Fixes

- Config validation error: re-run the installer wizard with
  `sudo bash scripts/install.sh --force-reconfigure`, or edit the exact failing setting
  with `sudoedit /etc/megaraid-dashboard/env`.
- Port already in use: identify the owner with `sudo lsof -i :8090`, stop the conflicting
  service, or reinstall with a free `APP_PORT`.
- Database permissions: restore ownership and directory access, then restart:

```bash
sudo install -d -m 0750 -o raid-monitor -g raid-monitor /var/lib/megaraid-dashboard
sudo chown raid-monitor:raid-monitor /var/lib/megaraid-dashboard/megaraid.db
sudo chmod 0640 /var/lib/megaraid-dashboard/megaraid.db
sudo systemctl restart megaraid-dashboard.service
```

## Healthz returns 503

### Symptom

`curl -fsS http://127.0.0.1:8090/healthz` exits non-zero, or `curl -i` shows HTTP 503
with a JSON body such as `{"status":"degraded","database":"error","collector":"ok"}`.

### Probable Causes

1. Database connection or SQLite writability failed.
2. Another process holds the collector lock, so this process cannot start the scheduler.
3. The scheduler was not started because the collector is disabled or startup failed.

### Diagnostics

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
curl -i "http://127.0.0.1:${APP_PORT:-8090}/healthz"
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 100 --no-pager
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db 'select 1;'
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db 'pragma integrity_check;'
sudo lsof /tmp/megaraid-dashboard-collector.lock
sudo grep -E '^(COLLECTOR_ENABLED|COLLECTOR_LOCK_PATH|DATABASE_URL)=' /etc/megaraid-dashboard/env
```

### Fixes

- Database `error`: fix the database path from `DATABASE_URL`, restore ownership to
  `raid-monitor:raid-monitor`, and run `scripts/preflight.sh` before restarting.
- Collector `lock_held`: stop the duplicate local app process or stale development
  server that owns `COLLECTOR_LOCK_PATH`, then restart the service.
- Collector `idle` with collection expected: set `COLLECTOR_ENABLED=true`, restart the
  unit, and confirm the `metrics_collector` job appears in logs after startup.

## No alerts arriving

### Symptom

The Events page shows warning or critical events, but the configured recipient receives no
email. The SMTP test may fail, or real events may stay visible without expected delivery.

### Probable Causes

1. SMTP host, port, username, token, sender, recipient, or STARTTLS setting is wrong.
2. A matching event was already notified inside `ALERT_SUPPRESS_WINDOW_MINUTES`.
3. Event severity is below `ALERT_SEVERITY_THRESHOLD`.
4. Maintenance mode is active, so notifier delivery is intentionally paused.
5. Alert throttling reached `ALERT_THROTTLE_PER_HOUR`.

### Diagnostics

```bash
sudo systemd-run --wait --pipe --collect \
  --uid=raid-monitor \
  --property=EnvironmentFile=/etc/megaraid-dashboard/env \
  /opt/megaraid-dashboard/.venv/bin/python -m megaraid_dashboard.alerts test
sudo grep -E '^(ALERT_SMTP_HOST|ALERT_SMTP_PORT|ALERT_SMTP_USER|ALERT_SMTP_USE_STARTTLS|ALERT_FROM|ALERT_TO|ALERT_SEVERITY_THRESHOLD|ALERT_SUPPRESS_WINDOW_MINUTES|ALERT_THROTTLE_PER_HOUR)=' /etc/megaraid-dashboard/env
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 200 --no-pager | grep -i 'alert\|smtp\|notifier\|maintenance\|throttle'
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select id,severity,category,summary,notified_at,created_at from events order by id desc limit 20;"
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select key,value from system_state where key='maintenance_mode';"
```

### Fixes

- SMTP failure: correct the SMTP settings in `/etc/megaraid-dashboard/env`, restart the
  service, then rerun the SMTP test command.
- Suppress window: wait for `ALERT_SUPPRESS_WINDOW_MINUTES` to pass, or lower the window
  only after confirming repeat alerts will not become noisy.
- Severity threshold: set `ALERT_SEVERITY_THRESHOLD=warning` if warning emails are
  required, then restart.
- Maintenance mode: stop the maintenance window from the UI after the work is complete.
- Throttle saturation: resolve the event flood first; for a bounded incident, raise
  `ALERT_THROTTLE_PER_HOUR` and restart after documenting the reason.

## Drive shows wrong state

### Symptom

The Drives page or drive detail page shows a state that does not match the controller CLI,
or it still shows the old state immediately after a physical change.

### Probable Causes

1. The collector has not completed a cycle since the controller state changed.
2. The page is showing the latest stored snapshot, which is stale after a service pause.
3. `storcli` is unavailable, blocked by sudoers, timing out, or returning invalid JSON.

### Diagnostics

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
curl -fsS "http://127.0.0.1:${APP_PORT:-8090}/healthz"
sudo grep -E '^(METRICS_INTERVAL_SECONDS|STORCLI_PATH|STORCLI_USE_SUDO)=' /etc/megaraid-dashboard/env
sudo -u raid-monitor sudo -n /usr/local/sbin/storcli64 /c0/eall/sall show all J >/tmp/pd.json
python3 -m json.tool /tmp/pd.json >/dev/null && echo "storcli pd json ok"
rm -f /tmp/pd.json
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select id,captured_at from controller_snapshots order by id desc limit 5;"
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select enclosure_id,slot_id,state,serial_number from pd_snapshots order by id desc limit 20;"
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 200 --no-pager | grep -i 'collector\|storcli'
```

### Fixes

- Normal cycle delay: wait one `METRICS_INTERVAL_SECONDS` interval and refresh the page.
- Stale snapshots after downtime: restart the service and watch for a successful
  `collector_run_completed` log entry.
- `storcli` connectivity: restore the binary path, sudoers allowlist, or controller access.
  The dashboard must be able to run `/c0/eall/sall show all J` as `raid-monitor`.

## Locate LED not blinking

### Symptom

The drive detail page accepts the locate request but the bay LED does not blink, or the UI
shows a JSON error for the locate start or stop request.

### Probable Causes

1. `/etc/sudoers.d/megaraid-dashboard` does not allow the exact locate command.
2. The storcli wrapper rejects the command because it is outside the JSON whitelist.
3. Controller firmware, enclosure backplane, or drive bay does not support locate LEDs.

### Diagnostics

```bash
sudo -l -U raid-monitor
sudo sed -n '1,200p' /etc/sudoers.d/megaraid-dashboard
sudo -u raid-monitor sudo -n /usr/local/sbin/storcli64 /c0/e252/s0 start locate J
sudo -u raid-monitor sudo -n /usr/local/sbin/storcli64 /c0/e252/s0 stop locate J
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 200 --no-pager | grep -i 'locate\|sudo\|storcli'
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select created_at,category,summary from events where category='operator_action' order by id desc limit 10;"
```

Replace `e252/s0` with the enclosure and slot shown on the drive detail page.

### Fixes

- Missing sudoers entry: rerun `sudo bash scripts/install.sh` or restore the sudoers
  fragment so `raid-monitor` can run the exact locate start and stop JSON commands.
- Wrapper rejection: use the UI route for `/c0/eN/sN start locate J` or
  `/c0/eN/sN stop locate J`; do not add broad shell access.
- Firmware or enclosure limitation: confirm with the controller vendor tools and chassis
  documentation. If the manual command succeeds but the LED stays dark, treat it as a
  hardware capability issue and use physical slot labels instead.

## Replace flow rejects with 409

### Symptom

The replace wizard returns HTTP 409, or the output panel shows an error such as
`must complete replace step missing before insert`, `serial mismatch`, or
`live serial mismatch`.

### Probable Causes

1. The requested step does not meet the state-machine prerequisite.
2. The typed serial number does not match the affected drive or replacement drive.
3. A prior step was skipped, failed, or was run before the collector snapshot caught up.

### Diagnostics

```bash
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 200 --no-pager | grep -i 'replace\|offline\|missing\|insert'
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select created_at,category,summary from events where category='operator_action' order by id desc limit 20;"
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select enclosure_id,slot_id,state,serial_number from pd_snapshots order by id desc limit 30;"
sudo -u raid-monitor sudo -n /usr/local/sbin/storcli64 /c0/e252/s0 show all J >/tmp/slot.json
python3 -m json.tool /tmp/slot.json >/dev/null && grep -i 'SN\|State' /tmp/slot.json
rm -f /tmp/slot.json
```

Replace `e252/s0` with the slot being replaced.

### Fixes

- Prerequisite failure: return to the wizard and run steps in order: offline, missing,
  physical swap, insert replacement, then rebuild polling.
- Serial mismatch: copy the serial number from the current drive detail page for Step 1,
  and from the newly visible replacement drive for Step 3.
- Snapshot delay: wait one collector interval after the physical swap, refresh the drive
  detail page, then retry the insert step.

## Migration fails on upgrade

### Symptom

An upgrade or restart fails in `scripts/preflight.sh` or `alembic upgrade head`. The journal
or installer output mentions a database lock, missing Alembic revision, failed migration,
or SQL object that does not match expected schema.

### Probable Causes

1. Another process has the SQLite database locked.
2. The installed source tree is missing the Alembic revision referenced by the database.
3. Manual SQL drift changed tables or columns outside migrations.

### Diagnostics

```bash
sudo systemctl stop megaraid-dashboard.service
sudo lsof /var/lib/megaraid-dashboard/megaraid.db
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db 'select version_num from alembic_version;'
ls -1 migrations/versions
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db '.schema events'
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db 'pragma integrity_check;'
sudo systemd-run --wait --pipe --collect --uid=raid-monitor --property=WorkingDirectory=/opt/megaraid-dashboard/src --property=EnvironmentFile=/etc/megaraid-dashboard/env /opt/megaraid-dashboard/src/scripts/preflight.sh
```

### Fixes

- Database lock: stop the dashboard and any shell sessions using SQLite, then rerun
  `scripts/preflight.sh`.
- Missing revision: deploy the complete source tree that contains the revision listed in
  `alembic_version`; do not edit the version table by hand.
- Manual SQL drift: restore the latest verified SQLite backup, then apply the upgrade from
  a clean schema. Use [docs/RUNBOOK.md](docs/RUNBOOK.md#restore-the-sqlite-database) for
  the restore flow.

## Browser shows 401 forever

### Symptom

The browser repeatedly prompts for HTTP Basic auth, or every proxied `/raid/` request
returns 401 even after entering the expected username and password.

### Probable Causes

1. The browser cached old Basic Auth credentials.
2. `ADMIN_PASSWORD_HASH` was regenerated but the operator is using the old password.
3. nginx or the browser is sending credentials for the wrong realm, host, or path.

### Diagnostics

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
curl -i "http://127.0.0.1:${APP_PORT:-8090}/"
sudo grep -E '^(ADMIN_USERNAME|ADMIN_PASSWORD_HASH)=' /etc/megaraid-dashboard/env
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 100 --no-pager | grep -i 'auth\|401\|rate'
sudo nginx -T | grep -n 'auth_basic\|proxy_pass\|X-Forwarded-Prefix'
```

### Fixes

- Cached browser credentials: open a private window, clear saved credentials for the host,
  or test with `curl -u 'admin:password' https://server.alexbomber.com/raid/`.
- Password mismatch: generate a new bcrypt hash, update `ADMIN_PASSWORD_HASH`, restart the
  service, and use the new password:

```bash
sudo /opt/megaraid-dashboard/.venv/bin/python -c 'import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass("New admin password: ").encode(), bcrypt.gensalt()).decode())'
sudoedit /etc/megaraid-dashboard/env
sudo systemctl restart megaraid-dashboard.service
```

- Wrong proxy auth layer: verify nginx forwards to `http://127.0.0.1:8090/` under
  `/raid/` and does not inject stale `Authorization` headers.

## Grafana panel shows no data

### Symptom

A Grafana dashboard loads, but one or more MegaRAID panels show `No data`, flat gaps, or an
empty label selector even though the dashboard UI is healthy.

### Probable Causes

1. The exporter port is not reachable from Prometheus because of firewall or bind address.
2. Prometheus is failing to scrape the MegaRAID target.
3. Grafana queries use labels that do not match the exported metrics.

### Diagnostics

```bash
sudo ss -lntp | grep ':8091'
curl -fsS http://127.0.0.1:8091/metrics | head -n 20
sudo ufw status numbered
curl -fsS http://prometheus.local:9090/api/v1/targets | python3 -m json.tool | grep -A20 -B5 megaraid
curl -G http://prometheus.local:9090/api/v1/query --data-urlencode 'query=up' | python3 -m json.tool
curl -G http://prometheus.local:9090/api/v1/labels --data-urlencode 'match[]=up{job="megaraid-dashboard"}' | python3 -m json.tool
```

### Fixes

- Exporter unreachable: start or restart the exporter service, bind it to the intended LAN
  interface, and open only the Prometheus source address to the exporter port.
- Scrape failure: correct the Prometheus target host, port, scheme, and path, then reload
  Prometheus and confirm the target is `up`.
- Label mismatch: update the Grafana panel query to use labels present in Prometheus.
  Prefer checking `/api/v1/labels` and `/api/v1/series` before editing dashboards.

## High RoC temperature without alerts

### Symptom

The Overview page shows a high RoC temperature, or `storcli` reports a high controller
temperature, but no warning or critical alert email arrives.

### Probable Causes

1. `ROC_TEMP_WARNING_CELSIUS` or `ROC_TEMP_CRITICAL_CELSIUS` is configured too high.
2. Hysteresis is blocking a repeat notification until the temperature clears and crosses
   the threshold again.
3. Maintenance mode or notifier delivery is paused.

### Diagnostics

```bash
sudo grep -E '^(ROC_TEMP_WARNING_CELSIUS|ROC_TEMP_CRITICAL_CELSIUS|ROC_TEMP_HYSTERESIS_CELSIUS|ALERT_SEVERITY_THRESHOLD)=' /etc/megaraid-dashboard/env
sudo -u raid-monitor sudo -n /usr/local/sbin/storcli64 /c0 show all J >/tmp/controller.json
python3 -m json.tool /tmp/controller.json >/dev/null && grep -i 'temperature\|roc' /tmp/controller.json
rm -f /tmp/controller.json
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select created_at,severity,category,summary,notified_at from events where summary like '%RoC%' or summary like '%temperature%' order by id desc limit 20;"
sudo -u raid-monitor sqlite3 /var/lib/megaraid-dashboard/megaraid.db "select key,value from system_state where key='maintenance_mode';"
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 200 --no-pager | grep -i 'roc\|temperature\|notifier\|maintenance'
```

### Fixes

- Threshold too high: set realistic warning and critical values, such as the post-heatsink
  targets documented in `README.md`, then restart the service.
- Hysteresis blocking repeats: cool the controller below
  `ROC_TEMP_WARNING_CELSIUS - ROC_TEMP_HYSTERESIS_CELSIUS`, then watch for a new crossing
  event if the temperature rises again.
- Notifier paused: stop maintenance mode, verify SMTP with the alert test command, and
  confirm `ALERT_SEVERITY_THRESHOLD` allows the event severity.
