# MegaRAID Dashboard Runbook

This runbook is the operator reference for routine checks, common incidents, and recovery
tasks. Generic command examples below use fresh-install defaults (`raid-monitor`,
`/opt/megaraid-dashboard`, optional `/raid/` prefix). Existing SERVER instead uses
`megaraid`, `/opt/megaraid-dashboard-ui-27af3da` and the root HTTPS origin.
Use [CURRENT_STATE.md](CURRENT_STATE.md) for the observed release, protected rollout
configuration, test evidence and remaining work. Resolve the effective account and
release before adapting generic commands; do not print secret environment values.

## Current production and safe verification

Release `27af3da` (#141) is deployed. Compact Actions, drive DG/Span/Row, shared Collector
ages, observed Patrol scheduling and CacheVault learn-date recovery are active.
The Telegram transport test reached the owner; no real hardware failure was induced.

After an SSH disconnect, check service WorkingDirectory/health and the matching
rollout status before taking action. The #141 one-shot rollout finished `verified`;
future updates use [versioned deployment](VERSIONED_DEPLOYMENT.md), whose durable receipt
survives reboot and whose repeated verified request returns already_installed.
See [the exact recovery checks](CURRENT_STATE.md#recovery-after-ssh-disconnect-or-repeated-activation).
Never bypass the guard or restore the database solely because the terminal disconnected.

For Learn Cycle, INFO `cachevault_next_learn_time_recovered` means the documented
numeric timestamp repaired invalid legacy calendar text. It is not a new hardware
fault. WARNING `cachevault_next_learn_time_invalid` still means unsupported/unrecoverable
data; preserve redacted evidence and investigate rather than starting Learn Cycle.

See [maintenance handoff](MAINTENANCE_HANDOFF.md) for triggers and scope.
Independent outage detection and a recurring backup restore drill remain unverified.
The routines below describe how to verify them, not proof that they are installed.


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
3. Start an approved maintenance window before Locate Start or other maintenance-gated writes.
   Expected output: alerts are suppressed only for the intentional, bounded maintenance work.
4. Start locate LED for the affected slot if needed and permitted by the selected capability.
   Expected output: explicit controller acceptance and a saved audit record; physically verify
   the LED because command acceptance does not prove LED behavior.
5. Use the drive replacement UI for the offline and missing steps.
   Expected output: each step requires the affected drive serial number and records an audit entry.
6. Replace the physical disk only after the UI confirms the controller accepted the missing step.
   Expected output: the new drive is visible in the same enclosure and slot.
7. Use the replacement insert step and follow rebuild progress.
   Expected output: rebuild percentage advances or completes.
8. Stop maintenance mode after rebuild is complete and the virtual drive is optimal.
   Expected output: dashboard returns to normal and new alerts are no longer suppressed.

Locate Stop is the sole maintenance exception. With read-only disabled and locate
capability selected, use the authenticated, CSRF-protected Stop action after an inactive
or expired window without reopening maintenance. Exact sudoers, explicit controller
Success and audit persistence still apply; read-only or capability `none` denies both
Locate actions. Follow [Stop recovery](STORCLI_LOCATE_ONLY_ROLLOUT.md#stop-recovery-after-an-inactive-or-expired-window)
for an uncertain Start; never open a window merely to recover Stop or repeat Start blindly.
The locate-only rollout does not enable the replacement steps in this general incident workflow.

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

1. Inspect the repeated events and the affected notification channel before changing modes.
   Expected output: current incidents are distinguished from delivery retries and audit noise.
   Maintenance is not a Telegram mute: real hardware incidents remain eligible.
2. Group events by category and subject.
   Expected output: the flood has a dominant source, such as a failed drive or collection error.
3. Fix the underlying cause first.
   Expected output: new events slow down or stop.
4. Check alert suppression and per-category behavior only after the cause is understood.
   Expected output: suppression is used to reduce repeats, not to hide an unresolved incident.
5. Close any actual maintenance window when the authorized work is complete.
   Expected output: controller-write authorization ends; incident delivery keeps its normal checks.

### Read a concise Telegram alert

The hostname selects the server; the final `26.10 10:57 · №42` line identifies its
original event and Europe/Rome occurrence time. Look up event `42` on that host's Events
page (or `events` table), then compare `occurred_at` in UTC with Europe/Rome, accounting
for summer/winter time. Year, seconds, machine identifiers, structured metadata and raw
summary belong to that record. The message is plain text without buttons or runbook links;
forward it to ChatGPT. A test says only `тест уведомлений` / `Действий не требуется.`
and has no event ID. A successful manual test proves that send only.

The [catalog and detection audit](TELEGRAM_ALERTS.md) records the completeness evidence:
confirmed PD/VD disappearance creates a critical incident even if both lists become empty.
Incomplete inventory and uncertain CacheVault probes retain the last reliable snapshot,
record collection failure and use the confirmed-outage policy. Do not treat silence or
monitoring uncertainty as healthy hardware.

### Missed alerts; what now?

1. Confirm the event exists on the Events page.
   Expected output: the event severity is at or above `ALERT_SEVERITY_THRESHOLD`.
2. Identify the channel and its delivery policy. Maintenance can explain SMTP suppression;
   it does not explain a permanent skip of a real hardware Telegram incident.
   Expected output: channel-specific state, rather than an assumed global mute, explains the result.
3. Check whether a recent matching event was already notified.
   Expected output: deduplication explains only repeated events inside the suppress window.
4. Check SMTP configuration and network reachability to the SMTP host.
   Expected output: credentials, host, port, and STARTTLS settings match the provider.
5. Check application logs for notifier failures.
   Expected output: SMTP or throttling errors are visible if delivery failed.
6. For Telegram, check the `event_deliveries` row for the event (`channel='telegram'`).
   Expected output: `status` and `detail` explain the outcome (`sent`, `retry` with the
   last error or `awaiting_*`/`channel_deferred`, `skipped` with
   `duplicate`/`maintenance`/`not_confirmed`/`pre_migration`/`obsolete`). `sending` means
   a committed claim with an uncertain outcome; it will be revalidated and retried.
   Legacy `failed` rows are also retried after revalidation. Info events and non-thermal
   warnings are never sent. Critical `system` messages require the explicit collector
   outage marker, not merely critical severity.
7. Check `system_state` for key `telegram_not_before`: its ISO UTC value is the durable
   channel cooldown after HTTP 429. All events wait; waiting does not consume attempts.
   Provider hints are capped at 3600 seconds before computing either deadline; invalid
   hints use 60 seconds. `telegram_retry_after_clamped` identifies an oversized hint,
   without logging its untrusted value. `telegram_cooldown_clamped` identifies repair
   of a previously persisted channel deadline beyond an hour. The next successful
   notifier cycle durably shortens such channel/attempted Telegram retry deadlines to
   one hour from that cycle; later cycles/restarts do not slide it. No manual SQL edit
   is needed. Longer provider requests may be retried early, with new 429s renewing the
   bounded cooldown. Do not clear it or run `telegram-test` to bypass a cooldown.
8. For `awaiting_fresh_data`, compare the latest `controller_snapshots.captured_at` with
   `max(60 seconds, 2 × METRICS_INTERVAL_SECONDS)`. `awaiting_temperature` means the
   sensor reading is missing; `awaiting_drive_identity` means the original/current
   serial cannot be verified. Repair collection/configuration, rather than marking a
   queued alert sent. `detail` holds the latest reason; logs retain transport failures.
   Active incidents retry with a capped one-hour delay, without an age cutoff or terminal
   attempt budget. `telegram_delivery_prolonged_failure` logs failures from attempt 8.
   Notifier health remains unhealthy while attempted deliveries await retry.
9. A process crash/timeout after acceptance can produce a duplicate with the same event ID;
   a crash before HTTP is retried. Recovered/replaced/removed incidents are skipped after
   fresh-state validation. Keep the events and delivery tables in database backups.
   Disabling Telegram preserves this queue; re-enabling can send an old still-active risk.
10. Telegram responses have a 10 s absolute request deadline and 64 KiB byte cap;
    compression is rejected before decoding. `response too large`,
    `unexpected response encoding` and `timeout` remain durable retries. A 429 with
    an unreadable body sets a 60 s fallback channel cooldown. Check connectivity/TLS
    and the trusted Telegram API path; do not enlarge limits without diagnosis.
    Collection, disk checks and retention continue during Telegram network waits.
11. Repeated controller warnings within the active hysteresis band (e.g. 94/96 C)
    preserve the first warning's confirmation deadline. Only clear/escalation resets it.
    Later candidates are revalidated after each unlocked HTTP phase, including current
    maintenance and snapshot freshness. A message already in flight cannot be recalled.
12. For recurring SMART / CacheVault replacement-required alerts, inspect the event's
    `before_json` / `after_json`: a false-to-true flag transition proves a new episode,
    including after snapshot pruning. An older queued episode is obsolete; a repeat
    baseline without a recorded clear still deduplicates. This adds no recovery events
    and does not alter SMTP selection or delivery.

13. For recurring controller overheating, inspect `system_state` keys
    `telegram_roc_warning_cleared_at` and `telegram_roc_critical_cleared_at` (ISO UTC).
    Each records the latest successful reading at/below its threshold minus hysteresis,
    even if no recovery event was emitted (e.g. 96 → 94 → 89 → 96 C). These two
    monotonic timestamps survive history pruning, update even with Telegram disabled,
    and roll back with a failed collection transaction. Missing sensors do not clear
    incidents. Retained readings bootstrap missing/invalid metadata; already-pruned
    pre-tracking history cannot be reconstructed. Keep these keys with database backups.

### Collection failure; what now?

1. Check the Events page for a system collection-failure event.
   Expected output: the summary shows whether `storcli` failed, timed out, or could not run.
   A single failure is a `warning` and is not emailed or sent to Telegram; a critical
   `Monitoring stale` event means collection has failed persistently and the RAID state is
   unknown, not that a drive failed.
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
   If the prior version predates migration `0009_event_deliveries`, first run
   `alembic downgrade 0008_cleanup_false_controller_alarm_events` with the *current* code
   as the service user. It only drops `event_deliveries`; events and SMTP `notified_at` are
   kept. Without it the older code fails with "Can't locate revision". These delivery
   fixes add no further migration. Rolling back only these fixes to an earlier PR #122
   revision is unsafe: it restores the three-hour cutoff and interrupted-send loss.
   Prefer `TELEGRAM_ENABLED=false` with this code, preserving the queue and SMTP. The
   `telegram_not_before` system-state row is harmless to older code but is only enforced
   by the fixed notifier; preserve it when temporarily disabling Telegram. The two
   `telegram_roc_*_cleared_at` rows are also harmless to older code; keep them. Older
   revisions ignore this recovery evidence and can suppress recurring overheating.
   Versions before the retry-hint cap can again persist oversized cooldowns or overflow;
   disabling Telegram with the fixed code is safer than reverting this security fix.
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

1. Confirm the service runs as the approved unprivileged account (`raid-monitor` on
   fresh installs, `megaraid` on existing SERVER).
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

## Existing megaraid production service

For the installed `27af3da` release, pinned StorCLI 1.23.02 and full-capability
policy, follow [the current checkpoint](CURRENT_STATE.md) and the
[full rollout contract](STORCLI_FULL_ROLLOUT.md). Production runs as `megaraid`;
installer `raid-monitor` defaults must not replace its service configuration.

The [read-only](STORCLI_READ_ONLY_ROLLOUT.md) and
[locate-only](STORCLI_LOCATE_ONLY_ROLLOUT.md) procedures are historical preparation
records, not the active selection or instructions to redeploy. Current capability
does not certify physical actions or authorize a production test window.
