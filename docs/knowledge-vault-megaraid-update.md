# Obsidian maintenance handoff — 2026-11-08

Canonical source: [CURRENT_STATE.md](CURRENT_STATE.md). Installed `8da1a93` (#155)
includes #154 noise reduction, live filtered Events category/count revision and
measured non-overlapping threshold labels. [Acceptance](RELEASE_155_ACCEPTANCE.md)
records exact historical and current boundaries. Health remains Healthy / Attention /
Critical / Unknown. Aggregate counts, fault reasons, table/detail counters and Error
Counter History remain; the bare tile number and duplicate sparkline are removed.

Managed verification: 2026-11-08 14:04:55 UTC (15:04:55 Europe/Rome), from `211dfb2`.
Initial acceptance saw one normal Collector completion; documentation follow-up saw two.
Health/metrics/settings/policy/configuration and process warning/error/traceback checks
passed. Drive Observed advanced automatically 14:55 -> 15:04 Europe/Rome; completed
manual refresh read the same latest saved sample. Drawn charts/ranges and Events
combined/empty filters with quiet polling passed. Excluded new events and close
thresholds were tested only with disposable data. No production injections or RAID writes.

Latest release evidence: 3430 default tests (one existing skip), 100% statement/branch
coverage, 515 safety and 315 Chromium; fresh +1 and all three CI jobs green. Historical
#151-153 chart/Observed and physical mapping/sort evidence remains dated.

All eight physical Locate LEDs are owner-confirmed. Buzzer setting/audit round trip
passed previously; owner-selected Enabled and closed maintenance are retained.
Silence/acoustic output and a real production rollback remain unverified.
Project documentation #147 and knowledge-vault #4 were merged on 2026-11-08.
They are no longer pending. This follow-up's publication/merge, local note update
and cross-device sync are distinct results; no cross-device sync is claimed.

Maintain these `AlexBomber12/knowledge-vault` notes together:

- `10-projects/megaraid-dashboard/megaraid-dashboard-overview.md`: current release,
  verification boundaries, maintenance stage, remaining work and source links.
- `20-homelab/notifications/megaraid.md`: incident contract, diagnosis, legacy versus
  versioned rollout recovery and healthy-RAID restrictions.
- `20-homelab/notifications/README.md`: only the MegaRAID row/date, preserving other
  services' history and notification policy.
- `20-homelab/homelab-restore-map-20260504.md`: update only the dated MegaRAID addendum.
- `20-homelab/infrastructure-inspector-maintenance-policy.md`: update only its MegaRAID
  addendum; unrelated local maintenance observations remain untouched.

Link [versioned deployment](VERSIONED_DEPLOYMENT.md) and
[maintenance handoff](MAINTENANCE_HANDOFF.md). Root tool installation/adoption,
managed application rollout and isolated acceptance are distinct evidence. Do not claim
a production rollback drill merely because fake-service tests passed.

One neutral real Telegram delivery was accepted/confirmed on 2026-10-30. No repeated
test is needed for documentation. Real hardware incidents remain eligible during
maintenance, operator audit noise is ineligible, and alert text has no buttons/runbook links.
Independent app/Collector monitoring and recurring backup/restore proof remain open.

Keep credentials and detailed production telemetry in private recovery material. Preserve
local frontmatter/mirror sync fields, historical journals and unrelated edits; never edit
the generated `mirror/` tree. Use a documentation PR for repository publication. A local
edit does not prove merge or synchronization to another machine. Confirm local target
hashes immediately before writing and retain private backups of the previous notes.
