# Critical alerts and operator scenarios

Audit date: 2026-11-07. Pre-update production baseline: #148 / `510ce9b`.
Corrections shipped in [PR #149](https://github.com/AlexBomber12/megaraid-dashboard/pull/149),
installed as `9f2693eb3000cc908cb1393f127fe98730ec8abc`. Fault scenarios use
isolated saved snapshots, fake StorCLI and mocked Telegram/SMTP. Deployment acceptance
used normal collection and production reads only; no controller operation or real
message manufactured a fault.

## Corrections found by following the user journey

| Trigger | Previous behavior | Delivered behavior |
| --- | --- | --- |
| UBad on the first poll or after a state change | Critical in the UI, warning event excluded from Telegram | Critical event, explicit UBad Telegram wording, normal relevance/dedup gates |
| First reliable poll confirms CacheVault absence | No baseline absence event | Critical absence event; unknown or failed probes still do not prove absence |
| CacheVault fails, requires replacement or disappears | Overall summary could remain Optimal | Both summaries and controller-health metric include the explicit fault |
| RoC crosses configured warning/critical threshold | Controller and Overview could disagree; exporter lacked configured grades | The same configured grade reaches both pages and the production exporter |
| Only a disk/sensor/cache component needs attention | Controller text claimed RAID was degraded | Component reason and actual VD state; no invented degradation |
| No physical or virtual inventory is reported | Could say Optimal, including “0/0 drives … healthy” | Critical summary; explicit lack of virtual drives |
| Assigned spare is observed as DHS/GHS | Unknown-state warning | Hot spare, with unchanged raw state; SMART/predictive/temperature faults still win |
| PD/VD state metrics and partially-degraded spelling | Spares were critical, Missing/degraded were warning; hyphenated VD state missed critical classification | Normalize semantic aliases; spare 0, Missing/degraded 2, unknown 1; no raw firmware value changes |
| Collector fails or saved data gets old | Healthy last-known sample lacked a prominent monitoring warning | Shared live warning distinguishes last-known health from current reliable observation |

Existing events are not rewritten and old warning rows are not retrospectively
promoted. Transport, channel configuration, the migration cutoff, authentication,
CSRF, command policy and destructive/maintenance gates are unchanged.

The StorCLI semantic-normalization contract gains the `Partially-Degraded` alias;
raw JSON/state values, invocation/command validation and privileges are unchanged.
PD/VD state metric names and 0/1/2 meanings stay stable, with corrected assignment:
degraded/missing are critical, healthy spares are optimal, unrecognized states warn.

## Scenario ledger

The 17-case chain runs each observation on a first poll, a transition from a healthy
baseline and during active maintenance: 51 cases. It uses the real detector, event
store, freshness/relevance logic, formatter and delivery bookkeeping with a fake
sender. It also compares Overview, Controller and the configured controller-health
metric, then proves a second cycle does not send the incident again.

| Scenario | Expected outcome | Executable evidence |
| --- | --- | --- |
| VD degraded / offline | Critical summary and appropriate immediate Telegram text | `test_critical_user_scenarios`; `test_telegram_messages` |
| PD Failed / Missing / UBad | Critical event; current matching identity required for delivery | Same chain; `test_telegram_relevance` |
| Online drive raises SMART / predictive flag | Critical despite raw Onln; reason retained | Same chain; `test_drive_health`; browser drive-health cases |
| Historical media counter exists/increases | UI Attention, actionable media event; no automatic failed-drive verdict | Same chain; counter/identity/dedup suites |
| Disk critical temperature | Critical immediately; persisted identity and hysteresis respected | Same chain; relevance and detector temperature suites |
| Controller warning / critical temperature | Matching page/exporter grade; warning waits for confirmation | Same chain; controller episode/relevance suites |
| CacheVault failed / replacement required / absent | Critical while volume may remain Optl; appropriate message | Same chain; component browser cases |
| CacheVault capacitance below configured threshold | Warning in summaries; does not broaden Telegram policy | Same chain; configured component tests |
| Healthy DHS / GHS after Hot Spare | Hot spare; no false unknown-state alarm | Shared drive-health and browser drive-health tests |
| Explicitly empty complete inventory | No healthy-volume claim; ambiguous/partial inventory is rejected separately | `test_overview_controller_health`; empty-inventory browser case |
| Unknown/contradictory legacy CV presence | Unverified/conflicting warning; never fabricate absence | `test_controller_health`; existing collector/relevance tests |
| Custom RoC/capacitance thresholds | Effective configuration wins over defaults | Component, metrics HTTP and browser cases |
| Metric severity and firmware aliases | PD/VD numeric grades agree with actionable severity; a hyphenated degraded VD reaches Telegram and both pages | HTTP metric tests; hyphenated-state chain/browser cases |
| No reliable sample, stale sample or failed poll | Visible notice on Overview/Controller/Drives/detail; clears after a reliable sample | `test_observation_health` service/browser suites |
| Failed/incomplete inventory or optional CV probe | Whole poll rejected; no false disappearance or recovery | Collector/parser/scheduler regression suites |
| Monitoring repeatedly fails | Confirmed outage only after count/time gates; later success ends it | Scheduler and Telegram relevance suites |
| Hardware incident during maintenance | Normal delivery/retry/confirmation/dedup remain active | Chain maintenance phase; notifier maintenance regressions |
| Operator audit warning/critical noise | No Telegram incident | Notifier policy and operator-noise regressions |
| Warning clears/escalates before confirmation | Original warning is not sent | `test_telegram_relevance`; controller episode suite |
| Same incident repeats / recurs / worsens | Dedup repeats; recovery or worsening permits a new incident | Notifier/relevance/controller episode suites |
| Disk replaced at same controller slot | Old queued incident becomes obsolete; new identity is independent | Replacement identity and retained-serial regressions |
| Stale data or missing sensor/identity | Wait without spending a send attempt | Telegram relevance suite |
| Telegram timeout/network/API/429 | Durable bounded retry/cooldown; no automatic controller action | Transport/notifier/cooldown suites |
| Telegram fails while SMTP works, or inverse | Independent channel progress and markers | Notifier concurrency and channel-isolation suites |
| Interrupted delivery, future event, backlog | Revalidate/requeue; bounded batches; no starvation by ineligible warnings | Notifier/relevance/concurrency suites |
| Read-only / capability none / absent exact policy | Block write before controller execution | Operator safety integration and policy tests |
| Missing authentication / CSRF / expired maintenance | Deny with no mutation; only Locate Stop retains its documented exception | Full action safety matrix |
| Wrong serial / changed drive/controller identity | Refuse stale target; raw 252:X remains authoritative despite S mapping | Operator chain and policy/identity regressions |
| Maintenance/policy changes during preflight | Recheck at dispatch and cancel write | `test_operator_chain_audit` |
| Locate Start/Stop | Gate Start; recovery Stop allowed only under its exact exception; physical state unverified | Safety matrix; Locate browser cases |
| Buzzer Enable/Disable/Silence | Explicit Success/audit; aliases agree; no claim of audible sound | Buzzer unit, safety and browser cases |
| CC and Patrol Start/Stop/mode | Live state checked, selected mode reflects observation, unknown schedule stays unknown | Safety matrix; action scenario browser cases |
| Mark Good / Spin Down / Assign Hot Spare | Appropriate unconfigured state, identity and exact authorization required | Advanced-action unit/browser/safety cases |
| Mark Bad | Removed/unavailable; crafted request cannot issue an unapproved command | Control inventory and safety matrix |
| Replace Offline/Missing/Insert | Destructive gate, typed serial, explicit topology and confirmed preceding step | Replacement unit, operator chain and browser cases |
| Foreign Import/Clear | Confirm current digest/membership; prove no rebuild; repeat evidence before dispatch | Foreign prewrite, operator chain and safety suites |
| Firmware Failure/unknown/malformed/wrong-controller/timeout | Never report successful mutation or fabricate a saved hardware state | Full fault/result matrix |
| Audit fails after dispatch or response is lost | Uncertain result; do not authorize next step or automatically retry | Safety matrix; operator chain/browser uncertainty cases |
| Double submit and live refresh during action | One dispatch; preserve row/identity; wait for a newer saved observation | `test_actions_scenarios`; operator chain browser suite |

Test references without paths live in `tests/test_services/`; browser suites live
in `tests/e2e/`. Operator integration is `tests/integration/test_operator_safety.py`.
The action catalog in `tests/safety/scenarios.py` and UI control inventory prevent
silently omitting an exposed action from the safety matrix.

## Verification and limits

Required commands (results belong to the final reviewed commit):

```bash
PYTEST_ADDOPTS='-n 4' bash scripts/ci.sh
pytest -o addopts= tests/e2e/ --browser chromium --no-cov -q
```

Keep 100% statement/branch coverage, strict mypy and Ruff gates. Ignored audit logs
and screenshots are under `artifacts/critical-*`. Screenshots are synthetic faults,
not SERVER incidents. This ledger is software evidence, not physical RAID recovery
certification or proof of Telegram receipt during a genuine hardware incident.

Final local acceptance passed 3404 default tests (one existing skip), 515 isolated
safety cases and 291 Chromium cases. Ruff, formatting, strict mypy and shell checks
passed; statement/branch coverage stays at 100%.
The catalog contains all 20 action types, including the unavailable Mark Bad path.
Fresh current-head Codex +1 and all three green GitHub checks passed before merge
([run 42316293906](https://github.com/AlexBomber12/megaraid-dashboard/actions/runs/42316293906)).
Reviewed `bf34ee922a418d9643a06c9f8a02273a6034da55` and squash-release trees match.

## Production acceptance — 2026-11-07

Managed activation finished `verified` at 16:55:45 UTC after a new normal sample.
Application/database/Collector, preserved settings/policy/configuration, eight healthy
drive details, horizontal physical mapping, retained Drives sort and automatic/manual
Observed refresh passed. Loopback 8092 exporter reported optimal 1 controller / 8 PD /
1 VD and a fresh Collector timestamp. The checked process journal contained one
normal completion, no warning/error or traceback; browser warnings/errors were absent.
Buzzer stayed Enabled. Existing running Consistency Check was observed only.
No forced poll, controller write, Telegram test or real fault drill was performed.
Private receipts and actual SERVER screenshots are in ignored deployment-9f2693e artifacts.

The shared health-vocabulary follow-up shipped as #150 / `f9e736f` and was verified
on 2026-11-07 at 22:38:55 UTC. Healthy labels, retained counters, normal Collector,
mapping/sort and Observed refresh passed. Two extra isolated cases preserved
Attention/Critical and numerical error badges plus recovery; no production fault.
#150 CI passed 3411 default tests (one existing skip), 100% coverage, 515 safety and
292 browser cases. The earlier #149 audit numbers above remain historical evidence.

The owner already confirmed physical identification for all eight LEDs. Buzzer's
Enabled setting is verified separately from audible output. No array degradation,
drive removal, media failure, spin-down, foreign recovery or rebuild is induced on
the healthy production array. Those physical paths require a disposable testbed or
genuine authorized maintenance. A stopped application cannot send its own outage
alert; independent availability monitoring remains a separate operational item.

`/healthz` reports application/database/collector ownership, not snapshot freshness.
The page notice and Telegram relevance explicitly check saved evidence. No recurring
automation or new monitoring service is introduced by this audit.

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
