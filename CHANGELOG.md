# Changelog

## [Unreleased] - Maintenance handoff and versioned deployment

- Fingerprint nested root-controlled rollout evidence directories during adoption and
  preservation checks; refuse nested drift/links without changing configuration.
- Add a hash-pinned offline release tool, detached systemd worker, durable receipts,
  explicit already-installed handling and guarded application rollback preserving live DB history.
- Reject migration/profile changes; retain protected settings/policy checks and normal-collection acceptance.
- Reconcile source/Obsidian documentation and event-driven maintenance handoff; no new automation.

## Production checkpoint — 2026-11-01

Installed application: `27af3da` (#141), including #139 readable times/control simplification,
#140 compact scheduling/observed state and #141 Reported schedule/live Observed header.
Fresh review and CI passed before activation. Production GET checks exercised manual and
automatic refresh using normal samples; settings/grants preserved and no controller writes.
Tooling above is separate from this already-installed application checkpoint.

## Delivered in #139 - Readable chart times and observed mode labels

- Show short browser-local dates/times on RoC, drive-temperature and error charts;
  full date/time and timezone remain in hover. Limit horizontal labels to eight
  and retain times for sparse history. Parse legacy UTC bucket labels explicitly as UTC.
- Remove known CC/Patrol mode prose repeated by selected buttons, preserving
  reported intervals and unknown/disabled modes. Selection remains based on saved state.
- Hide the CC Concurrent/Sequential switch for exactly one saved VD, retaining CC
  Start/Stop and Patrol modes. Remove unsupported Mark Bad from rendered drive rows;
  retain existing API/wrapper refusal and safety gates.
- Add an operator action reference. No controller, authorization or schema changes;
  this follow-up shipped after the historical `cfad744` checkpoint below.

## Production checkpoint — 2026-10-31

Historical deployment record, not a semver tag: SERVER ran `cfad744` at this checkpoint.
See [current state](docs/CURRENT_STATE.md) for evidence and outstanding operational work.

### Delivered

- #134 (`25c2b74`): independent operator-chain safety fixes and refusal paths.
- #135 (`16c06d9`): compact Actions layout with the existing safety gates.
- #136 (`8b166c6`): identity-bound observed DG/Span/Row, shared Collector footer ages,
  and Patrol Auto/Manual/Unknown scheduling display without invented intervals.
- #137 (`cfad744`): bounded UTC recovery of a malformed legacy CacheVault learn date
  from its documented numeric counter; no schema, firmware, schedule or policy change.
- Neutral Telegram test accepted on 2026-10-30 22:11:08 UTC and receipt confirmed.
  No real hardware fault was induced or controller write used for validation.

### Documentation

- Reconciled current deployment, remaining monitoring/backup/tooling work, historical
  audit boundaries and Obsidian handoff. Future releases require review and green CI.

The older “Unreleased” sections below retain development history and original
intermediate behavior. They are not a list of changes missing from SERVER; current
Telegram text is concise plain text, with no inline button or runbook link.

## [Unreleased] - Compact Actions panel

### Changed

- Put controller, drive and foreign-configuration writes in a shared compact Actions
  panel, with one maintenance gate and reasons next to the affected action.
- Keep Locate Stop recovery available outside maintenance; preserve exact-command policy,
  CSRF, serial confirmations, foreign digests and dry-run defaults.
- Refresh the maintenance gate together with action availability after window expiry.
- Stack action rows on narrow screens, leave unknown modes unselected, and show automatic
  rebuild as status without unsupported controls. A silenced buzzer remains enabled.
- Clarify that hardware warning/critical Telegram alerts remain active during maintenance.

## [Unreleased] - Telegram alerts

### Added

- Optional alert-only Telegram channel (`TELEGRAM_ENABLED`, default off) with Russian
  messages that explain the problem, risk and one action; the final contract has no links/buttons.
- `event_deliveries` table (migration `0009`) for independent per-channel delivery and
  retry state; existing events are backfilled as already handled for Telegram.
- `python -m megaraid_dashboard.alerts telegram-test` for a neutral manual delivery check.
- `megaraid_telegram_alerts_total{result}` metric.
- A single critical `Monitoring stale` event after persistent collection failure
  (`COLLECTOR_STALE_ALERT_MINUTES`, at least 3 failed polls).

### Fixed

- Cap untrusted Telegram retry hints at 3600 seconds before per-event/global deadline
  arithmetic, safely handling overflow-sized integers and invalid values. Durably repair
  older excessive channel/attempted-retry deadlines without extending them on restart.

- Recognize controller-temperature recoveries missed by adjacent-sample event detection;
  persist two bounded recovery timestamps atomically with collection, including while
  Telegram is disabled. New episodes remain eligible after raw-history pruning, without
  adding recovery events or changing SMTP.

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

- Keep Russian Telegram alerts within two content lines plus a Rome-time/event-ID footer
  and 160 characters for SERVER / AI-SERVER. Distinguish failed, missing and offline
  disks; use only structured facts and retain detailed diagnostics in the event store.
- The intermediate inline runbook button was removed by the concise-message contract;
  final messages have no links, buttons or text parsing.
- Make `telegram-test` neutral and independent of incidents; manual success does not
  verify automatic delivery. Document existing detector limits without changing policy.

- A single failed collection is now a `warning` event instead of `critical`, so it no
  longer emails at `ALERT_SEVERITY_THRESHOLD=critical`.

## [Unreleased] - UI consistency fix pack

### Added

- Page-level live refresh (`live-refresh.js`): Overview, Controller, Drives, Drive detail and
  the Events/Audit status bar re-fetch every 30 seconds and swap `[data-live-region]` blocks.
  Refresh pauses while typing, while a dialog or replace-wizard step is open, and while the tab
  is hidden. The header shows `Live · HH:MM:SS`, `Paused` or `Update failed`, plus a manual
  refresh button.
- Accessible confirmation dialog (`confirm-dialog.js`) for drive advanced actions;
  destructive actions require typing the affected drive's serial number.
- Four-step progress stepper for the replace-drive wizard.
- `bytes_iec` template filter (`4000787030016` -> `3.64 TiB`).
- System status bar on the Drives, Events/Audit and Foreign Configuration pages.

### Changed

- Single v2 stylesheet: legacy v1 selectors removed; `:focus-visible`, type scale, shared
  `kv-grid`, `alert`, `disclosure` and `segmented` components, and 1024/760/720/560 px
  breakpoints. No page scrolls horizontally at 375 px.
- Maintenance start form moved from Overview to the Controller page (`#maintenance`).
- Foreign configuration import/clear live only on `/controller/foreign-config`; the Controller
  page shows status and a link. The foreign-config page does not auto-refresh because every
  load queries storcli.
- Title Case headings and buttons across all pages.
- Drives table sorts through header buttons with `aria-sort`; sort survives live refresh.
- Static asset version hashes every CSS and JS file, so browsers reload changed scripts.

### Fixed

- Replace wizard no longer hangs the tab when a step is cancelled (stepper mutation loop).
- Elements with the `hidden` attribute stay hidden regardless of component `display` rules.
- RoC temperature chart height stays bounded.

### Removed

- Header UTC clock (replaced by the live-refresh status).
- Unused `partials/alert_status.html`.

## Dashboard redesign

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
