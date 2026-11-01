# Production state and remaining work

Evidence checkpoint: **2026-11-01**, SERVER. This is dated verification, not a live
health feed. Detailed telemetry and exact backup/status receipts remain private,
in ignored recovery material. Do not publish credentials, serials or raw logs.

## Installed release

| Item | Verified state |
| --- | --- |
| Application | `27af3da95318ce3f902eaa9bf6a86dad89710da2`, merged [PR #141](https://github.com/AlexBomber12/megaraid-dashboard/pull/141) |
| Public origin | <https://megaraid.alexbomber.com/>; nginx HTTPS and Basic authentication |
| Service | `megaraid-dashboard.service`, user/group `megaraid`, active/running at verification |
| Hardening exception | `NoNewPrivileges=no` for the reviewed sudo-based rollout; fresh-install defaults differ |
| Working directory | `/opt/megaraid-dashboard-ui-27af3da` |
| Local application port | `8090`; application/database/Collector health verified |
| Database | `/var/lib/megaraid-dashboard/megaraid.db`; migration `0009_event_deliveries` |
| Configuration | `/etc/megaraid-dashboard/env`, plus protected rollout env/drop-ins |
| Hardware | LSI 9270CV-8i / SAS 2208, firmware `23.34.0-0019`, Ubuntu 24.04 |
| StorCLI | Pinned `1.23.02`, `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64` |
| Binary SHA-256 | `642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547` |
| Capability | `full-supported-actions`; `CONTROLLER_READ_ONLY=false` |
| Maintenance / destructive mode | Both false at verification |
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
| Observed states and retained Patrol schedule | #140, `42b9f6e` | Compact collection-to-page path, scenario ledger and production reads |
| Reported schedule and live Observed header | #141, `27af3da` | Real normal samples: automatic/manual refresh without full reload |

PR #141 had a fresh current-head Codex +1 and green CI before merge/activation.
Reviewed head `ea8c667323c270fc6c29ba0101aa71859efc30ee` and installed squash release have
identical trees. [CI run 41252528727](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/41252528727)
passed 3151 default tests (one existing skip), 515 isolated safety cases and 232 Chromium
cases, with 100% statement/branch coverage. Automated hardware boundaries are mocked.

Activation finished `verified`. Post-install checks confirmed normal collection by the
new process, healthy application/database/Collector, preserved configuration/settings/policy,
no checked-process warning/error events or browser errors, and no horizontal overflow.
**Reported schedule** preserves the controller's value and timezone semantics without
promising a future start. **Observed** follows the saved sample both automatically and
through Refresh now. Tests used normal collector samples, no forced collection or writes.

The firmware's exact schedule-marker rollover rule after a natural Patrol completion
remains unverified. Do not advance dates by a guessed recurrence, change clocks/schedules,
or start/stop Patrol or CC to make the displayed date current. See
[the investigation and scenario ledger](ACTIONS_SCENARIOS.md).

Numeric CacheVault Learn Cycle recovery remains included. Its INFO event is not an
incident; unsupported malformed values still warn. See [the parsing contract](CACHEVAULT_LEARN_TIME.md).

## Telegram

Automatic Telegram delivery was enabled at verification. One neutral real test was
API-accepted on 2026-10-30 and receipt confirmed by the owner. This proves transport and
destination delivery, not a real hardware-fault drill. No repeat test was sent for these releases.

Hardware incidents retain confirmation, freshness, identity, deduplication and retry checks
during maintenance. Operator audit noise and arbitrary HTTP/UI exceptions are not Telegram
incidents. A stopped app cannot report its own outage. Independent availability/freshness
monitoring is still unverified. See [the delivery contract](TELEGRAM_ALERTS.md).

## Recovery after SSH disconnect or repeated activation

The installed #141 release used a legacy one-shot helper launched through systemd.
Its matching `/run/megaraid-ui-27af3da-*/status.json` finished `verified`; exact backup/status
paths are in the private release receipt. `/run` is ephemeral; a missing file after reboot
does not itself establish a failed deployment.

Legacy helpers expect one exact old baseline. Repeating a completed activation can
therefore refuse a baseline mismatch before changes. Do not remove the guard, activate
an old staged release or restore the live database after a terminal disconnect. Inspect:

```bash
systemctl show megaraid-dashboard.service -pActiveState -pSubState -pWorkingDirectory -pMainPID
curl --fail --silent --show-error http://127.0.0.1:8090/healthz
```

Future compatible application-only updates use [versioned deployment](VERSIONED_DEPLOYMENT.md):
detached execution, durable receipts, an already-installed result and guarded application
rollback. Code availability does not prove the root tool is installed or that a managed
production update ran. Keep isolated tooling evidence distinct from the installed #141
application. Neither rollback path restores/deletes live database history.

## Remaining work, in order

1. **Independent monitoring:** inspect the existing observer; add only missing availability
   and Collector-freshness coverage, with incident-only notification.
2. **Recurring backup/restore proof:** verify schedule, retention and off-host storage;
   restore a separate copy and check integrity/history/delivery state. Deployment backups
   do not establish recurring recovery readiness.
3. **Firmware observation:** inspect the next naturally completed Patrol cycle when
   diagnosing scheduling; do not force an operation for a checklist.

Documentation and versioned tooling are part of the [maintenance handoff](MAINTENANCE_HANDOFF.md).
Physical LED, buzzer, spin-down, replacement, foreign recovery and rebuild effects remain
unverified. Use a disposable testbed or genuine authorized maintenance, never a healthy-RAID fault.

## Documentation map

- [Roadmap](roadmap.md), [maintenance handoff](MAINTENANCE_HANDOFF.md).
- [Versioned deployment](VERSIONED_DEPLOYMENT.md), [runbook](RUNBOOK.md),
  [fresh installation](../INSTALL.md), [troubleshooting](../TROUBLESHOOTING.md).
- [Actions](ACTIONS_REFERENCE.md), [scenarios](ACTIONS_SCENARIOS.md),
  [readiness](OPERATOR_ACTION_READINESS.md), [safety](OPERATOR_SAFETY.md).
- [Full capability](STORCLI_FULL_ROLLOUT.md), [StorCLI compatibility](STORCLI_123_COMPATIBILITY.md),
  [GET-only smoke](PRODUCTION_READ_ONLY_SMOKE.md), [Learn Cycle](CACHEVAULT_LEARN_TIME.md).
- [Telegram](TELEGRAM_ALERTS.md), [Obsidian handoff](knowledge-vault-megaraid-update.md).

Older audits/inventories remain dated evidence. Verify live state before future changes;
historical instructions do not grant present deployment or controller authorization.
