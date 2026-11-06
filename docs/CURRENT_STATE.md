# Production state and remaining work

Evidence checkpoint: **2026-11-06**, SERVER. This is dated verification, not a live
health feed. Detailed telemetry and exact backup/status receipts remain private,
in ignored recovery material. Do not publish credentials, serials or raw logs.

## Installed release

**PR #146 is installed and verified.** The owner also confirmed on 2026-11-06 that
physical Locate identification has been checked for all eight cages. The real Buzzer
Enable/Disable setting round trip and audit also passed; acoustic alarm is not certified.

| Item | Verified state |
| --- | --- |
| Application | `8b60b7242c0b908ff4fc86a8f07a8aa983288109`, merged [PR #146](https://github.com/AlexBomber12/megaraid-dashboard/pull/146) |
| Public origin | <https://megaraid.alexbomber.com/>; nginx HTTPS and Basic authentication |
| Service | `megaraid-dashboard.service`, user/group `megaraid`, active/running at verification |
| Hardening exception | `NoNewPrivileges=no` for the reviewed sudo-based rollout; fresh-install defaults differ |
| Working directory | `/opt/megaraid-dashboard-ui-8b60b72` |
| Local application port | `8090`; application/database/Collector health verified |
| Database | `/var/lib/megaraid-dashboard/megaraid.db`; migration `0009_event_deliveries` |
| Configuration | `/etc/megaraid-dashboard/env`, plus protected rollout env/drop-ins |
| Hardware | LSI 9270CV-8i / SAS 2208, firmware `23.34.0-0019`, Ubuntu 24.04 |
| StorCLI | Pinned `1.23.02`, `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64` |
| Binary SHA-256 | `642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547` |
| Capability | `full-supported-actions`; `CONTROLLER_READ_ONLY=false` |
| Maintenance / destructive mode | Both false after deployment acceptance and after the supervised buzzer check; maintenance closed |
| Alarm Buzzer | Enabled, confirmed by the normal sample at 2026-11-06 18:13 UTC after the owner selected Enabled; maintenance closed |
| Approval policy | 79 exact writes, 25 reads and 8 explicit replacement topologies; preserved |

Capability is an authorization ceiling, not permission to exercise a healthy array.
Authentication, CSRF, identity, exact approval, audit and maintenance/destructive gates
still apply. Locate Stop retains only its documented exception. Mark UBad is unavailable.
Do not apply fresh-install `raid-monitor` defaults over this deployment.

## Delivered changes and evidence

| Change | Release | Verification boundary |
| --- | --- | --- |
| Operator action-chain safety fixes | #134, `25c2b74` | Fake StorCLI software chains; [readiness audit](OPERATOR_ACTION_READINESS.md) |
| Compact Actions | #135, `16c06d9` | Deployed with existing safety gates |
| Drive position and shared Collector/status displays | #136, `8b166c6` | Saved identity-bound evidence and production GET checks |
| CacheVault next-learn recovery | #137, `cfad744` | Normal collection and persisted date; no Learn Cycle started |
| Chart times and reduced action duplication | #139, `942805f` | Chart ranges, single-VD mode hiding and Mark Bad removal |
| Observed states and retained Patrol schedule | #140, `42b9f6e` | Compact collection-to-page path and scenario ledger |
| Reported schedule and live Observed header | #141, `27af3da` | Real normal samples: automatic/manual refresh without full reload |
| Physical cage labels, Drives sort, buzzer aliases and managed install permissions | #146, `8b60b72` | Managed production activation and authenticated UI checks; physical LED acceptance confirmed by owner |

PR #146 had a fresh current-head Codex +1 and all three green CI jobs before merge.
Reviewed head `fff4e73cf4ef07764a74eb3d238666c851b56473` and the installed squash release
have identical trees. [CI run 42128112501](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/42128112501)
passed 3276 default tests (one existing skip), 515 isolated safety cases and 237 Chromium
cases, with 100% statement/branch coverage. Automated hardware boundaries are mocked.

Managed activation reached `verified` on **2026-11-06 at 07:58:54 UTC** after a newer
normal sample from the new process. Configuration, policy and effective settings were
preserved. App/database/Collector health passed; the bounded checked-process journal
and browser had no warning/error events. Deployment and post-install UI checks issued
no controller writes. No migrations, dependency changes, forced collection or RAID jobs.

Authenticated production checks covered one horizontal S0-S7 row on Overview and all
eight detail diagrams, matching titles/highlights/Previous/Next destinations, Drives
ascending/descending physical order and retained order through Refresh now. Physical
labels are presentation only: S0-S3 -> 252:4-7; S4-S7 -> 252:0-3. URLs, stored slot IDs,
serial identity and command/topology targets still use actual controller addresses.

The firmware's schedule-marker rollover rule after natural Patrol completion remains
unverified. Reported schedule preserves its value without promising a future start;
Observed follows the saved sample. Do not guess dates, change clocks/schedules or
start/stop Patrol or CC for acceptance. [Scenario ledger](ACTIONS_SCENARIOS.md).

Numeric CacheVault Learn Cycle recovery remains included; its INFO event is not an
incident. Unsupported malformed values still warn. [Parsing contract](CACHEVAULT_LEARN_TIME.md).

## Physical acceptance

On 2026-11-01 the owner authorized activity-for-locate Off -> On. Readback, unchanged
array state and other settings were verified. On 2026-11-05 physical identification
was confirmed working; on 2026-11-06 the owner confirmed **all eight LEDs checked**.
This observation does not turn command acceptance into a software-visible LED sensor.
See [hardware acceptance](LOCATE_ACTIVITY_VERIFICATION.md).

The real supervised Buzzer chain passed on 2026-11-06: initial `Disable` -> Enable
accepted -> normal Collector confirmed `On` at 14:03 UTC -> Disable accepted ->
normal Collector confirmed `Disable` at 14:08 UTC. Both request/outcome events say
succeeded, and separate observed-change events match. No uncertainty remained or
automatic retry occurred. Initial Disabled was restored and maintenance closed.
Afterward the owner selected Enabled as the desired persistent setting. A fresh
normal sample at **2026-11-06 18:13 UTC** already confirmed Enabled, with maintenance
closed; this follow-up required no additional controller command. The round-trip
test above used two alarm-setting writes and no array operations; no acoustic fault was
induced and Silence was not exercised. Spin-down, replacement, foreign recovery
and rebuild effects remain unverified. Never induce a healthy-RAID fault for a checklist.

## Telegram

One neutral real test was API-accepted on 2026-10-30 and receipt confirmed by the owner.
This proves transport/destination delivery, not a real hardware-fault drill. No repeated
Telegram test was sent for this deployment or documentation update.

Hardware incidents retain confirmation, freshness, identity, deduplication and retry
checks during maintenance. Operator audit noise and arbitrary UI/HTTP exceptions are
not Telegram hardware incidents. A stopped app cannot report its own outage.
Independent availability/freshness monitoring remains unverified. [Delivery contract](TELEGRAM_ALERTS.md).

## Recovery after SSH disconnect or repeated activation

The installed #146 release used [versioned deployment](VERSIONED_DEPLOYMENT.md):
detached systemd execution and a durable root-owned receipt under
`/var/lib/megaraid-dashboard-deploy/FULL_COMMIT_SHA.json`. It finished `verified`;
the exact private backup/context is recorded in recovery material. Check status before
repeating a request. `queued` is not success. Do not restore live DB history after SSH loss.

```bash
systemctl show megaraid-dashboard.service -pActiveState -pSubState -pWorkingDirectory -pMainPID
curl --fail --silent --show-error http://127.0.0.1:8090/healthz
python3 /usr/local/sbin/megaraid-dashboard-deploy.py status --commit 8b60b7242c0b908ff4fc86a8f07a8aa983288109
```

Guarded managed application rollback is available from its private context; no actual
production rollback/fault drill has been performed. It preserves newer DB snapshots,
audit and notification history. #141 was the historical legacy baseline, adopted as
`verified_external` on 2026-11-01. Old one-shot `/run` receipts and helpers are historical;
do not execute an old staged release or bypass a baseline refusal.

## Remaining work, in order

1. Inspect existing independent app/Collector monitoring; add only missing incident-only coverage.
2. Verify recurring backups, retention/off-host storage and a separate-copy restore.
3. Observe the next natural Patrol completion when investigating schedule-marker rollover.
4. Acoustic/Silence and fault/replacement acceptance require a suitable separate need/testbed;
   the successful LED and buzzer-setting checks do not certify those effects.

The [maintenance handoff](MAINTENANCE_HANDOFF.md) creates no recurring agent job, timer,
observer or automatic update. Deployment backup is not recurring recovery proof.

## Documentation map

- [Roadmap](roadmap.md), [maintenance handoff](MAINTENANCE_HANDOFF.md).
- [Versioned deployment](VERSIONED_DEPLOYMENT.md), [runbook](RUNBOOK.md),
  [fresh installation](../INSTALL.md), [troubleshooting](../TROUBLESHOOTING.md).
- [Actions](ACTIONS_REFERENCE.md), [scenarios](ACTIONS_SCENARIOS.md),
  [readiness](OPERATOR_ACTION_READINESS.md), [safety](OPERATOR_SAFETY.md).
- [Full capability](STORCLI_FULL_ROLLOUT.md), [StorCLI compatibility](STORCLI_123_COMPATIBILITY.md),
  [GET-only smoke](PRODUCTION_READ_ONLY_SMOKE.md), [Learn Cycle](CACHEVAULT_LEARN_TIME.md).
- [Telegram](TELEGRAM_ALERTS.md), [Obsidian handoff](knowledge-vault-megaraid-update.md).

Older audits remain dated evidence. Historical instructions grant no present controller
or deployment authorization. Local Obsidian edits, PR publication and cross-device sync
are distinct results and must be reported separately.
