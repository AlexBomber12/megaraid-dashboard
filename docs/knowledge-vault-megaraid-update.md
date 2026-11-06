# Obsidian maintenance handoff — 2026-11-06

Canonical source: [CURRENT_STATE.md](CURRENT_STATE.md). Installed application is
`8b60b72` (#146), including the earlier UI/schedule fixes, physical S0-S7 cage labels,
Drives sort/refresh preservation, buzzer aliases and managed installation permissions.
Managed activation finished verified on 2026-11-06; all eight physical Locate LEDs
were confirmed by the owner. Real Buzzer Disable -> On -> Disable setting/audit
acceptance passed on 2026-11-06; initial Disabled restored, maintenance closed.
Real rollback and acoustic alarm effects are not certified.
The owner requested a handoff and plan, without recurring automatic checks.

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
