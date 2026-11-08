# MegaRAID Dashboard — maintenance roadmap

Updated: **2026-11-08**. Installed application: `211dfb2` (#153) on SERVER.
[CURRENT_STATE.md](CURRENT_STATE.md) records the production verification boundary.
This plan grants no controller mutation, notification, deployment or recurring automation.

## Delivered

- Typed JSON StorCLI boundary, SQLite/Alembic, collection and event history.
- Independent SMTP/Telegram delivery state, freshness/identity checks, confirmation,
  deduplication and retries. One neutral real Telegram delivery is confirmed.
- Authentication/CSRF, maintenance/destructive gates, root-reviewed exact commands,
  explicit replacement topology and durable audit.
- Compact Actions, readable chart times, single-VD simplification, Mark Bad removal,
  consistent saved operation states and retained Patrol scheduling (#139–140).
- Reported schedule label and automatic/manual Observed refresh (#141), verified
  with normal production samples and no hardware writes.
- Numeric CacheVault Learn Cycle recovery (#137); no clock/firmware/schedule change.
- Versioned application-only rollout with detached execution, durable status,
  repeat-request handling and guarded rollback; see [the procedure](VERSIONED_DEPLOYMENT.md).
  Managed production update #146 reached verified on 2026-11-06; actual rollback
  remains untested on SERVER. Application rollback preserves live DB history.
- One-row physical S0-S7 labels and Drives sort/refresh/navigation verified after
  installation; owner confirmed all eight physical Locate LEDs. Buzzer setting
  Disable -> On -> Disable acceptance passed with normal samples and audit on
  2026-11-06; initial Disabled restored after the test. The later owner-selected
  Enabled setting was confirmed by the 18:13 UTC normal sample with maintenance
  closed and no additional follow-up write. Acoustic/Silence unverified.
- Reconciled source/Obsidian documentation and [maintenance handoff](MAINTENANCE_HANDOFF.md).

- Shared drive-health assessment (#148) and critical-alert/component/metric/freshness
  corrections (#149) are installed. #149 passed 3404 default tests (one existing skip),
  100% statement/branch coverage, 515 safety and 291 Chromium cases. Managed acceptance
  passed at 2026-11-07 16:55:45 UTC with normal collection and no controller writes.

- Shared Healthy / Attention / Critical / Unknown labels and retained count/error
  badges (#150) are installed and accepted at 2026-11-07 22:38:55 UTC. #150 CI:
  3411 default tests (one existing skip), 100% coverage, 515 safety and 292 Chromium
  cases; two additional isolated badge/recovery checks passed. Normal collection,
  mapping/sort and Observed refresh were verified without controller writes.

- Design audit A1-A5 (#151), shared design rules (#152) and the acceptance-discovered
  chart/Drive Observed regressions (#153) are closed by installed `211dfb2`.
  Managed verification: 2026-11-08 11:20:15 UTC; two normal Collector runs, both charts
  after range swaps, automatic/manual Observed, mapping/sort and preserved settings.
  Latest release checks: 3416 default (one existing skip), 100% statement/branch coverage,
  515 safety and 305 Chromium cases; fresh +1 and all green CI.

## Remaining work

| Priority | Work | Required evidence |
| --- | --- | --- |
| 0 | Review/merge the updated documentation PRs and accept handoff | PR #147 and knowledge-vault #4; current-head CI/review; owner records open operational gates or explicit deferrals |
| 1 | Inspect independent app/Collector monitoring; add only missing coverage | Observer independent of the failed app; incident-only failure/recovery tests without RAID faults |
| 2 | Verify recurring backups, retention and separate-copy restore | Scheduled/off-host copy, SQLite integrity and history/delivery checks; live DB untouched |
| 3 | Observe schedule rollover after a natural Patrol completion | Fresh read evidence; no guessed next date or forced operation |

Items 1 and 2 deliberately remain open; close them with evidence or explicit
owner-accepted deferral before final handoff. Optional TB/TiB semantic verification and
broad cosmetic renaming/cleanup remain deferred under [design rules](DESIGN_CONSISTENCY.md). This handoff installs no observer or timer,
sends no Telegram test, restores no database and performs no OS maintenance.

Item 3 is natural observation when needed for diagnosis, not a requirement to delay
handoff or force a controller operation. Acoustic/Silence and real rollback/recovery
effects remain explicit acceptance limits, not exercises to perform on healthy RAID.

## Maintenance boundaries

Use the event/change-driven [maintenance triggers](MAINTENANCE_HANDOFF.md). The owner
requested a handoff and plan, not recurring agent runs. No automatic merge, dependency
upgrade, reboot, firmware update or controller write is enabled.

Physical action validation needs a disposable testbed or genuine maintenance.
New firmware/StorCLI shapes require redacted real JSON and regression tests. Retain
pinned 1.23.02 until a separately reviewed compatibility change. Multi-controller,
non-MegaRAID, expanders, ZFS/mdraid and mobile-first UI remain out of scope. Cooling,
a separate monitoring stack or a broad redesign needs its own scope.

## Release acceptance

Use a PR to main, required local checks, green CI and current-head review before activation.
Preserve secrets, effective settings, the pinned binary and exact commands. Use a
hash-pinned offline bundle, backup, detached worker and durable receipt; verify health
and the new process's normal collection. Application rollback preserves newer DB history.
Use GET-only UI checks for the affected views.

The May proposal remains in Git history. Its intermediate behavior, counts and
maintenance-alert suppression are not the current work queue or security contract.
