# Obsidian maintenance handoff — 2026-11-08

Canonical source: [CURRENT_STATE.md](CURRENT_STATE.md). Installed `f9e736f` (#150)
includes #148 shared drive assessment, #149 critical/component/metric/freshness fixes
and #150 Healthy / Attention / Critical / Unknown presentation. The green 8 Healthy
count, fault counts and numerical error badges remain; firmware codes stay diagnostic.

Managed acceptance finished verified on 2026-11-07 at 22:38:55 UTC (23:38:55 Europe/Rome).
All eight cards, mapping/sort, normal Collector and automatic/manual Observed refresh
passed. Fresh exporter grades on loopback 8092 were optimal for 1 controller / 8 PD /
1 VD. Buzzer remains Enabled. #150 CI: 3411 default tests (one existing skip), 100%
coverage, 515 safety and 292 Chromium; two extra isolated badge/recovery cases passed.
No controller write or production fault drill. Read-only follow-up on 2026-11-08
confirmed the same healthy current release, fresh metrics and normal collection.

The owner confirmed all eight physical Locate LEDs on 2026-11-06. Buzzer setting/audit
round-trip acceptance passed separately that day; the later chosen Enabled state is
retained. Silence/acoustic output and a real production rollback remain unverified.
The owner authorized publication in project PR #147 and knowledge-vault PR #4 on
2026-11-08; merging and cross-device synchronization remain separate steps.

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
