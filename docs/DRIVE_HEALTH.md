# Shared drive health assessment

Overview tiles, Drives rows, drive-detail health cards and position diagrams use
one presentation assessment. Firmware state, serial identity, S labels, controller
addresses and all command/action eligibility checks remain unchanged.

## Shared UI vocabulary

Every health badge uses **Healthy / Attention / Critical / Unknown**, including
controller summaries, drive counts, tiles, list rows, detail cards, VD health and
position tooltips. Unknown means no recognized assessment is available; an unknown
firmware drive state still produces Attention with its explicit reason under the
existing assessment rules. Missing temperature alone does not imply drive failure.

Online is an operational controller state, not a health verdict: an online drive
with SMART/predictive faults remains Critical. Healthy replaces Optimal in displayed
assessment badges. Internal severity keys, metrics and raw states are unchanged.
Since #154, raw drive state is a visible **Firmware State** field and VD state
is an inline technical value in RAID Configuration; drive tiles/list rows omit it.
Reported CacheVault state remains a technical field. One-value Controller details
disclosures are removed. Healthy spare/unconfigured drives keep an explicit **Role**
label, not another competing health grade. Health-column sorting uses the displayed
assessment; physical S ordering and real-address action targets are preserved.

This wording change shipped in [PR #150](https://github.com/AlexBomber12/megaraid-dashboard/pull/150),
installed as `f9e736f` and verified on 2026-11-07 at 22:38:55 UTC. The retained 8 Healthy
count and two isolated Attention/Critical/numerical-error/recovery cases passed.
Production reads checked normal collection, all eight cards and both Observed refresh
paths. [Current state](CURRENT_STATE.md) records the acceptance boundary.

| Observed evidence | Assessment | Explanation |
| --- | --- | --- |
| Failed, Offline/Offln/OfLn, Missing/Msng, UBad | Critical | Controller failure, offline, absent drive or controller-marked unusable |
| SMART alert or positive predictive-failure count, including with Onln | Critical | Explicit SMART alert or predictive count |
| Critical configured drive temperature | Critical | Critical temperature |
| Rebuild, JBOD or unrecognized firmware state | Attention | Rebuild in progress, outside RAID or unknown raw state |
| Elevated configured temperature | Attention | Elevated temperature |
| Historical media/other error count without a stronger signal | Attention | Historical count; no automatic failed-drive/replacement verdict |
| UGood without reported issues | Healthy | Role: Unconfigured; raw UGood in details; no surface scan or compatibility certification |
| DHS/GHS without reported issues | Healthy | Role: Hot spare; raw role in controller details |
| Online without reported issues | Healthy | Raw controller state remains visible |

Critical signals take precedence over warnings; all reported reasons stay visible.
Onln does not override SMART/predictive alerts. Both Overview and Controller summary
aggregation include these drive faults, counters and configured drive temperature
grades. Missing temperature stays explicitly unknown and does not invent a
physical failure. Unrecognized state spellings are warnings.

This changes presentation only: event severity, Telegram delivery, persistence,
wrapper/schema/grants, maintenance/destructive gates and replacement eligibility
are unchanged. No automatic controller operation follows a health badge.
Healthy describes reported evidence, not guaranteed absence of defects or RAID compatibility.

The follow-up [critical scenario audit](CRITICAL_SCENARIOS_AUDIT.md) separately
corrects new UBad event severity/delivery, CacheVault/controller aggregate evidence
and collection-freshness notices. It does not alter command eligibility or raw states.

## Verification

- `tests/test_services/test_drive_health.py`: state aliases, SMART/predictive
  precedence, historical counters, temperature, mixed faults and identical saved
  assessments across Overview, Drives, detail and the selected backplane slot.
- `tests/e2e/test_drive_health.py`: isolated persisted samples for Onln with SMART
  or predictive alert, UBad, historical media errors, UGood, elevated/critical
  temperature and unknown firmware state at 1440/1024/390 pixels. Refresh/navigation
  recovery returns all surfaces to Healthy. S0 retains the actual 252:4 target;
  fake StorCLI receives no calls.
- Existing safety suites remain required. No production fault or hardware mutation
  is used for acceptance. Deployment requires separate authorization.

## Design acceptance through #153 — 2026-11-08

#151 preserves warning/critical field colours and wraps fault reasons without page
overflow. #152 shares neutral Unknown presentation/counts and normal-temperature styling;
unrecognized firmware drive states remain Attention. #153 closes the subsequent
chart-swap/Drive Observed findings. Fault colours, numeric badges and recovery cases
passed in isolated snapshots at 1440/1024/390 px; healthy RAID was not faulted.
Historical #153 release checks were 3416 default tests (one existing skip), 100% statement/branch
coverage, 515 safety and 305 Chromium cases. These extend, not replace, the historical
incident and physical-acceptance limits above. [Current evidence](CURRENT_STATE.md).

## Current presentation and acceptance through #155

#154 replaces duplicate disclosures with visible technical fields, quiet collection
health text and one Not Reported list per technical card. The aggregate health counts,
fault reasons, table/detail fault counters and Error Counter History remain. The bare
per-tile number and duplicate sparkline are removed; health/detection/notification
semantics are unchanged. #155 fixes filtered Events navigation and threshold-label
overlap, with 3430 default tests (one existing skip), 100% statement/branch coverage,
515 safety and 315 Chromium. [Current acceptance](RELEASE_155_ACCEPTANCE.md) separates
isolated fault/arrival/extreme-threshold cases from normal read-only production checks.
