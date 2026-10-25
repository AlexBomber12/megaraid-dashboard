# Changelog

## [Unreleased] - Telegram alerts

### Added

- Optional alert-only Telegram channel (`TELEGRAM_ENABLED`, default off) with Russian
  messages that explain the problem, risk, and one action and link to the runbook.
- `event_deliveries` table (migration `0009`) for independent per-channel delivery and
  retry state; existing events are backfilled as already handled for Telegram.
- `python -m megaraid_dashboard.alerts telegram-test` for a synthetic dry-run message.
- `megaraid_telegram_alerts_total{result}` metric.
- A single critical `Monitoring stale` event after persistent collection failure
  (`COLLECTOR_STALE_ALERT_MINUTES`, at least 3 failed polls).

### Fixed

- Preserve recurring SMART and CacheVault replacement-required alerts by using their
  durable false-to-true transition metadata as an incident boundary; unchanged baseline
  observations still deduplicate, without adding SMTP recovery events.
- Preserve temperature-warning confirmation across repeated same-incident warnings.
- Bound Telegram response handling with a 10-second absolute deadline, 64 KiB byte cap
  and rejection of compressed bodies; keep 429 cooldown on unreadable responses.
- Release the scheduler write lock during Telegram network I/O so collection, retention
  and disk checks continue; revalidate later sends using current time/data/maintenance.
- Preserve physical-drive temperature warnings exactly at the hysteresis boundary;
  retain the controller detector's separate clearing rules for warnings and criticals.
- Filter Telegram eligibility before limiting the candidate batch, so accumulated
  nonthermal warnings cannot block critical alerts or due retries. Match the outage
  marker as a JSON boolean, excluding numeric/string lookalikes.
- Persist channel-wide Telegram HTTP 429 cooldown across cycles and restarts.
- Revalidate fresh data, original disk serial and current incident state before delivery;
  removed, replaced and recovered incidents never produce delayed warnings.
- Retain active critical alerts beyond three hours and eight failures, with bounded
  candidate batches, sends per cycle and capped retry backoff.
- Retry interrupted claims after validation; document possible duplicate delivery after
  ambiguous acceptance. Keep notifier health unhealthy while failed attempts await retry.
- Restrict monitoring-outage Telegram text to explicitly marked collector outage events.

### Changed

- A single failed collection is now a `warning` event instead of `critical`, so it no
  longer emails at `ALERT_SEVERITY_THRESHOLD=critical`.

## [Unreleased] - Dashboard redesign

### Added

- New main page layout with one controller tile, eight-drive grid, recent activity timeline,
  and system status bar.
- Controller detail page at `/controller` with health, live operations, CacheVault, RAID
  configuration, hardware identity, buzzer controls, and RoC temperature history.
- Drive detail page redesign with backplane position diagram, inline error sparkline,
  fixed-height temperature chart, locate controls, and replacement entry.
- Buzzer control workflow: `Silence`, `Disable`, and `Enable` the physical controller alarm
  from the web UI.
- Advanced drive actions: Mark UBad, Mark UGood, Spin Down, and Make Hot Spare.
- Auto-refresh every 30 seconds for the main dashboard content.

### Fixed

- Drive detail temperature and error charts stay bounded instead of growing vertically.
- Activity feed shows the latest operator and system events in the redesigned main page.
- Controller alarm display distinguishes persistent buzzer configuration from an active
  physical alarm.

### Removed

- Legacy strip-of-tiles overview layout.
- Legacy overview partial path in favor of `/partials/main-page`.
