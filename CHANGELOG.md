# Changelog

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
