# Changelog

## [Unreleased] - UI consistency fix pack

### Added

- Page-level live refresh (`live-refresh.js`): Overview, Controller, Drives, Drive detail and
  the Events/Audit status bar re-fetch every 30 seconds and swap `[data-live-region]` blocks.
  Refresh pauses while typing, while a dialog or replace-wizard step is open, and while the tab
  is hidden. The header shows `Live · HH:MM:SS`, `Paused` or `Update failed`, plus a manual
  refresh button.
- Accessible confirmation dialog (`confirm-dialog.js`) for drive advanced actions;
  destructive actions require typing the drive's `enclosure:slot`.
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
