# Production state and remaining work

Documentation updated **2026-11-08**. Production acceptance: **2026-11-08**, SERVER. This is dated verification, not a live
health feed. Detailed telemetry and exact backup/status receipts remain private,
in ignored recovery material. Do not publish credentials, serials or raw logs.

## Installed release

**PR #155 is installed and verified; #154 and all earlier fixes are included.** The owner also confirmed on 2026-11-06 that
physical Locate identification has been checked for all eight cages. The real Buzzer
Enable/Disable setting round trip and audit also passed; acoustic alarm is not certified.

| Item | Verified state |
| --- | --- |
| Application | `8da1a9369342212767c49af190e2e513aae988d7`, merged [PR #155](https://github.com/AlexBomber12/megaraid-dashboard/pull/155) |
| Public origin | <https://megaraid.alexbomber.com/>; nginx HTTPS and Basic authentication |
| Service | `megaraid-dashboard.service`, user/group `megaraid`, active/running at verification |
| Hardening exception | `NoNewPrivileges=no` for the reviewed sudo-based rollout; fresh-install defaults differ |
| Working directory | `/opt/megaraid-dashboard-ui-8da1a93` |
| Local application port | `8090`; application/database/Collector health verified |
| Metrics | `127.0.0.1:8092/metrics`; 1 controller, 8 PD and 1 VD report optimal (0), Collector timestamp fresh |
| Database | `/var/lib/megaraid-dashboard/megaraid.db`; migration `0009_event_deliveries` |
| Configuration | `/etc/megaraid-dashboard/env`, plus protected rollout env/drop-ins |
| Hardware | LSI 9270CV-8i / SAS 2208, firmware `23.34.0-0019`, Ubuntu 24.04 |
| StorCLI | Pinned `1.23.02`, `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64` |
| Binary SHA-256 | `642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547` |
| Capability | `full-supported-actions`; `CONTROLLER_READ_ONLY=false` |
| Maintenance / destructive mode | Both false after deployment acceptance and after the supervised buzzer check; maintenance closed |
| Alarm Buzzer | Owner-selected Enabled retained and confirmed again at #155 acceptance; maintenance closed |
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
| Design audit A1-A5 | #151 | Isolated fault/overflow/time/unit scenarios; included in current #155 |
| Shared design rules | #152, `76ebf1f` | Service verified, but UI acceptance found chart/Observed defects; superseded by #153 |
| Chart lifecycle and Drive Observed correction | #153, `211dfb2` | Real range swaps, automatic/manual saved-sample refresh and two normal Collector runs |
| Reduced UI noise and clearer Events | #154, `970d16b` | Included in #155 deployment; technical values and actionable reasons retained, duplicate sparkline/tile badge removed |
| Live filtered Events and threshold label geometry | #155, `8da1a93` | Isolated excluded-event/cursor and text-box regressions; production filter polling, drawn charts and normal Collector/UI acceptance |

PR #155 had a fresh current-head Codex +1 without findings and all three green CI jobs
before merge. Reviewed head `fe73a0218728224fd026af4b91c2de8ca3e5c9bb` and installed
merge release `8da1a93` have identical trees. [Release acceptance](RELEASE_155_ACCEPTANCE.md)
records #154's changes and both independently reproduced #155 fixes.

| PR | Delivered scope | Default tests | Isolated safety | Chromium |
| --- | --- | --- | --- | --- |
| [#151](https://github.com/AlexBomber12/megaraid-dashboard/pull/151) | A1-A5 signal/layout/chart/time/Celsius fixes | 3412 + one existing skip | 515 | 299 |
| [#152](https://github.com/AlexBomber12/megaraid-dashboard/pull/152) | Shared summaries/tokens/chart theme and Unknown counts | 3416 + one existing skip | 515 | 301 |
| [#153](https://github.com/AlexBomber12/megaraid-dashboard/pull/153) | Chart lifecycle and guarded Drive Observed correction | 3416 + one existing skip | 515 | 305 |
| [#154](https://github.com/AlexBomber12/megaraid-dashboard/pull/154) | Reduced duplicate signals, Events labels/counts, simpler technical/action rows | 3411 + one existing skip | 515 | 305 |
| [#155](https://github.com/AlexBomber12/megaraid-dashboard/pull/155) | Global filter revisions independent of event cursor; non-overlapping measured threshold labels | 3430 + one existing skip | 515 | 315 |

These are their respective release checks, not new tests for this documentation.
All retained the 100% statement/branch gate. Latest released [CI run 42485484927](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/42485484927)
passed all three jobs, with fresh head review. #155 also passed 60 focused checks.

**Earlier acceptance findings remain historical evidence.** #152 reached service
verification, but blank swapped canvases and stale Drive Observed were accepted only
after #153. The subsequent #154 review found filtered Events categories/counts could
remain stale for excluded events; a separate close-threshold overlap predated #154.
#155 closes both without altering stored events or hardware behavior.

Managed #155 activation from `211dfb2` reached **verified on 2026-11-08 at 14:04:55 UTC**
(15:04:55 Europe/Rome). The new process completed a normal Collector run; a read-only
follow-up during this documentation update confirmed two completions, fresh healthy
1 controller / 8 PD / 1 VD metrics, app/database/Collector ok, preserved settings,
exact policy and protected configuration, with no new warning/error events or traceback
in the checked process journal. No forced collection, controller write, migration,
dependency change or extra Telegram test was used. Detailed receipts stay private.

| Production scenario | Observed result on 2026-11-08 after #155 |
| --- | --- |
| Drive/Controller history | Both drive charts drawn with unique titles; 7d/30d drive and 1h/24h RoC ranges work; threshold labels readable |
| Automatic Drive Observed | Saved sample 13:55:10 -> 14:04:50 UTC (14:55 -> 15:04 Europe/Rome), without reload |
| Manual Refresh now | Completed Live state reads the same latest saved 14:04:50 UTC sample; both charts remain drawn; no forced hardware poll |
| Events category/severity/combined filters | Query, counts and correct visible/empty result retained; quiet poll advanced Live 15:06:16 -> 15:07:17 Europe/Rome without filter change |
| Overview and controls | 8 Healthy, S0-S7 mapping retained, Buzzer Enabled and maintenance closed; existing Patrol Running/CC Stopped observed only |

New excluded-category arrivals and close-threshold extremes were tested only with
disposable data, at 1440/390 px; no events were injected and no threshold was changed
in production. Earlier #153 all-range, reverse-sort and multi-sample evidence remains
dated history, not a newly repeated #155 physical/sort drill.

Physical labels remain S0-S3 -> 252:4-7 and S4-S7 -> 252:0-3. Real addresses,
serial identity, command/topology targets and action gates are unchanged. Health uses
Healthy / Attention / Critical / Unknown; raw firmware codes stay diagnostic. Unknown
health is counted; unknown health and modes are neutral. An unrecognized firmware drive
state remains Attention. Fault reasons, severity colours and table/detail counters remain. #154 removes the
redundant bare error-count badge on backplane tiles and the duplicate Error Trend
sparkline; Error Counter History remains. Health assessment, raw diagnostic values,
notification eligibility and action gates are unchanged. Fault/recovery tests use
isolated snapshots, never induced production faults.

The MegaRAID exporter uses loopback **8092**, not 8091 (another service). Fresh grades
were optimal (0) for 1 controller / 8 PD / 1 VD. Exporter availability and the app's own
Notifier do not prove an independent outage/freshness observer.

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

The installed #155 release used [versioned deployment](VERSIONED_DEPLOYMENT.md):
detached systemd execution and a durable root-owned receipt under
`/var/lib/megaraid-dashboard-deploy/FULL_COMMIT_SHA.json`. It finished `verified`;
the exact private backup/context is recorded in recovery material. Check status before
repeating a request. `queued` is not success. Do not restore live DB history after SSH loss.

```bash
systemctl show megaraid-dashboard.service -pActiveState -pSubState -pWorkingDirectory -pMainPID
curl --fail --silent --show-error http://127.0.0.1:8090/healthz
python3 /usr/local/sbin/megaraid-dashboard-deploy.py status --commit 8da1a9369342212767c49af190e2e513aae988d7
```

Guarded managed application rollback is available from its private context; no actual
production rollback/fault drill has been performed. It preserves newer DB snapshots,
audit and notification history. #141 was the historical legacy baseline, adopted as
`verified_external` on 2026-11-01. Old one-shot `/run` receipts and helpers are historical;
do not execute an old staged release or bypass a baseline refusal.

## Remaining work, in order

Shared health vocabulary, A1-A5, #153 lifecycle/Observed findings, #154 presentation
simplification and the two #155 findings are closed by the accepted current release.
Capacity semantics and broad cosmetic renaming remain optional, under [design rules](DESIGN_CONSISTENCY.md).
The earlier documentation PR [#147](https://github.com/AlexBomber12/megaraid-dashboard/pull/147)
and [knowledge-vault #4](https://github.com/AlexBomber12/knowledge-vault/pull/4) were both
merged on 2026-11-08; they are no longer pending work. This follow-up reconciles #154-155.
Local Obsidian update, publication/merge of this follow-up and cross-device sync remain
separate results. Final handoff requires evidence or an explicit owner-accepted deferral
for the following two operational gaps, then owner acceptance.

1. Inspect existing independent app/Collector monitoring; add only missing incident-only coverage.
2. Verify recurring backups, retention/off-host storage and a separate-copy restore.
3. Observe the next natural Patrol completion when investigating schedule-marker rollover.
4. Acoustic/Silence and fault/replacement acceptance require a suitable separate need/testbed;
   the successful LED and buzzer-setting checks do not certify those effects.

The [maintenance handoff](MAINTENANCE_HANDOFF.md) creates no recurring agent job, timer,
observer or automatic update. Deployment backup is not recurring recovery proof.

## Documentation map

- [Release #155 acceptance](RELEASE_155_ACCEPTANCE.md).
- [Design consistency and regression checks](DESIGN_CONSISTENCY.md).
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
