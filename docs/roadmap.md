# MegaRAID Dashboard — current roadmap

Updated: **2026-10-31**. Installed application: `cfad744` on SERVER.
[CURRENT_STATE.md](CURRENT_STATE.md) is the evidence-backed production checkpoint.
This roadmap is a plan, not authorization to mutate a controller or deploy a change.

## Delivered

- JSON-only typed StorCLI boundary, SQLite/Alembic, scheduled collection and event history.
- Email and independent Telegram incident delivery, persistence, freshness/identity
  checks, confirmation, deduplication and bounded retries. Neutral real delivery is proven;
  real hardware-incident delivery was not induced for testing.
- HTTPS/authentication, CSRF, maintenance/destructive gates, exact approved command policy,
  serial/topology checks and durable operator audit.
- Controller/drive/foreign workflows, replacement/rebuild monitoring and read-only smoke
  procedures. Physical effects remain unverified on the healthy RAID; Mark UBad unavailable.
- Compact Actions (#135), corrected position/Collector/schedule displays (#136), and
  numeric CacheVault next-learn recovery (#137), all included in the deployed release.
- Install/runbook/architecture/troubleshooting guides, security audit reports, metrics
  exporter and repository contribution/CI tooling exist. Their existence does not prove
  every operational procedure or external monitoring stack is deployed.

## Next work, in order

| Priority | Work | Completion evidence still required |
| --- | --- | --- |
| 1 | Inspect independent availability and Collector-freshness monitoring; implement only missing coverage | Observer independent of the failed app; incident-only delivery and failure/recovery tests without RAID faults |
| 2 | Verify recurring backups, retention and a separate-copy restore | Recent scheduled backup, SQLite integrity and history/delivery sanity checks; live DB untouched |
| 3 | Version deployment tooling with durable status and an already-installed result | Disconnect/retry/rollback tested in isolation; exact baseline and approval checks retained |
| 4 | Maintain project/Obsidian documentation | Matching dated release, live-vs-mocked evidence, unresolved limits and recovery paths |

Documentation reconciliation is the current task. It does not itself perform items 1–3,
enable observers, restore databases, send Telegram or redeploy the application.

## Deferred / evidence-dependent

- Real LED, buzzer, spin-down, replacement, foreign recovery and rebuild validation needs
  a disposable testbed or genuine authorized maintenance. Never create a fault on the healthy RAID.
- New firmware/StorCLI response shapes need redacted real JSON and regression tests.
  Keep the pinned 1.23.02 selection; the newer binary's patrol incompatibility is documented.
- A fresh periodic security review, restore/fresh-install exercise and release tagging
  are useful operational work; prior audits are dated, not continuous certification.
- A separate Prometheus/Grafana deployment, hardware cooling changes, new scheduling UI
  or broad visual redesign needs its own confirmed scope. These are not known software bugs.
- Multi-controller, non-MegaRAID, SAS expanders, ZFS/mdraid and mobile-first UI stay out of scope.

## Release acceptance

Changes go through PRs to main, local required checks, green CI and current-head review
before activation. Preserve secrets, effective settings, pinned binary and root-reviewed
exact commands. Verify health and a new process's normal collection after activation;
use GET-only UI checks where relevant. Keep a backup and a reviewed rollback path.
Report software proof, production read observations and untested physical effects separately.

The May 2026 PR-by-PR proposal is retained in Git history
([pre-update roadmap](https://github.com/AlexBomber12/megaraid-dashboard/blob/cfad744a5867c5009bfc06a9804cceaeac49be82/docs/roadmap.md)).
Its old counts, example URL, default account, maintenance alert suppression and
unimplemented labels are not the current work queue or security policy.
