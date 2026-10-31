# Production state and remaining work

Evidence checkpoint: **2026-10-31**, SERVER (`192.168.50.2`). This is an observation,
not a live status page or certification of future hardware health. Deployment evidence
is retained in private, ignored artifacts; secrets and hardware serials are not published.

## Installed release

| Item | Verified state |
| --- | --- |
| Application | `cfad744a5867c5009bfc06a9804cceaeac49be82`, merged [PR #137](https://github.com/AlexBomber12/megaraid-dashboard/pull/137) |
| Public origin | <https://megaraid.alexbomber.com/>; nginx HTTPS and Basic authentication |
| Service | `megaraid-dashboard.service`, user/group `megaraid`, active/running |
| Deployment hardening exception | `NoNewPrivileges=no` for the reviewed sudo-based rollout; the fresh-install unit template differs |
| Working directory | `/opt/megaraid-dashboard-ui-cfad744` |
| Local application port | `8090`; `/healthz` reported application, database and collector `ok` |
| Database | `/var/lib/megaraid-dashboard/megaraid.db`; delivery state uses migration `0009_event_deliveries` |
| Base configuration | `/etc/megaraid-dashboard/env`, plus protected rollout environment files/drop-ins |
| Hardware | LSI 9270CV-8i / SAS 2208, firmware `23.34.0-0019`, Ubuntu 24.04 |
| Selected StorCLI | Pinned `1.23.02`, `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64` |
| Binary SHA-256 | `642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547` |
| Capability | `full-supported-actions`; `CONTROLLER_READ_ONLY=false` |
| Maintenance / destructive mode | Both false at the checkpoint |
| Protected approval policy | 79 exact writes, 25 reads and 8 explicit replacement topologies; unchanged by recent updates |

Capability is an authorization ceiling, not permission to test an action. State,
identity, exact approval, authentication, CSRF, audit and maintenance/destructive
gates still apply. Locate Stop has only its documented recovery exception. Mark
UBad remains unavailable. Installer defaults still use `raid-monitor` and read-only
mode; do not apply fresh-install defaults over this existing SERVER deployment.

## Delivered changes and evidence

| Change | Release/evidence | Scope of verification |
| --- | --- | --- |
| Operator action-chain safety fixes | [PR #134](https://github.com/AlexBomber12/megaraid-dashboard/pull/134), `25c2b74` | Software chains with fake StorCLI; see [readiness audit](OPERATOR_ACTION_READINESS.md) |
| Compact Actions design | [PR #135](https://github.com/AlexBomber12/megaraid-dashboard/pull/135), `16c06d9` | Deployed; exact command policy and safety gates preserved |
| Drive position, Collector footer, Patrol schedule display | [PR #136](https://github.com/AlexBomber12/megaraid-dashboard/pull/136), `8b166c6` | Production GET-only UI check: rows `4,5,6,7,3,2,1,0`, consistent ages, observed Auto mode; no browser errors/overflow in checked views |
| CacheVault next-learn parsing | [PR #137](https://github.com/AlexBomber12/megaraid-dashboard/pull/137), `cfad744` | New-process normal collection, persisted UTC date, journal and service health |

PR #137 had a fresh current-head Codex +1 and successful CI before merge and activation.
Reviewed head `b3e0adfdb95e4ce985e604c9d4c852b778e46f0f` and squash release have identical
source trees. [CI run 41148435036](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/41148435036)
passed 3,107 unit tests (one existing skip), 515 isolated integration tests and 211
Chromium tests, with 100% statement/branch coverage. These tests mock the hardware boundary.

Activation finished at **13:08:51 Europe/Rome (12:08:51 UTC)** on 2026-10-31.
Snapshot 50740 was produced by the new process. At **13:45:44 Europe/Rome**, the bounded
current-process journal contained eight normal collector completions and eight INFO
`cachevault_next_learn_time_recovered` records, with no warning/error events or tracebacks.
There was no separate post-#137 browser audit; its production proof is persisted data,
journal and service health. The preceding UI checks belong to #136.

The malformed legacy value `2026/13/07  17:50:43 (847389043 seconds)` is interpreted
as **2026-11-07 17:50:43 UTC (18:50:43 Europe/Rome)** using the documented numeric
counter. Raw calendar text is preserved. No firmware, controller clock, schedule,
binary or database-schema change was made; no Learn Cycle was started. See
[the cause, source and parsing limits](CACHEVAULT_LEARN_TIME.md).

## Telegram

Automatic Telegram delivery is enabled in the observed production configuration.
The neutral test was accepted by the API at **2026-10-30 22:11:08 UTC**
(23:11:08 Europe/Rome), and the owner confirmed receipt. Exactly one test message
was sent. This proves destination/transport delivery, not a real hardware-incident drill.

Hardware incidents retain freshness, identity, confirmation, deduplication and retry
checks during maintenance. Operator action audit noise and arbitrary UI/HTTP exceptions
are not Telegram incidents. A process that is completely stopped cannot notify about
its own outage. Independent availability/freshness monitoring is a remaining task,
not a deployed feature. See [the delivery contract](TELEGRAM_ALERTS.md).

## Recovery after SSH disconnect or repeated activation

The successful deployment status is
`/run/megaraid-ui-cfad744-xls_62vx/status.json`; its completed state is `verified`.
The backup is `/var/backups/megaraid-ui-cfad744-urgg02gz`. `/run` evidence is ephemeral
and may disappear on reboot; retain durable deployment records separately.

The one-shot `deploy_cfad744.py` expects the previous `8b166c6` baseline. Once the
new release is active, rerunning it fails with
`Service differs from the approved running baseline` **before backup/install/restart**.
This is not evidence that the successful deployment rolled back. Do not edit the
baseline guard or retry blindly. Inspect read-only:

```bash
systemctl show megaraid-dashboard.service -pActiveState -pSubState -pWorkingDirectory -pMainPID
curl --fail --silent --show-error http://127.0.0.1:8090/healthz
```

Read the matching status file if it still exists. A different path, unhealthy service,
or non-verified status requires diagnosis. Do not activate an old staged release or
restore the live database merely because an SSH session disconnected. The completed
cfad744 activation preserved settings/policy and executed no controller writes.

## Remaining work, in order

1. **Independent monitoring:** inspect existing host monitoring, then add only the missing
   application-availability and Collector-freshness checks, with incident-only notification.
   Verify observer failure/Telegram failure behavior without inducing RAID faults.
2. **Backup and restore evidence:** verify the existing recurring backup schedule and
   retention. Restore a copy to a separate temporary database and check integrity/history/
   delivery state. Deployment backups exist; recurring restore readiness is not yet proved.
3. **Deployment tooling:** replace ad-hoc one-shot helpers with a versioned procedure,
   durable status and an explicit already-installed result. Keep baseline checks and
   rollback safeguards; never relax them just to make a repeat command succeed.
4. **Documentation maintenance:** keep this checkpoint, runbooks and Obsidian aligned
   when a release or operational observation changes. A docs PR does not redeploy the app.

There are no unresolved known software defects from the completed button/UI/Learn Cycle
checks. This does not establish that the application has no undiscovered defects.
Physical LED, buzzer, spin-down, replacement, foreign recovery and rebuild effects
remain unverified on this firmware. Do not exercise them on the healthy RAID to close
a checklist; use a separate testbed or a genuine authorized maintenance need.

## Documentation map

- [Roadmap](roadmap.md): current priorities and completion boundaries.
- [Runbook](RUNBOOK.md), [installation](../INSTALL.md), [troubleshooting](../TROUBLESHOOTING.md).
- [Operator action readiness](OPERATOR_ACTION_READINESS.md), [safety contract](OPERATOR_SAFETY.md),
  [full capability procedure](STORCLI_FULL_ROLLOUT.md), [read-only smoke](PRODUCTION_READ_ONLY_SMOKE.md).
- [UI corrections](UI_STATUS_FIXES.md), [StorCLI compatibility](STORCLI_123_COMPATIBILITY.md),
  [Learn Cycle](CACHEVAULT_LEARN_TIME.md), [Telegram](TELEGRAM_ALERTS.md).
- [Obsidian handoff](knowledge-vault-megaraid-update.md): scope, synchronization and source links.

Historical audit counts, initial binary inventories and earlier deployment holds are
dated evidence, not current operational instructions. Follow this checkpoint for
SERVER-specific paths; verify live state before any future change.
