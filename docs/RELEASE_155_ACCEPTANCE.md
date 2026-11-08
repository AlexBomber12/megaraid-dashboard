# Release #154–155 acceptance — 2026-11-08

## Installed application and review boundary

SERVER runs `8da1a9369342212767c49af190e2e513aae988d7` (#155), including merged
#154 / `970d16b` and earlier fixes. Managed activation from #153 / `211dfb2`
reached verified at **14:04:55 UTC / 15:04:55 Europe/Rome**. Reviewed head
`fe73a0218728224fd026af4b91c2de8ca3e5c9bb` and merged release have identical trees.
Fresh current-head Codex +1 had no findings; [CI 42485484927](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/42485484927)
passed CI, isolated safety and Chromium jobs before merge and activation.

The app/database/Collector are healthy, metrics on loopback 8092 cover 1 controller,
8 physical and 1 virtual drive, and settings/exact policy/protected configuration are
preserved. Initial acceptance checked one normal Collector completion; documentation
follow-up confirmed two, without new process warning/error events or traceback.
This is dated evidence; `/healthz` alone does not prove freshness or an external observer.

## #154 presentation changes

- Events uses Info/Warning/Critical words and icons, Info blue, readable category
  names and counts, and an Observed timestamp. Stored categories/query values and
  historical event text remain unchanged.
- Collections use quiet Healthy text; aggregate health counts and fault reasons remain.
  The bare per-tile numeric error badge is removed. Table/detail counters and the
  Error Counter History chart remain; the duplicate Error Trend sparkline and its
  misleading Shield/BBM caption are removed.
- Firmware values are visible technical fields or inline diagnostics instead of
  one-value disclosures. Missing technical fields share Not Reported; missing health
  evidence still means Unknown/uncertainty, not fabricated Healthy.
- Drive actions impossible for the current state share Other Actions. Authentication,
  CSRF, maintenance, identity, topology, exact-policy and all API refusal gates remain.

## #155 findings and fixes

| Scenario | Before | Accepted behavior and evidence |
| --- | --- | --- |
| A new event is excluded by category/severity/combined filters, or the list is empty | Filter chips/counts could remain stale until reload | Global category-count revision refreshes navigation independently of the filtered list cursor; isolated real-HTMX tests cover all four cases |
| A quiet poll has unchanged counts | Unnecessary navigation replacement could disturb selection/focus | Existing navigation node retained; cursor and selected query preserved; production quiet poll retains combined filters and an empty result |
| Warning/Critical thresholds are close near a plot edge | Text overlaps; this edge case already existed before #154 | Measured text boxes receive spacing and both plot bounds while threshold lines stay at actual values; 6 browser cases at 1440/390 px cover ROC 109/110 and drive 59/60, 21/22 |

The opaque filter revision is presentation metadata, not an authorization token.
Hidden events are not consumed by a filtered cursor. No stored event/delivery/audit
rows, schema, dependencies, auth/CSRF, rate limits or StorCLI contract/policy changed.

## Checks and production observations

- #155: 3430 default tests passed, one existing skip, **100% statement/branch coverage**;
  515 isolated safety and 315 Chromium tests passed. 60 focused checks also passed.
- #154's own CI passed 3411 default tests (one existing skip), 515 safety and 305
  Chromium. Independent review additionally ran 49 focused browser cases and isolated
  fault-view inspections. These counts describe different scopes, not additive coverage.
- Both real drive charts are painted with one heading each; 7d/30d drive and 1h/24h
  RoC selection work. Normal production threshold labels are readable; close configured
  thresholds and new event arrivals were exercised only with disposable test data.
- Automatic Drive Observed advanced **13:55:10 -> 14:04:50 UTC**, or **14:55 -> 15:04
  Europe/Rome**, without reload. Refresh now completed and read the same latest saved
  sample with both charts drawn; it did not force another hardware collection.
- Events retained its combined query, global counts and empty result while Live advanced
  **15:06:16 -> 15:07:17 Europe/Rome**. All Events restores the full existing list.
- Overview retained 8 Healthy and S0-S7 -> 252:4,5,6,7,0,1,2,3. Buzzer Enabled,
  maintenance closed; existing Patrol Running and CC Stopped were observed only.

No fake event, production threshold change, forced Collector run, controller write,
new Telegram test or physical failure was used. Detailed logs, receipts, serials and
screenshots remain in private ignored acceptance material. Earlier #153 7d/365d/30d,
reverse-sort and two-sample manual-refresh evidence stays historical, not a repeated
#155 drill. Physical LED acceptance belongs to the owner's earlier eight-cage check;
acoustic/Silence and destructive/recovery effects remain unverified.

## Transfer status

Project docs #147 and knowledge-vault #4 were merged on 2026-11-08. Their source
merge is completed; this follow-up updates current pointers for #154–155. Local
Obsidian edits, GitHub publication/merge and cross-device sync are distinct results.
Before final handoff, record independent app/Collector monitoring and recurring/off-host
backup plus separate-copy restore evidence, or explicit owner-accepted deferral, then
owner acceptance. [Current state](CURRENT_STATE.md), [roadmap](roadmap.md) and
[maintenance handoff](MAINTENANCE_HANDOFF.md) govern. No recurring automation is added.
