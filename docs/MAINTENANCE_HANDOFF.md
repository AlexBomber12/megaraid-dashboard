# Maintenance handoff

Updated **2026-11-07** for Alexey. The owner requested a handoff and maintenance plan,
without recurring automatic checks. No new chat, automation, observer or timer is created.

## Start here

1. Read [current state](CURRENT_STATE.md) and verify the actual installed revision.
   `9f2693e` (#149) is the verified production application at this checkpoint.
2. Read [the runbook](RUNBOOK.md), [notification contract](TELEGRAM_ALERTS.md) and
   [deployment procedure](VERSIONED_DEPLOYMENT.md) for the task.
3. Begin read-only. An old note/screenshot/incident is not permission to operate a
   disk, repeat a deployment or change notification state.

Completed UI/Learn Cycle findings are closed. Independent app/Collector monitoring and
recurring backup/restore proof remain open in [the roadmap](roadmap.md). Do not describe
them as installed/tested merely because this handoff exists.

## Release #149 acceptance and transfer gates

Managed activation was verified on 2026-11-07 at 16:55:45 UTC. Normal Collector,
health, 1/8/1 optimal exporter grades on loopback 8092, all eight drive views,
physical mapping/sort and automatic/manual Observed refresh passed. Settings/policy
were preserved; Buzzer remains Enabled. No controller writes or real fault drill.
[The scenario ledger](CRITICAL_SCENARIOS_AUDIT.md) records 3404 default tests
(one existing skip), 100% coverage, 515 safety cases, 291 browser cases and 51
isolated incident chains; that is software evidence, not physical recovery certification.

Before closing handoff, publish/review the project and vault documentation PRs,
finish the requested shared health vocabulary, and verify independent availability/
freshness monitoring plus recurring/off-host backup and separate-copy restore.
The exporter and deployment backup already working do not close the latter two items.
Alexey owns acceptance and any future authorization. Record the result or explicit
deferral of each open item; do not silently turn an unverified item into completed work.

Natural Patrol schedule rollover, acoustic/Silence behavior and production rollback
are retained limitations, not reasons to exercise healthy RAID. A safe separate
testbed or genuine authorized maintenance is the acceptance boundary for physical
replacement/rebuild/spin-down/foreign recovery.

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
