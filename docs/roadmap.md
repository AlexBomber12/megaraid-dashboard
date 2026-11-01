# MegaRAID Dashboard — maintenance roadmap

Updated: **2026-11-01**. Installed application: `27af3da` (#141) on SERVER.
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
  Isolated tooling tests are not proof that a managed production update occurred.
- Reconciled source/Obsidian documentation and [maintenance handoff](MAINTENANCE_HANDOFF.md).

## Remaining work

| Priority | Work | Required evidence |
| --- | --- | --- |
| 1 | Inspect independent app/Collector monitoring; add only missing coverage | Observer independent of the failed app; incident-only failure/recovery tests without RAID faults |
| 2 | Verify recurring backups, retention and separate-copy restore | Scheduled/off-host copy, SQLite integrity and history/delivery checks; live DB untouched |
| 3 | Observe schedule rollover after a natural Patrol completion | Fresh read evidence; no guessed next date or forced operation |

Items 1 and 2 deliberately remain open. This handoff installs no observer or timer,
sends no Telegram test, restores no database and performs no OS maintenance.

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
