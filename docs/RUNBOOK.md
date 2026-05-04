# MegaRAID Dashboard Runbook

This runbook is the operator reference for routine checks, common incidents, and recovery
tasks. It assumes the production service runs behind nginx at `/raid/`, uses SQLite for local
state, and runs as the `raid-monitor` service user.

## Routines

### Daily dashboard check

1. Open the dashboard overview.
   Expected output: controller, virtual drive, CacheVault, and drive tiles are visible.
2. Check the global health state.
   Expected output: no critical tiles unless an incident is already being handled.
3. Open the Events page.
   Expected output: no new warning or critical events since the last check.
4. If maintenance is active, confirm the banner reason and expiry.
   Expected output: the maintenance window is intentional and has a bounded end time.

### Daily event review

1. Filter Events by `warning` and `critical`.
   Expected output: only known or already-actioned events are present.
2. Check for repeated event subjects.
   Expected output: no repeated temperature, drive, SMART, or collection-failure events.
3. For operator actions, open the audit view or filter Events by operator action.
   Expected output: recent write actions match the known maintenance work.
4. Record any unexplained event in the local incident notes.
   Expected output: each unexplained event has an owner and next action.

### Weekly trend skim

1. Open Grafana, when the Prometheus/Grafana stack is deployed.
   Expected output: MegaRAID dashboards load without scrape gaps.
2. Skim drive temperature trends.
   Expected output: no drive has a rising baseline or frequent warning crossings.
3. Skim error counter trends.
   Expected output: media, other, and predictive-failure counters are flat.
4. Review the last seven days of Events.
   Expected output: no recurring alert pattern is being normalized.

### Monthly SQLite backup verification

1. Confirm the latest SQLite backup artifact exists.
   Expected output: backup timestamp is recent enough for the local backup policy.
2. Restore the backup to a temporary path, never over the live database.
   Expected output: SQLite opens the restored copy without errors.
3. Run a read-only row count on key tables.
   Expected output: `controller_snapshots`, `pd_snapshots`, and `events` contain expected data.
4. Delete the temporary restored copy.
   Expected output: only the approved backup artifact and live database remain.

### Monthly storage and log check

1. Check the data filesystem free space.
   Expected output: free space is above the configured warning threshold.
2. Check journald usage for the application namespace.
   Expected output: logs are within the configured retention cap.
3. Confirm retention jobs have recent successful log entries.
   Expected output: hourly and daily aggregation runs complete without error.

### Quarterly security review reminder

1. Review nginx basic-auth configuration.
   Expected output: only intended operators have access.
2. Review `/etc/sudoers.d/megaraid-dashboard`.
   Expected output: only exact approved `storcli64` commands are whitelisted.
3. Review `/etc/megaraid-dashboard/env` permissions.
   Expected output: secrets are not world-readable.
4. Review the operator-action audit log.
   Expected output: all write or destructive actions have an expected actor and reason.

## Incident Response

### Drive failed; what now?

1. Open the failed drive detail page from the Drives page.
   Expected output: enclosure, slot, serial number, state, and recent errors are visible.
2. Confirm the physical slot against the chassis labeling before touching hardware.
   Expected output: the dashboard slot matches the drive to replace.
3. Start locate LED for the affected slot if needed.
   Expected output: the expected bay LED turns on and the audit log records the action.
4. Start maintenance mode before write actions.
   Expected output: notifier sends no new emails while maintenance is active.
5. Use the drive replacement UI for the offline and missing steps.
   Expected output: each step requires the affected drive serial number and records an audit entry.
6. Replace the physical disk only after the UI confirms the controller accepted the missing step.
   Expected output: the new drive is visible in the same enclosure and slot.
7. Use the replacement insert step and follow rebuild progress.
   Expected output: rebuild percentage advances or completes.
8. Stop maintenance mode after rebuild is complete and the virtual drive is optimal.
   Expected output: dashboard returns to normal and new alerts are no longer suppressed.

### Drive temperature warning or critical; what now?

1. Open the drive detail page and review the temperature graph.
   Expected output: the current temperature and recent trend are visible.
2. Check nearby drives for the same pattern.
   Expected output: one hot drive points to a drive issue; many hot drives point to airflow.
3. Inspect chassis airflow, fan speed, dust, intake blockage, and ambient temperature.
   Expected output: an environmental cause is either found or ruled out.
4. If temperature is still critical, reduce load or power down intentionally.
   Expected output: the controller is not left running in sustained critical temperature.
5. Keep the event open until hysteresis clears the dashboard state.
   Expected output: a clear event appears after temperature drops below the clear threshold.

### Controller RoC thermal alert; what now?

1. Open the Overview page and confirm the controller temperature tile.
   Expected output: RoC temperature severity matches the latest snapshot.
2. Check chassis airflow across the controller heatsink.
   Expected output: directed airflow is present and not obstructed.
3. Review recent fan or case changes.
   Expected output: a likely airflow regression is identified or ruled out.
4. If the alert persists, schedule physical inspection of the heatsink and fan path.
   Expected output: the controller is not left in a recurring critical state.
5. Leave suppression settings unchanged unless the alert is known and bounded.
   Expected output: real RoC overheating does not become invisible.

### Alert flood; what now?

1. Start maintenance mode with a clear reason and bounded duration.
   Expected output: notifier skips alert delivery while the dashboard still records events.
2. Group events by category and subject.
   Expected output: the flood has a dominant source, such as a failed drive or collection error.
3. Fix the underlying cause first.
   Expected output: new events slow down or stop.
4. Check alert suppression and per-category behavior only after the cause is understood.
   Expected output: suppression is used to reduce repeats, not to hide an unresolved incident.
5. Stop maintenance mode when the event stream is stable.
   Expected output: normal alerting resumes.

### Missed alerts; what now?

1. Confirm the event exists on the Events page.
   Expected output: the event severity is at or above `ALERT_SEVERITY_THRESHOLD`.
2. Confirm maintenance mode was not active at the event time.
   Expected output: no active maintenance window explains the skip.
3. Check whether a recent matching event was already notified.
   Expected output: deduplication explains only repeated events inside the suppress window.
4. Check SMTP configuration and network reachability to the SMTP host.
   Expected output: credentials, host, port, and STARTTLS settings match the provider.
5. Check application logs for notifier failures.
   Expected output: SMTP or throttling errors are visible if delivery failed.

### Collection failure; what now?

1. Check the Events page for a system collection-failure event.
   Expected output: the summary shows whether `storcli` failed, timed out, or could not run.
2. Run the service preflight script.
   Expected output: database writability and migration checks pass.
3. Verify `storcli64` exists at the configured path.
   Expected output: the binary path matches `STORCLI_PATH`.
4. Verify the service user can use the sudoers whitelist.
   Expected output: allowed JSON commands run and unrelated commands are denied.
5. Wait for the next collector interval.
   Expected output: a collection-recovered event appears after successful collection.

### Disk space alert; what now?

1. Check free space on the data filesystem.
   Expected output: free space confirms the alert severity.
2. Check database size and journald usage.
   Expected output: growth source is identified.
3. Confirm retention jobs are running.
   Expected output: raw snapshots are pruned after the configured retention window.
4. Free space using normal host operations.
   Expected output: the dashboard records a clear event after the next disk-space check.

## Recovery

### Service will not start; what now?

1. Check systemd status for `megaraid-dashboard`.
   Expected output: the failing phase is visible, such as preflight, env loading, or uvicorn.
2. Run `scripts/preflight.sh` with the same environment file.
   Expected output: Alembic and SQLite checks either pass or give a direct failure.
3. Check `/etc/megaraid-dashboard/env`.
   Expected output: required settings exist and secrets are not malformed.
4. Check journald for the service namespace.
   Expected output: the Python exception or startup error is visible.
5. Restart the service after fixing the cause.
   Expected output: `/healthz` returns `ok`.

### Restart the service

1. Confirm no drive write operation is in progress.
   Expected output: no active offline, missing, insert, or rebuild polling action is being changed.
2. Run `systemctl restart megaraid-dashboard`.
   Expected output: systemd reports the unit as active.
3. Open `/healthz`.
   Expected output: status is `ok` or a clear degraded reason is returned.
4. Open the dashboard.
   Expected output: the latest snapshot and Events page render.

### Restore the SQLite database

1. Stop the service.
   Expected output: no process is writing to the live database.
2. Copy the live database aside before replacing it.
   Expected output: a rollback copy exists outside the restore target.
3. Put the verified backup at the configured database path.
   Expected output: ownership and permissions match the service user.
4. Run `scripts/preflight.sh`.
   Expected output: migrations and SQLite writability pass.
5. Start the service.
   Expected output: dashboard renders restored history and new snapshots append normally.

### Roll back to a prior version

1. Stop the service.
   Expected output: the application is not serving or collecting during rollback.
2. Back up the current SQLite database and environment file.
   Expected output: both state files can be restored if rollback fails.
3. Restore the prior source tree under `/opt/megaraid-dashboard/src`.
   Expected output: the application code matches the intended prior revision.
4. Reinstall the package in the service virtual environment if needed.
   Expected output: imports resolve to the restored source tree.
5. Run `scripts/preflight.sh`.
   Expected output: database migrations are compatible with the restored version.
6. Start the service and check `/healthz`.
   Expected output: service is healthy before operators resume normal use.

## Backups

### Manual backup items

1. Back up the SQLite database from the configured `DATABASE_URL`.
   Expected output: a restorable copy of controller history, events, audit logs, and settings state.
2. Back up `/etc/megaraid-dashboard/env`.
   Expected output: SMTP, auth, storcli, retention, and threshold settings are recoverable.
3. Back up `/etc/sudoers.d/megaraid-dashboard`.
   Expected output: the narrow storcli command whitelist can be restored exactly.
4. Back up `deploy/megaraid-dashboard.service` customizations if locally modified.
   Expected output: systemd hardening and environment wiring can be reconstructed.
5. Back up nginx site configuration.
   Expected output: path-prefix routing and HTTP basic auth can be restored.

### Backup verification

1. Restore the SQLite backup to a temporary filename.
   Expected output: the live database is not touched.
2. Run `sqlite3 restored.db "pragma integrity_check;"`.
   Expected output: `ok`.
3. Run read-only counts against key tables.
   Expected output: counts are plausible for snapshot, event, and audit history.
4. Remove the temporary restored database.
   Expected output: no stale test restore remains on the production host.

## Upgrade Procedure

### Upgrade the application

1. Read the release notes or PR description before deploying.
   Expected output: schema, security, and storcli-wrapper risks are known.
2. Back up SQLite and `/etc/megaraid-dashboard/env`.
   Expected output: rollback has usable state.
3. Put the new source tree under the install prefix using the installer or local release process.
   Expected output: `/opt/megaraid-dashboard/src` contains the intended revision.
4. Install dependencies into `/opt/megaraid-dashboard/.venv`.
   Expected output: package metadata and imports match the new revision.
5. Run `scripts/preflight.sh`.
   Expected output: Alembic upgrades complete and SQLite is writable.
6. Restart the service.
   Expected output: `/healthz` is healthy and the dashboard renders.
7. Watch the next collector cycle.
   Expected output: a fresh snapshot appears and no collection-failure event is recorded.

### Upgrade nginx or proxy configuration

1. Validate nginx configuration before reload.
   Expected output: syntax check passes.
2. Confirm `X-Forwarded-Prefix` is set by nginx, not trusted from clients.
   Expected output: `/raid/` links render with the correct prefix.
3. Reload nginx.
   Expected output: active sessions continue and new requests route to FastAPI.
4. Open the dashboard through the public path.
   Expected output: static assets, HTMX partials, and forms work behind `/raid/`.

## Quarterly Review Checklist

1. Confirm the service still runs as `raid-monitor`.
   Expected output: systemd `User` and `Group` are unchanged.
2. Confirm the app starts read-only unless maintenance mode is explicitly enabled.
   Expected output: write controls are not usable during normal operation.
3. Confirm destructive operations still require serial-number confirmation.
   Expected output: offline, missing, and replacement operations cannot be run accidentally.
4. Confirm all storcli calls still go through the wrapper.
   Expected output: no direct subprocess calls to `storcli64` exist outside the wrapper path.
5. Confirm Basic Auth and rate limiting are still enabled at the web layer.
   Expected output: unauthenticated access is blocked and repeated failures are throttled.
6. Confirm alert delivery still works with a controlled test event or staging check.
   Expected output: SMTP delivery succeeds and the event receives `notified_at`.
7. Confirm backups restore cleanly.
   Expected output: the latest backup passes integrity check and row-count sanity checks.
8. Confirm docs still match deployment reality.
   Expected output: proxy, install, runbook, and architecture notes do not contradict production.
