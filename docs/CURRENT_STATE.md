# Production state and remaining work

Documentation updated **2026-11-08**. Production acceptance: **2026-11-07**, SERVER. This is dated verification, not a live
health feed. Detailed telemetry and exact backup/status receipts remain private,
in ignored recovery material. Do not publish credentials, serials or raw logs.

## Installed release

**PR #150 is installed and verified.** The owner also confirmed on 2026-11-06 that
physical Locate identification has been checked for all eight cages. The real Buzzer
Enable/Disable setting round trip and audit also passed; acoustic alarm is not certified.

| Item | Verified state |
| --- | --- |
| Application | `f9e736fab4f9b78718d7db9e0774ef0f41ed8f38`, merged [PR #150](https://github.com/AlexBomber12/megaraid-dashboard/pull/150) |
| Public origin | <https://megaraid.alexbomber.com/>; nginx HTTPS and Basic authentication |
| Service | `megaraid-dashboard.service`, user/group `megaraid`, active/running at verification |
| Hardening exception | `NoNewPrivileges=no` for the reviewed sudo-based rollout; fresh-install defaults differ |
| Working directory | `/opt/megaraid-dashboard-ui-f9e736f` |
| Local application port | `8090`; application/database/Collector health verified |
| Metrics | `127.0.0.1:8092/metrics`; 1 controller, 8 PD and 1 VD report optimal (0), Collector timestamp fresh |
| Database | `/var/lib/megaraid-dashboard/megaraid.db`; migration `0009_event_deliveries` |
| Configuration | `/etc/megaraid-dashboard/env`, plus protected rollout env/drop-ins |
| Hardware | LSI 9270CV-8i / SAS 2208, firmware `23.34.0-0019`, Ubuntu 24.04 |
| StorCLI | Pinned `1.23.02`, `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64` |
| Binary SHA-256 | `642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547` |
| Capability | `full-supported-actions`; `CONTROLLER_READ_ONLY=false` |
| Maintenance / destructive mode | Both false after deployment acceptance and after the supervised buzzer check; maintenance closed |
| Alarm Buzzer | Owner-selected Enabled retained and confirmed again at #150 acceptance; maintenance closed |
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
| Shared drive-health assessment | #148, `510ce9b` | SMART/predictive/temperature/error evidence across saved-data views; raw state and command gates preserved |
| Critical alerts, component/metric grades and monitoring uncertainty | #149, `9f2693e` | 51 isolated incident chains, full action catalog, managed deployment and read-only production acceptance |
| Shared health vocabulary and retained error badges | #150, `f9e736f` | Healthy labels across views, two isolated badge/recovery cases and production normal-Collector/UI acceptance |

PR #150 had a fresh current-head review without findings and all three green CI jobs
before merge. Reviewed `ecb73fb1c71a04894f0b84ce79da7f045e53d137` and the installed
squash release have identical trees. [CI run 42416059669](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/42416059669)
passed 3411 default tests (one existing skip), 515 isolated safety cases and 292 Chromium
cases, with 100% statement/branch coverage. Hardware boundaries are mocked.

Managed activation reached `verified` on **2026-11-07 at 22:38:55 UTC**
(23:38:55 Europe/Rome), from #149 / `9f2693e`. The new process saved a normal Collector
sample; application/database/Collector, settings, exact command policy and protected
configuration passed. The checked process journal and browser contained no new
warning/error or traceback. No controller writes, forced poll, migration or dependency
change was used. A read-only follow-up on 2026-11-08 (Europe/Rome) confirmed the same
current healthy release, fresh metrics and five normal Collector completions in the
bounded journal window, without warnings/errors or traceback. This is dated evidence.

Overview, Controller, Drives, all eight drive-detail cards, VD health, position tooltips
and Notifier use **Healthy / Attention / Critical / Unknown**. The retained green
count reads **8 Healthy**. Raw firmware values are separate Controller details;
Onln is absent from ordinary tiles/list rows. Two additional disposable-DB browser
cases confirmed 1 Critical / 7 Healthy, 1 Attention / 7 Healthy, the numerical media
error badge, and recovery to 8 Healthy. These were synthetic faults, not SERVER incidents.

Production reads confirmed the horizontal S0-S7 row, all eight selected positions,
ascending/descending Drives order and retained sort after Refresh now. S0-S3 map to
252:4-7; S4-S7 map to 252:0-3. Stored addresses, serials, command/topology targets and
action gates remain unchanged. Observed advanced automatically from 22:30 to 22:38 UTC;
manual refresh retained the correct sample. No horizontal overflow or fresh-data
monitoring warning appeared. Existing running Consistency Check was observed only.

The MegaRAID exporter uses loopback **8092**, not 8091 (another service on SERVER).
Fresh grades were optimal (0) for 1 controller, 8 PD and 1 VD. Exporter availability
does not prove an independent consumer or outage notification.

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

#149 makes new UBad incidents critical and adds a confirmed initial CacheVault-absence
incident. The `Partially-Degraded` alias reaches detection, relevance, text and metrics.
Historical warning events are not rewritten or resent. The [critical scenario audit](CRITICAL_SCENARIOS_AUDIT.md)
separates simulated delivery from production checks.

One neutral real test was API-accepted on 2026-10-30 and receipt confirmed by the owner.
This proves transport/destination delivery, not a real hardware-fault drill. No repeated
Telegram test was sent for this deployment or documentation update.

Hardware incidents retain confirmation, freshness, identity, deduplication and retry
checks during maintenance. Operator audit noise and arbitrary UI/HTTP exceptions are
not Telegram hardware incidents. A stopped app cannot report its own outage.
Independent availability/freshness monitoring remains unverified. [Delivery contract](TELEGRAM_ALERTS.md).

## Recovery after SSH disconnect or repeated activation

The installed #150 release used [versioned deployment](VERSIONED_DEPLOYMENT.md):
detached systemd execution and a durable root-owned receipt under
`/var/lib/megaraid-dashboard-deploy/FULL_COMMIT_SHA.json`. It finished `verified`;
the exact private backup/context is recorded in recovery material. Check status before
repeating a request. `queued` is not success. Do not restore live DB history after SSH loss.

```bash
systemctl show megaraid-dashboard.service -pActiveState -pSubState -pWorkingDirectory -pMainPID
curl --fail --silent --show-error http://127.0.0.1:8090/healthz
python3 /usr/local/sbin/megaraid-dashboard-deploy.py status --commit f9e736fab4f9b78718d7db9e0774ef0f41ed8f38
```

Guarded managed application rollback is available from its private context; no actual
production rollback/fault drill has been performed. It preserves newer DB snapshots,
audit and notification history. #141 was the historical legacy baseline, adopted as
`verified_external` on 2026-11-01. Old one-shot `/run` receipts and helpers are historical;
do not execute an old staged release or bypass a baseline refusal.

## Remaining work, in order

The shared health-vocabulary follow-up is closed by deployed #150. The project
documentation PR #147 and knowledge-vault PR #4 carry the updated handoff; publication
is owner-authorized, and review/CI/merge and final handoff acceptance remain separate.

1. Inspect existing independent app/Collector monitoring; add only missing incident-only coverage.
2. Verify recurring backups, retention/off-host storage and a separate-copy restore.
3. Observe the next natural Patrol completion when investigating schedule-marker rollover.
4. Acoustic/Silence and fault/replacement acceptance require a suitable separate need/testbed;
   the successful LED and buzzer-setting checks do not certify those effects.

The [maintenance handoff](MAINTENANCE_HANDOFF.md) creates no recurring agent job, timer,
observer or automatic update. Deployment backup is not recurring recovery proof.

## Documentation map

- [Critical scenario audit](CRITICAL_SCENARIOS_AUDIT.md), [drive-health contract](DRIVE_HEALTH.md).
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
