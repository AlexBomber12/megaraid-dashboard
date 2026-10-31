# Obsidian documentation handoff — 2026-10-31

Canonical production evidence: [CURRENT_STATE.md](CURRENT_STATE.md).
The installed release is `cfad744` (#137); #134 safety, #135 compact design and #136
status corrections are included. The earlier proposal against the 2026-10-26
knowledge-vault blob is superseded by this checkpoint.

Related notes in `AlexBomber12/knowledge-vault` / Obsidian:

- `10-projects/megaraid-dashboard/megaraid-dashboard-overview.md`: purpose, architecture,
  delivered state, priorities, decisions and verification limits.
- `20-homelab/notifications/megaraid.md`: current deployment, Telegram contract,
  neutral-delivery evidence, safe diagnostics and rollback boundaries.
- `20-homelab/notifications/README.md`: MegaRAID row reconciled without changing
  other services' historical observations or notification policy.
- `20-homelab/homelab-restore-map-20260504.md`: dated MegaRAID inventory addendum.
- `20-homelab/infrastructure-inspector-maintenance-policy.md`: dated MegaRAID addendum;
  older notifier plans are historical and independent host monitoring remains unproven.

Maintain those notes together; use the documentation PRs as publication evidence.
Editing a local note does not prove GitHub merge or synchronization to another machine.
Preserve existing local frontmatter/sync metadata and unrelated edits. Do not edit
the generated `mirror/` tree or copy credential notes into project documentation.

Telegram's neutral test was API-accepted on 2026-10-30 22:11:08 UTC and the owner
confirmed receipt. Exactly one message was sent. It did not create an incident or
exercise real hardware failure. Current alerts use short plain Russian text without
links/buttons; details remain in Events and independent `event_deliveries` state.
Hardware incidents are not suppressed by maintenance; operator audit noise is ineligible.

The #137 deployment completed `verified`; its subsequent baseline-mismatch refusal
was a repeated one-shot invocation, not a failed initial deployment. Do not repeat
activation or change RAID merely because SSH disconnected. Backup/restore verification,
independent app/Collector monitoring and durable deployment tooling remain next work.
