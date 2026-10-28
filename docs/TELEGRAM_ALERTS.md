# Concise Telegram catalog and delivery audit

Implementation base: `6385bfbb7af81da564fcef8c97988cf665ab8a31`.
Evidence is repository code, redacted storcli fixtures and mocked pipeline tests.
This PR does not inspect production hardware, change configuration, enable automatic
Telegram delivery, send a live test, merge or deploy anything.

## Detection to delivery

1. `services/event_detector.py::detect` compares saved/current structured snapshots.
   Baseline/new VD/PD/CacheVault discoveries emit unhealthy-state/counter/flag events;
   disk temperature uses persisted identity and hysteresis. RoC temperature is checked
   on the first usable reading too. Confirmed disappearance emits critical VD/PD state
   events; reappearance records info presence evidence for recovery and recurrence.
   Replacements are info events plus any new disk incidents.
2. `services/scheduler.py::CollectorService.run_once` saves snapshots, RoC clear evidence,
   events and temperature states atomically. `_record_collection_failure` records warnings;
   `_monitoring_stale_summary` requires >=3 failed polls and the configured stale interval
   before the explicit `monitoring_outage=true` critical event. Success records recovery.
   Incomplete/uncertain inventories and CV probes reject the poll before persistence,
   leaving the last reliable snapshot intact; they never count as successful monitoring.
3. `db/dao.py::record_event` preserves hardware events regardless of maintenance.
   Migration `0009` still permanently excludes pre-migration warning/critical events.
   Only old `skipped/maintenance` rows are reconsidered through current relevance;
   other terminal skips, sent rows and pre-migration exclusions remain intact.
4. The scheduler's optional Telegram phase precedes independent SMTP.
   `services/telegram_notifier.py::_load_candidates` filters complete eligibility before
   LIMIT 100; terminal deliveries and future events/due times are excluded.
5. The cycle checks warning confirmation, current relevance and deduplication
   before claiming. `services/telegram_relevance.py` uses saved fresh readings and disk
   identity; missing evidence waits, positive recovery/replacement evidence skips.
6. Only then `services/telegram_messages.py::build_telegram_text` renders content.
   `incident_state`, `incident_key` and relevance helpers compare normalized semantic
   states across aliases. Text, hostname and summary are
   not dedup keys. Presence recovery evidence separates disappearance episodes.
7. `alerts/telegram.py::TelegramTransport.send` delegates to `send_async`: both post
   literal text with no buttons, runbook links, reply_markup, parse_mode or callbacks. The CLI
   `alerts/__main__.py::_handle_telegram_test` uses an independent neutral formatter.

All source paths above are relative to `src/megaraid_dashboard/`.

## Exact message matrix

Every real alert row is conditional on eligibility, active relevance and all gates below.
Temperature warning rows send only after confirmation. `\n` means one newline;
counts include newlines and the footer. SERVER previews are exact; for AI-SERVER replace
only `SERVER` with `AI-SERVER` (three additional characters), also tested verbatim.
The synthetic date/ID in this table illustrates existing event identity, not a real event.

| Condition | Outcome | Exact SERVER preview | Characters SERVER / AI-SERVER | Evidence |
| --- | --- | --- | --- | --- |
| VD Dgrd / Degraded | Send | `🔴 SERVER: сбой массива, риск потери данных.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 97 / 100 | M; N/R |
| VD Pdgd / Partially Degraded | Send | `🔴 SERVER: сбой массива, риск потери данных.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 97 / 100 | M; N/R |
| VD OfLn / Offln / Offline | Send | `🔴 SERVER: массив недоступен.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 82 / 85 | M; N/R |
| VD Failed | Send | `🔴 SERVER: массив недоступен.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 82 / 85 | M; N/R |
| VD entirely absent from complete inventory | Send: same unavailable-array text as VD Offline | `🔴 SERVER: массив недоступен.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 82 / 85 | P; D |
| PD Failed | Send | `🔴 SERVER: отказ диска.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 76 / 79 | M; N/R |
| PD Missing / Msng (entry present or confirmed disappearance) | Send | `🔴 SERVER: диск не обнаружен.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 82 / 85 | M; N/R |
| PD Offline / Offln | Send | `🔴 SERVER: диск в состоянии «офлайн».\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 90 / 93 | M; N/R |
| Positive/increasing media errors | Send | `🔴 SERVER: ошибки носителя диска.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 86 / 89 | M; N/R |
| Positive predictive-failure count | Send | `🔴 SERVER: диск сообщает о вероятном отказе.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 97 / 100 | M; N/R |
| SMART true | Send | `🔴 SERVER: предупреждение самодиагностики диска.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 101 / 104 | M; N/R |
| CacheVault present=false | Send | `🔴 SERVER: защита кэша не обнаружена.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 73 / 76 | M; N/R |
| CacheVault replacement_required=true | Send | `🔴 SERVER: защита кэша требует замены.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 74 / 77 | M; N/R |
| CacheVault Degraded / Dgrd / Failed / Unhealthy / Faulty | Send | `🔴 SERVER: сбой защиты кэша.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 64 / 67 | M; N/R |
| Disk temperature warning, confirmed | Defer → send at confirmation | `🟠 SERVER: перегрев диска: 57 °C.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 69 / 72 | M; N/R |
| Disk temperature critical | Send | `🔴 SERVER: критическая температура диска: 65 °C.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 84 / 87 | M; N/R |
| Controller temperature warning, confirmed | Defer → send at confirmation | `🟠 SERVER: перегрев контроллера: 96 °C.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 75 / 78 | M; N/R |
| Controller temperature critical | Send | `🔴 SERVER: критическая температура контроллера: 106 °C.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 91 / 94 | M; N/R |
| Explicit confirmed monitoring outage | Send | `🔴 SERVER: мониторинг недоступен, состояние RAID неизвестно.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 96 / 99 | M; N/R |
| Eligible incident with unknown metadata (e.g. CV Unknown) | Send | `🔴 SERVER: событие RAID, данные неполны.\nПерешли в ChatGPT.\n26.10 10:57 · №42` | 76 / 79 | M; N/R |
| Manual CLI test | Send once explicitly | `🧪 SERVER: тест уведомлений\nДействий не требуется.` | 49 / 52 | M; T; CLI |

Known aliases, casing and surrounding whitespace are normalized at classification and
comparison boundaries: detection, relevance, incident identity and formatting agree.
Optimal/Optl are healthy; Missing/Msng and Offline/Offln identify the same PD incident.
Related VD aliases also share incident identity. Unknown values never introduce a state,
bay, count or measurement from free text. Temperature
rendering accepts only integral non-bool 0–200 °C metadata. The alert's recorded occurrence
measurement is shown, not a fabricated latest reading. Missing/invalid event measurements
use the fallback; a missing current sensor still defers dispatch. Footer tests cover
Rome summer/winter time, naive stored UTC, and full 64-bit IDs. The longest catalog preview
is 104 characters; the 160-character contract leaves room for real event IDs.

## Suppress, defer and retry matrix

`—` is no outbound message; character count is 0. References name existing regression
suites; P exercises collection through the real bounded transport using mocked HTTP.

| Condition | Outcome | Preview | Characters | Evidence |
| --- | --- | --- | --- | --- |
| Telegram disabled | No Telegram phase; SMTP continues | — | 0 | S; C |
| Notifier process lock held | Defer entire cycle, no overlapping send | — | 0 | S |
| Invalid configuration | Fail closed; leave pending, SMTP continues | — | 0 | N; S |
| Recovery/info, healthy status, successful polling | Suppress; no eligible event | — | 0 | D; N; S |
| Isolated failed collection / fewer than 3 failures / outage shorter than stale threshold | Suppress | — | 0 | S |
| Unrelated critical system event or non-boolean outage marker | Suppress before LIMIT | — | 0 | R policy/query tests |
| Other-error counters, capacitance warnings, unknown VD warnings, disk-space, unsupported categories | Suppress before LIMIT | — | 0 | D; N; R |
| Routine replacement, rebuild, buzzer, foreign config and other operation events | Suppress info/unsupported categories | — | 0 | D; N; R |
| Born/pending during maintenance; legacy maintenance skip | Normal relevance, confirmation, dedup and retry; maintenance does not suppress real incidents | Catalog when active | Catalog | N maintenance/retry tests; A |
| Existed before migration 0009 | Permanently skip pre_migration | — | 0 | A |
| Already sent/skipped, event in future, retry not due | Exclude from candidates | — | 0 | N; R |
| Warning before confirmation deadline (default 15 min) | Defer awaiting_confirmation; no attempt | — | 0 | N; R |
| Repeated same warning / controller oscillation in hysteresis band | Preserve first confirmation deadline; dedup after send | — | 0 | R; E |
| Warning cleared or escalated before dispatch | Skip not_confirmed / obsolete | — | 0 | N; R |
| Missing/stale snapshot (older than max(60 s, 2 × collection interval)) | Defer awaiting_fresh_data; no attempt | — | 0 | R |
| Missing current sensor | Defer awaiting_temperature; no attempt | — | 0 | R; E |
| Unknown drive identity or malformed drive subject | Defer awaiting_drive_identity; no attempt | — | 0 | R |
| Missing VD/counter incident state | Defer awaiting_incident_state; no attempt | — | 0 | R |
| Replaced/recovered object; changed VD/CV/PD state; SMART cleared; counter below event value | Skip obsolete | — | 0 | R |
| Temperature recovered: disk strictly below clear boundary, controller at/below boundary | Skip obsolete | — | 0 | R; E |
| Reliable successful snapshot at/after outage | Skip outage obsolete | — | 0 | R; S |
| Same incident delivered within 24 h; increasing counter without recovery | Skip duplicate | — | 0 | N; R |
| Worsening state, different CV dimension, replacement identity, recurrence after recovery/flag rising edge, repeat beyond 24 h | Send new eligible active incident, catalog wording | Catalog | Catalog | N; R; E |
| Cooldown active, five sends reached, or earlier transport failure this cycle | Defer channel_deferred, no attempt | — | 0 | N; R; K |
| 100 candidates reached | Remaining candidates wait for later cycle | — | 0 | R |
| HTTP/network/timeout/API failure (including 401/403) | Durable retry, backoff 1/2/4…60 min; stop further sends this cycle | — | 0 accepted locally | N; T |
| HTTP 429 (even unreadable body) | Durable per-event retry plus shared cooldown; positive hint capped 3600 s, invalid hint 60 s | — | 0 accepted locally | T; K; R |
| Old oversized persisted attempted retry/cooldown | Repair once to <=1 h; preserve other channels/confirmation/terminal state | — | 0 | K |
| Interrupted sending or legacy failed row | Requeue, revalidate, retry; remote acceptance before crash can duplicate | Catalog on retry | Catalog | N; R |
| Active risk older than 3 h / >=8 failed attempts | Keep bounded retries; log prolonged failure from attempt 8; no lifetime cutoff | Catalog when accepted | Catalog | N; R |
| Complete inventory confirms absent PD/VD (even both lists empty) | Keep disappearance relevant; retain previous PD serial | Catalog | Catalog | P |
| Queued PD Missing / VD Offline before disappearance | Retain while absent; dedup matching disappearance; recovery/replacement skips | Catalog | Catalog | P; R |
| Other queued disk events after disappearance | Skip obsolete; fresh disappearance event covers absence | — | 0 | P; R |
| Failed, partial, unsupported or inconsistent inventory; CV probe error | Record collection warning, retain reliable baseline, defer hardware delivery; confirmed-outage policy applies | — | 0 until confirmed outage | P; S |
| Controller present flag with missing CV detail in legacy data | Defer awaiting_incident_state; never infer CV absence | — | 0 | R |
| Missing data / transport failure indefinitely | Remain pending; delivery not guaranteed | — | 0 accepted locally | R; N |

A failed request can already have been accepted remotely; the table does not claim
exactly-once receipt or that zero remotely received messages can be proved on timeout.
Claim and outcome are committed separately. Database locks are released during HTTP;
collection, retention and disk checks continue, and later candidates recheck time/data. The process notifier lock spans both channels. SMTP markers and policy stay
independent; each phase runs even when the other fails. Response handling retains the
10-second absolute deadline, 64 KiB cap, compression/redirect restrictions and token
redaction. These delivery controls are preserved.

## Evidence for absence and durable uncertainty

The collector admits **only reliable whole polls** to the snapshot tables:

- All three inventory responses must explicitly succeed for a single controller 0.
  Process exit success alone, missing/unknown status or a firmware Failure is insufficient.
- `/c0 show all J` must provide integer `Physical Drives` and `Virtual Drives` counts.
  Its PD/VD identity and semantic-state lists must match the fully parsed detail responses
  from `/c0/eall/sall show all J` and `/c0/vall show all J`, with unique slots/VD IDs and
  nonempty PD serials. A malformed or missing detail block rejects the poll.
- Explicit zero controller counts plus empty detail responses prove empty inventories;
  empty tables may be omitted only when the corroborating count is zero. Losing both
  detail lists while the controller still reports objects is uncertainty, not hardware loss.
- Confirmed CV absence requires a successful CV response with explicit
  `Cachevault_Info: []` **and** the successful controller response's explicit empty
  `Cachevault_Info`. Generic command failures, firmware failures (including `use /cx/bbu`
  or `use /cx/cv` hints), missing fields and disagreeing probes are unknown, never absence.
  Present CV readings must parse successfully and agree with controller presence.

A rejected poll records the existing `Collection failed:` warning and saves no hardware
snapshot, recovery event or temperature clear. Three failed polls plus the configured
stale interval produce one critical monitoring outage. A pending hardware message also
waits after a failed poll even if the last reliable snapshot is still within the freshness
window. The failure events and last reliable snapshot survive restart; successful reliable
collection resumes comparison from that baseline and ends the outage. This conservative,
atomic approach needs **no database migration**, new settings, state table or backfill.
It deliberately pauses all hardware updates while the optional CV result is uncertain.

A confirmed disappearance records semantic Missing/Offline incident state plus
`present=false`; this is absence evidence, not a claim that firmware returned that row.
The PD serial is copied from the preceding reliable snapshot and survives raw retention.
Reappearance records `present=true` as info evidence; no recovery message is sent. This
makes old queued incidents obsolete and allows another loss within 24 hours to alert.
Aliases share incident identity, so spelling changes cannot restart warning confirmation
or bypass deduplication. Initial controller warnings retain the 15-minute confirmation;
initial critical readings alert immediately. Unchanged high readings create no new event,
including after a restart with the saved high-temperature snapshot.

### Remaining limitations and safe rollout

- New firmware shapes or absence markers require redacted JSON evidence and tests.
  Without explicit counts/identity lists or successful empty CV evidence, monitoring stays
  uncertain rather than claiming a disk/cache failure. Sequential inventory commands are
  not an atomic hardware read; disagreement during a topology change retries conservatively.
- No live hardware or absent-CV payload was collected by this PR. The complete and partial
  cases in P derive from the checked-in real redacted inventory and controlled mutations;
  the successful empty-CV shape is a contract fixture, not an assertion about production
  firmware. Capture evidence in a separately authorized investigation if firmware differs.
- VD identity is the existing single-controller VD ID. Reappearance between polls, or
  deletion/recreation with the same ID between polls, cannot be reconstructed. Likewise,
  there is no disappearance proof without a preceding retained reliable snapshot.
- Legacy snapshots were collected under the earlier permissive parser. No historical
  incidents are manufactured or replayed. A legacy CV-present flag without CV detail
  remains unknown; the next reliable poll establishes current evidence.
- Unknown CV state strings retain the existing generic critical incomplete-data policy;
  unknown PD/VD strings never become a named known failure. This PR adds no categories.
- Delivery still requires configuration, eligibility and fresh evidence, and remote receipt
  cannot be exactly-once across a crash or timeout.

For a later authorized deployment, keep automatic Telegram disabled, back up the database
and record the installed revision, install the reviewed artifact, and inspect collection
success/uncertainty locally before considering any separately authorized real manual test.
Do not induce hardware faults or replay events. No migration is required. Rollback restores
the previous application artifact with the same database/configuration; preserve delivery
rows and keep automatic Telegram disabled. Old code restores its previous detection gaps
and button behavior, so rollback is a fallback, not a way to validate the new contract.

## Verification references

Paths are relative to `tests/`; every transport/hardware boundary is mocked.

- **P:** `test_services/test_alert_detection_delivery.py`: real collection, detection,
  stored state, restart and exact mocked HTTP payloads; disappearance of one/all PDs and VD,
  queued pre-disappearance events, recovery/replacement/recurrence, initial thermal episodes,
  CV uncertainty/confirmed loss and incomplete-inventory rejection.
- **M:** `test_services/test_telegram_messages.py`: exact catalog for both hosts,
  aliases/transitions, unknown/hostile data, timestamps and identity/size/line contract.
- **T:** `test_alerts/test_telegram.py`: exact outbound JSON for sync/async and alert/test,
  plus deadline, streaming-size, redaction, redirect, compression and rate-limit regressions.
- **CLI:** `test_alerts/test_cli.py`: neutral exact text, destination override and failures.
- **N:** `test_services/test_telegram_notifier.py`: send policy, maintenance, dedup,
  worsening, retries, batch limits, SMTP independence, detector-to-message integration;
  `test_body_changes_cannot_change_dedup_or_channel_state` varies only body text and
  proves repeat/worsening outcomes, attempt counts and SMTP markers remain unchanged.
- **R:** `test_services/test_telegram_relevance.py`: fresh data/identity/state,
  interruption/cooldown/recovery/recurrence, boundaries, policy before candidate limits.
- **S:** `test_services/test_scheduler.py` and `test_scheduler_locking.py`: collection,
  confirmed outage, recovery, disabled phase, overlap and independent SMTP orchestration.
- **D:** `test_services/test_event_detector.py`, `test_event_detector_roc.py` and
  `test_event_detector_foreign_config.py`: baseline/transitions and behavioral regressions
  for the corrected gaps.
- **A:** `test_db/test_alembic.py::test_alembic_0009_backfills_telegram_state_and_preserves_smtp_marker`
  and DAO/maintenance tests: maintenance eligibility and unchanged email marker.
- **C:** `test_services/test_telegram_concurrency.py`: locks released during HTTP,
  concurrent collector/retention/disk checks, next-send revalidation and independent SMTP.
- **E:** `test_services/test_telegram_controller_episodes.py`: recovery proof and
  recurrence survive retained-history pruning and disabled Telegram.
- **K:** `test_services/test_telegram_cooldown_bounds.py`: bounded persistent deadlines,
  restart recovery and other-channel/terminal/confirmation invariants.

Run the unchanged `PATH="$PWD/.venv/bin:$PATH" bash scripts/ci.sh` for Ruff, formatting,
strict mypy, install-script checks and all unit/regression tests with the existing
100% line/branch coverage gate. CI also runs browser tests separately using
`pytest -o "addopts=" tests/e2e/ --browser chromium --no-cov -v`.

The [prepared knowledge-vault runbook update](knowledge-vault-megaraid-update.md) contains
the proposed external update. It preserves the historical user-reported deployment
checkpoint and describes the pending fixes without asserting a new production check.
It has not been applied to knowledge-vault.
