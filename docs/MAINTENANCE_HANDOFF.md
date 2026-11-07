# Maintenance handoff

Updated **2026-11-08** for Alexey. The owner requested a handoff and maintenance plan,
without recurring automatic checks. No new chat, automation, observer or timer is created.

## Start here

1. Read [current state](CURRENT_STATE.md) and verify the actual installed revision.
   `f9e736f` (#150) is the verified production application at this checkpoint.
2. Read [the runbook](RUNBOOK.md), [notification contract](TELEGRAM_ALERTS.md) and
   [deployment procedure](VERSIONED_DEPLOYMENT.md) for the task.
3. Begin read-only. An old note/screenshot/incident is not permission to operate a
   disk, repeat a deployment or change notification state.

Completed UI/Learn Cycle findings are closed. Independent app/Collector monitoring and
recurring backup/restore proof remain open in [the roadmap](roadmap.md). Do not describe
them as installed/tested merely because this handoff exists.

## Release #150 acceptance and transfer gates

Managed activation was verified on 2026-11-07 at 22:38:55 UTC (23:38:55 Europe/Rome).
Normal Collector, application/database health, preserved settings/policy/configuration,
fresh 1/8/1 optimal exporter grades on loopback 8092, physical mapping/sort and both
Observed refresh paths passed. Healthy labels and the retained 8 Healthy count were
checked across production views. Attention/Critical and numerical error badges passed
two isolated fault/recovery cases. Buzzer remains Enabled. No controller writes or
production fault drill. #150 CI passed 3411 default tests (one existing skip), 100%
statement/branch coverage, 515 safety cases and 292 browser cases. The earlier
[critical scenario ledger](CRITICAL_SCENARIOS_AUDIT.md) retains its #149 counts and
51 incident chains. A read-only 2026-11-08 follow-up confirmed normal operation.

| Gate | Status | Owner and closure evidence |
| --- | --- | --- |
| Shared UI health vocabulary | Closed by deployed #150 | Production views and isolated fault/recovery checks recorded |
| Source/vault documentation | Publication authorized; PR #147 and vault #4 open | Alexey: fresh CI/review, merge and acceptance of this handoff |
| Independent app/Collector monitoring | Unverified | Alexey: identify the existing observer and prove failure/recovery plus stale-Collector detection, independently of the app, without RAID faults |
| Recurring recoverable backups | Unverified | Alexey: prove schedule, retention, off-host copy and separate-copy restore integrity/history/delivery state; live DB untouched |

The healthy exporter and deployment backup do not close the monitoring/recurring
recovery gates. Record evidence or an explicit owner-accepted deferral before closing
handoff; an unverified item must not silently become completed work. These notes
create no recurring agent job, monitor, backup timer or autonomous update authority.

Natural Patrol schedule rollover, acoustic/Silence behavior and production rollback
remain documented limits. Physical replacement/rebuild/spin-down/foreign recovery
acceptance belongs on a disposable testbed or genuine separately authorized maintenance,
not an induced fault in healthy RAID. They are not routine production acceptance steps.

## Maintenance triggers

| Trigger | Action | Acceptance evidence |
| --- | --- | --- |
| Hardware alert | Match Events ID, time, identity and fresh data; use the incident runbook | Confirmed facts and unknowns before any separately authorized physical action |
| Stale data, UI outage or suspected lost alert | Read service/health/current-process journal and delivery state | Identify the failed layer; preserve dedup/retry and incident history |
| Approved app update | PR, review/CI, offline bundle, plan/start/status | Correct revision, unchanged settings/policy, normal collection and relevant GET UI checks |
| Approved OS/kernel update | Confirm recoverable backup and baseline; verify validated StorCLI JSON/service after maintenance | Wrapper compatibility and fresh normal collection; no induced RAID fault |
| Restore/security readiness review | Inspect existing backups/access; restore only a separate copy | Integrity/history/delivery proof; never start collector/notifiers against a restored copy |
| New firmware/StorCLI shape | Preserve redacted JSON; add boundary tests through a PR | Explicit known/unknown behavior, no textual-table parsing or guessed topology |

These are triggers and criteria, not a schedule or authority for autonomous changes.
The owner decides the frequency of any later review or automation separately.

## Operational boundaries

- Healthy RAID is not a testbed. No forced CC/Patrol/Learn Cycle, disk state,
  replacement, firmware/clock/schedule change or induced fault for acceptance.
- Preserve `megaraid`, protected settings, pinned 1.23.02, exact grants, serial/topology
  checks, maintenance/destructive gates and audit history.
- Hardware incidents retain eligibility during maintenance; UI/HTTP errors and operator
  audit noise are not automatically Telegram hardware incidents.
- All eight physical Locate LEDs were confirmed by the owner on 2026-11-06.
  Mocked tests and production reads alone do not prove sound, spin-down, replacement,
  foreign recovery, media recovery or rebuild effects.
- Inspect durable state before retrying after disconnect. Application rollback does
  not restore/delete the live DB or reset notification state.

## Evidence and continuity

Public docs hold release/test summaries and procedures. Detailed telemetry, exact
backup/status receipts and secrets stay in private recovery material outside ephemeral
`/run`; encrypt credential backups and keep them out of Git.

Obsidian entry points: project overview and `20-homelab/notifications/megaraid.md` in
`AlexBomber12/knowledge-vault`. Preserve local frontmatter/mirror metadata and unrelated
notes. GitHub merge, local note edit and synchronization to another device are distinct
events: report only verified steps.

New maintainer prompt: "Read CURRENT_STATE, MAINTENANCE_HANDOFF and the relevant runbook.
Verify live facts read-only. Preserve RAID and notification/audit history. Work through
a reviewed PR; activate only within the owner's current task authorization."
