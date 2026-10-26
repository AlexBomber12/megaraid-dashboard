# Concise Telegram catalog and delivery audit

Audit base: `origin/main` = `232a719a9e4e507d78ea45efbeb52e28ab00b9b9`.
Evidence is repository code and mocked tests, not an inspection of running hardware.
The user's supplied production status is commit `232a719`, schema
`0009_event_deliveries`, successful real manual delivery, automatic Telegram disabled.
This pending wording PR neither verifies nor changes that deployment.

## Detection to delivery

1. `services/event_detector.py::detect` compares saved/current structured snapshots.
   Baseline/new VD/PD/CacheVault discoveries emit unhealthy-state/counter/flag events;
   disk temperature uses persisted identity and hysteresis. RoC temperature needs a
   previous snapshot. Replacements are info events plus any new disk incidents.
2. `services/scheduler.py::CollectorService.run_once` saves snapshots, RoC clear evidence,
   events and temperature states atomically. `_record_collection_failure` records warnings;
   `_monitoring_stale_summary` requires >=3 failed polls and the configured stale interval
   before the explicit `monitoring_outage=true` critical event. Success records recovery.
3. `db/dao.py::record_event` permanently skips Telegram events born during maintenance;
   migration `0009` permanently skips prior warning/critical events (`pre_migration`).
4. The scheduler's optional Telegram phase precedes independent SMTP.
   `services/telegram_notifier.py::_load_candidates` filters complete eligibility before
   LIMIT 100; terminal deliveries and future events/due times are excluded.
5. The cycle checks maintenance, warning confirmation, current relevance and deduplication
   before claiming. `services/telegram_relevance.py` uses saved fresh readings and disk
   identity; missing evidence waits, positive recovery/replacement evidence skips.
6. Only then `services/telegram_messages.py::build_telegram_text` renders content.
   `incident_state`, `incident_key`, outage/recovery/recurrence helpers and policy remain
   behaviorally unchanged. Text, hostname and summary are not dedup keys.
7. `alerts/telegram.py::TelegramTransport.send` delegates to `send_async`: both post
   literal text plus one fixed URL button. No parse_mode or callbacks. The CLI
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
| PD Failed | Send | `🔴 SERVER: отказ диска.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 76 / 79 | M; N/R |
| PD Missing / Msng (entry present) | Send | `🔴 SERVER: диск не обнаружен.\nПерешли в ChatGPT. Диски не трогай.\n26.10 10:57 · №42` | 82 / 85 | M; N/R |
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

Aliases, casing and surrounding whitespace are normalized **by the formatter**, for
baseline and transition events. Detector limitations below remain unchanged. Unknown
values never introduce a state, bay, count or measurement from free text. Temperature
rendering accepts only integral non-bool 0–200 °C metadata. The alert's recorded occurrence
measurement is shown, not a fabricated latest reading. Missing/invalid event measurements
use the fallback; a missing current sensor still defers dispatch. Footer tests cover
Rome summer/winter time, naive stored UTC, and full 64-bit IDs. The longest catalog preview
is 104 characters; the 160-character contract leaves room for real event IDs.

## Suppress, defer and retry matrix

`—` is no outbound message; character count is 0. References name existing regression
suites; only wording/footer assertions were updated where those bodies changed.

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
| Born during maintenance or pending when maintenance is active | Permanently skip maintenance | — | 0 | N maintenance tests; A |
| Existed before migration 0009 | Permanently skip pre_migration | — | 0 | A |
| Already sent/skipped, event in future, retry not due | Exclude from candidates | — | 0 | N; R |
| Warning before confirmation deadline (default 15 min) | Defer awaiting_confirmation; no attempt | — | 0 | N; R |
| Repeated same warning / controller oscillation in hysteresis band | Preserve first confirmation deadline; dedup after send | — | 0 | R; E |
| Warning cleared or escalated before dispatch | Skip not_confirmed / obsolete | — | 0 | N; R |
| Missing/stale snapshot (older than max(60 s, 2 × collection interval)) | Defer awaiting_fresh_data; no attempt | — | 0 | R |
| Missing current sensor | Defer awaiting_temperature; no attempt | — | 0 | R; E |
| Unknown drive identity or malformed drive subject | Defer awaiting_drive_identity; no attempt | — | 0 | R |
| Missing VD/counter incident state | Defer awaiting_incident_state; no attempt | — | 0 | R |
| Removed/replaced drive; changed/recovered VD/CV/PD; SMART cleared; counter below event value | Skip obsolete | — | 0 | R |
| Temperature recovered: disk strictly below clear boundary, controller at/below boundary | Skip obsolete | — | 0 | R; E |
| Successful snapshot at/after outage | Skip outage obsolete | — | 0 | R; S |
| Same incident delivered within 24 h; increasing counter without recovery | Skip duplicate | — | 0 | N; R |
| Worsening state, different CV dimension, replacement identity, recurrence after recovery/flag rising edge, repeat beyond 24 h | Send new eligible active incident, catalog wording | Catalog | Catalog | N; R; E |
| Cooldown active, five sends reached, or earlier transport failure this cycle | Defer channel_deferred, no attempt | — | 0 | N; R; K |
| 100 candidates reached | Remaining candidates wait for later cycle | — | 0 | R |
| HTTP/network/timeout/API failure (including 401/403) | Durable retry, backoff 1/2/4…60 min; stop further sends this cycle | — | 0 accepted locally | N; T |
| HTTP 429 (even unreadable body) | Durable per-event retry plus shared cooldown; positive hint capped 3600 s, invalid hint 60 s | — | 0 accepted locally | T; K; R |
| Old oversized persisted attempted retry/cooldown | Repair once to <=1 h; preserve other channels/confirmation/terminal state | — | 0 | K |
| Interrupted sending or legacy failed row | Requeue, revalidate, retry; remote acceptance before crash can duplicate | Catalog on retry | Catalog | N; R |
| Active risk older than 3 h / >=8 failed attempts | Keep bounded retries; log prolonged failure from attempt 8; no lifetime cutoff | Catalog when accepted | Catalog | N; R |
| Missing data / transport failure indefinitely | Remain pending; delivery not guaranteed | — | 0 accepted locally | R; N |

A failed request can already have been accepted remotely; the table does not claim
exactly-once receipt or that zero remotely received messages can be proved on timeout.
Claim and outcome are committed separately. Database locks are released during HTTP;
collection, retention and disk checks continue, and later candidates recheck time/data/
maintenance. The process notifier lock spans both channels. SMTP markers and policy stay
independent; each phase runs even when the other fails. Response handling retains the
10-second absolute deadline, 64 KiB cap, compression/redirect restrictions and token
redaction. None of these delivery controls changed in this PR.

## Confirmed detection limits (unresolved)

1. **Entirely absent drive:** `event_detector.py::_detect_physical_drives` iterates only
   `current.physical_drives`. `_detect_drive_replacements` clears temperature state for
   absent slots but creates no event. Reproduce with `_previous()` and
   `_current().model_copy(update={"physical_drives": []})` in detector tests: `events=[]`.
   Existing `test_slot_disappearance_clears_temperature_state` already proves this;
   `test_disappeared_drive_only_has_indirect_array_coverage` proves that changing VD Optl
   to Dgrd/OfLn produces a critical VD alert, while keeping Optl does not. If both VD and
   PD entries disappear, `_detect_virtual_drives` also skips absent entries
   (`test_removed_array_and_drive_produce_no_disappearance_event`). The scheduler treats
   this valid snapshot as a successful collection, so no outage event covers it.
   **Consequence:** no guaranteed notification for a vanished disk or array. A stored
   `Missing`/`Msng` PD event covers only a disk that still has a snapshot entry; stale
   queued events for a vanished disk become obsolete, not a new disappearance alert.
2. **PD classification is case-sensitive:**
   `event_detector.py::physical_drive_state_severity` recognizes only exact Failed,
   Missing, Msng, Offline, Offln spellings. `failed`, ` FAILED `, `missing`, ` Missing `
   or `offline` produce no baseline event / an info transition; Telegram suppresses them.
   `test_existing_disk_classification_is_case_sensitive` reproduces both paths. Formatter
   normalization cannot recover an event that the detector never classified as critical.
3. **Initial controller overheating:** `event_detector.py::detect` calls
   `_detect_roc_temperature` only with a previous snapshot; `_detect_baseline` omits it.
   Initial 110 °C followed by another 110 °C produces no temperature event. A later
   threshold crossing or reading after an unavailable sensor can create one.
   `test_initial_controller_overheating_without_crossing_is_not_detected` reproduces
   all three observations. **Consequence:** an already-hot controller can remain silent
   if it stays high without a qualifying new crossing. Initial disk temperature is covered.
4. **CV health spelling:** `_cachevault_state_severity` treats only exact Optimal/Optl
   as healthy. `optimal`, ` Optimal `, `optl` create a critical event, demonstrated by
   `test_existing_cachevault_classification_does_not_normalize_healthy_aliases`.
   The concise formatter uses the unknown-data fallback instead of inventing a fault;
   classification/eligibility are preserved, so a false positive remains possible.

These are code/test findings, not evidence that production has any failed hardware or
that its actual storcli output uses these alternative spellings. No detection redesign,
severity change, schema migration or production setting change is bundled here.

## Verification references

Paths are relative to `tests/`; every transport/hardware boundary is mocked.

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
  `test_event_detector_foreign_config.py`: baseline/transitions and the reproductions above.
- **A:** `test_db/test_alembic.py::test_alembic_0009_backfills_telegram_state_and_preserves_smtp_marker`
  and DAO/maintenance tests: durable skips and unchanged email marker.
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
exact proposed replacements. It distinguishes the user-reported deployed baseline,
successful manual delivery, disabled automatic delivery and this pending wording PR.
It has not been applied to knowledge-vault.
