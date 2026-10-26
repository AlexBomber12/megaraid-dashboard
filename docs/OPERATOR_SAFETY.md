# Operator control coverage and safety contract

This inventory is for review against the rendered UI at `6385bfb` (main before the
parallel locate fix and PR #125). The source baseline in `operator-controls.json`
records **every button, form, input, select, link and disclosure** in templates and
application JavaScript, including controls inside conditional templates and the
JavaScript-created confirmation dialog. It is deliberately checked in rather
than regenerated in CI. Review both files when a control changes.

No test result here proves that an LED illuminated, a motor stopped, a buzzer
sounded, metadata changed on disk, or a rebuild completed on the target firmware.
All controller mutations execute a temporary fake. Physical hardware behavior is
unverified. Never use these scenarios on production hardware.

## Coverage matrix

All paths are relative to the application origin. POST is an operational state
change even when grouped as Configuration, shown in a maintenance section, or
called a dry run. Read-only navigation and filters must produce GET requests only.
`J` below is the wrapper's JSON-output argument; assertions use independent,
exact argument tuples in `tests/safety/scenarios.py`.

| Operator control | Endpoint / expected side effect | Availability and refusal states | Existing coverage extended / added |
| --- | --- | --- | --- |
| Start Maintenance; duration (15/30/60/240/1440 minutes); required reason | POST `/maintenance/start`; persist active window, expiry, actor and audit; suppress alerts | Form only when inactive. Browser required reason; server rejects blank, <1 or >1440 duration. Audit failure must roll back state. The resulting UI window must authorize controller operations. | `tests/e2e/test_maintenance_flow.py`, `tests/test_web/test_maintenance_routes.py`, `tests/integration/test_operator_safety.py` |
| Stop Maintenance in controller section and global banner | POST `/maintenance/stop`; clear window; audit active-to-inactive transition | Buttons only during an active window; stop after stop is idempotent. Audit failure must roll back. Banner and controller form must agree after refresh. | Same maintenance suites; `tests/test_web/test_maintenance_banner.py` |
| Blink Locate LED / Locate LED On; Stop Locate | POST `/drives/{e}:{s}/locate/start` and `/stop`; `/c0/eE/sS start/stop locate J`; audit controller result | Active LED changes start label and stop availability. Absent/unknown drive, no snapshot and refused controller status must not be presented as successful. Intended state/feedback assertions remain enabled while separate locate fix lands. | `tests/e2e/test_locate_flow.py`, `tests/e2e/test_drive_detail_page.py`, `tests/test_web/test_drive_actions_routes.py`, integration result matrix |
| Buzzer Silence; Enabled; Disabled | POST `/controller/buzzer/silence`, `/enable`, `/disable`; `/c0 set alarm=silence/on/off J`; audit operational change | Maintenance required. On disables Enable; Off disables Disable. Unknown alarm state covered. Failed command leaves snapshot unchanged and presents refusal. | `tests/e2e/test_buzzer_control_flow.py`, `tests/e2e/test_control_availability.py`, `tests/test_web/test_buzzer_routes.py`, integration matrix |
| Patrol Read Start / Stop; Switch to Auto / Manual | POST `/controller/patrol-read/start`, `/stop`, `/mode`; `/c0 start/stop patrolread J`, `/c0 set patrolread=on mode=auto/manual J` | Only when patrol status exists; Start ready/stopped, Stop active/paused/running, modes depend on current mode. Maintenance required; running/not-running conflict refused. API disable mode (`patrolread=off`) has existing service/route tests, no rendered disable control. | `tests/e2e/test_controller_operations.py`, `tests/test_web/test_routes_patrol_read_branches.py`, `tests/test_services/test_patrol_read.py`, integration matrix |
| Consistency Check Start / Stop; Switch to Auto / Manual | POST `/controller/consistency-check/start`, `/stop`, `/mode`; `/c0/vall start/stop cc J`, `/c0 set consistencycheck=on mode=auto/manual J` | Only when CC status exists; ready/stopped vs running gates and maintenance. API VD-specific start has existing tests; UI targets all VDs. Read-only refresh must not manufacture snapshot updates. | Controller browser suite; `tests/test_web/test_routes_consistency_check_branches.py`, `tests/test_services/test_consistency_check.py`, integration matrix |
| Spin Down | POST `/drives/{e}:{s}/actions/spindown`; `/c0/eE/sS spindown J` | Onln/UGood/UBad, maintenance, live state and identity check. It changes operational state even though shown under Configuration. Confirmation dialog Cancel must issue no request. | `tests/e2e/test_drive_detail_page.py`, `tests/test_web/test_drive_advanced_routes.py`, integration matrix |
| Hot Spare | POST `/drives/{e}:{s}/actions/hotspare`; `/c0/eE/sS add hotsparedrive dgs=0 J` | UGood, existing target DG, maintenance, live state/identity. Missing DG disables with reason. Target DG bounds and wrong DG refused. | Advanced browser/route suites; integration matrix |
| Mark as UBad; Mark as UGood | POST `/drives/{e}:{s}/actions/mark-ubad`, `/mark-ugood`; `/c0/eE/sS set bad/good J` | UGood→UBad and UBad→UGood only. State mismatch disables with explanation. Maintenance and live identity apply; UBad additionally requires the destructive flag and typed serial confirmation; wrong serial keeps dialog Confirm disabled. Cancel must preserve DB and trace. | Advanced browser/route suites; integration matrix |
| Begin Replacement; Continue; Cancel | Local wizard controls; no mutation until confirmed step | Drive exists. Initial Cancel focuses entry button. Serial must match visible outgoing drive; dry-run checked by default. Wizard must disable while running and block old identity after a live refresh. | `tests/e2e/test_replace_wizard_flow.py`, `tests/e2e/test_drive_detail_page.py`, `tests/e2e/test_live_refresh.py`, `tests/test_web/test_drive_replace_ui.py` |
| Set Offline & Missing (Step 1 runs two endpoints) | POST `/drives/{e}:{s}/replace/offline`, `/missing`; `/c0/eE/sS set offline/missing J`; separate audits | Offline: Onln/Offln/Failed/UBad/UGood; Missing: Offln. Maintenance + destructive flag, persisted and live serial, live state. Failure in Offline stops Missing. Dry runs validate without executing anything or adding audit rows. | Replacement browser suite; `tests/test_web/test_drive_replace_routes.py`, integration matrix |
| New Drive Inserted; new serial; dry-run checkbox | Local stage transition plus GET `/drives/{e}:{s}/replace/topology` | Server-derived DG/array/row. Replacement serial must differ from outgoing serial. Topology failures leave Insert disabled. Existing tests cover identity changes and refresh during wizard. | Replacement browser suite; `tests/test_web/test_drive_replace_step3.py`, `tests/e2e/test_live_refresh.py` |
| Insert & Start Rebuild; Close; Close Wizard | POST `/drives/{e}:{s}/replace/insert`; `/c0/eE/sS insert dg=0 array=0 row=0 J`; audit then rebuild status reads | Latest audit must be successful Missing for same slot; outgoing/replacement serial checks, live identity/state, maintenance + destructive. Dry run returns planned argv without controller calls/audit. Failure stays in result stage. Close ends polling. | Replacement browser suite; existing Step 3, rebuild-status and live-refresh tests; integration matrix |
| Review Foreign Configuration link | GET `/controller/foreign-config`; `/c0/fall show all J` | Link depends on snapshot foreign presence; details depend on live result. No foreign configuration hides action forms. Unknown/failed probe must explain uncertainty. | `tests/e2e/test_foreign_configuration.py`, `tests/test_web/test_foreign_config_routes.py` |
| Import disclosure; confirmation digest; dry-run; Import button | POST `/controller/foreign-config/import`; `/c0/fall import J` | Live foreign metadata must exist; exact live digest; known non-rebuilding snapshot; maintenance + destructive for execution. Dry run permits **read-only show calls only**, no audit/state changes. Closing disclosure is cancellation, not execution. | Foreign browser suite; existing foreign route tests; integration matrix |
| Clear disclosure; typed CLEAR FOREIGN CONFIG; dry-run; Clear button | POST `/controller/foreign-config/clear`; `/c0/fall delete J` | Browser pattern and server phrase check; same foreign/rebuild/mode gates. No mutation on rejected confirmation, probe failure or dry-run. Errors must appear in result region even on HTTP 4xx/5xx. | Same foreign suites |
| Brand; Overview/Controller/Drives/Events/Audit Log navigation; breadcrumbs | GET `/`, `/controller`, `/drives`, `/events`, `/audit` (redirects to event category filter) | Auth required except whitelisted health/static. Current nav state must update. Empty snapshot pages remain usable. | `tests/e2e/test_main_page_redesign.py`, `tests/e2e/test_controller_detail_page.py`, `tests/e2e/test_control_availability.py`, existing layout/routes/auth tests |
| Controller card; drive tiles; Drives count; backplane slots; table slot links; Previous/Next drive | GET controller/drive detail paths | Links require snapshot rows. First/last drive has disabled neighbor link; single enclosure only. No POST. | Existing main/drive detail browser suites, control availability suite, slot-link/backplane route tests |
| Refresh now; automatic live refresh | GET current page and live-region partials | Live/paused/stale/error states; preserve active wizard identity and chart sizing. No POST. | `tests/e2e/test_live_refresh.py`, existing redesigned page tests, availability suite |
| ROC ranges 1h/24h/7d/30d | GET `/controller/roc-history?range_hours=1/24/168/720` | Controller page, active tab switches. No POST. | Existing controller browser suite plus availability suite |
| Drive history ranges 7d/30d/365d | GET `/drives/{e}/{s}/charts?range_days=...` with captured identity | Drive page requires snapshot; identity anchors chart; active range switches. No POST. | Availability browser suite, existing drive chart/web tests |
| Table sort: Slot, Model, State, Temp, Size, Media Errors, Other Errors, Predictive | Local sort only; ascending/descending aria state | Drive rows exist. No HTTP write and no database change. | Availability browser suite and existing table/layout tests |
| All Events link; severity/category chips; Load Older Events; event slot links | GET `/events`, query filters and `/partials/events` cursor | Empty/nonempty events; older button only with next cursor; slot links only for recognized slot references. Filters toggle and preserve state. No POST or controller command. | `tests/e2e/test_events_page.py`, availability suite, existing events pagination/filter/slot-link route tests |
| Maintenance hint links; foreign Back to Controller; native disclosure summaries | GET fragment/page navigation or local expansion | Conditional hints when inactive, absent foreign back-link, Configuration/Changes Array State disclosures and wizard steps | Source inventory plus browser cancel/navigation coverage |

## Failure contract and isolation

`tests/integration/test_operator_safety.py` runs the same real FastAPI application
and migration-backed temporary SQLite fixtures as Playwright. It parameterizes all
20 controller mutation endpoints over Success, exit-code-0 JSON Failure, missing
status, unknown status, nonzero exit, timeout, unavailable executable and audit
failure. Gate matrices cover authentication, CSRF, maintenance, destructive flags,
state/DG conflicts, confirmation, replacement sequencing and live serial mismatch.
Existing unit suites retain parser variants, invalid inputs, detailed topology,
rebuild, rollback and UI layout coverage; this matrix does not copy those tests.

Assertions include exact subprocess argv, attempted mutations versus read probes,
HTTP status, truthful audit outcome, unchanged controller, physical/virtual drive and CacheVault snapshot rows, and browser
feedback/button state/refresh. Maintenance additionally checks persisted state and
audit atomicity. Audit fault injection raises after the real SQL INSERT and proves transaction rollback.
A command may already have reached the fake before audit storage
fails; the expected HTTP 500 must make that uncertainty visible, not claim success.
The integration client can echo CSRF; browser tests cannot manually add a valid
header or cookie. A guard rejects that old browser workaround.

The HTTPS fixture binds loopback, creates an ephemeral self-signed certificate,
authenticates through real Basic auth middleware and accepts that certificate only
in test contexts. Chromium itself stores the actual Secure `__Host-csrf` cookie.
Production JS obtains the token and submits actual clicks. Negative CSRF tests
remove/break a header rather than synthesizing a valid one.

Settings explicitly override inherited environment and ignore `.env`; collectors,
metrics listeners and Telegram are disabled. Application subprocess calls are
restricted to the exact temporary fake path, with sudo forbidden. Outbound socket
connections are restricted to loopback, browser external requests are blocked, and
SQLAlchemy connection/query guards reject databases outside the pytest temporary
root. No real StorCLI, production data, SMTP or Telegram transport is allowed.
Fake call traces contain command arguments only; no authentication headers,
cookies, password hashes or real credentials are written to artifacts.

## Product defects to track

These are intended failing acceptance assertions, not skips or xfails:

- Locate currently treats JSON Failure as HTTP success and produces generic Sent
  feedback; error/stop/button-state handling belongs to the parallel locate fix.
- Missing/unknown Command Status is accepted as success across mutation families.
  Replacement also accepts explicit JSON Failure. Audit rows can falsely say succeeded.
- Advanced actions compare live state but not the live serial against the displayed
  drive. The destructive UBad route does not enforce the destructive flag.
- UI availability disagrees with server rules for UBad Spin Down and Failed Mark as UGood.
- The maintenance UI persists a database window, while controller routes inspect
  the separate settings flag. A started UI window does not authorize writes.
- Patrol Read and Consistency Check view-model loaders always return `None`, so
  their state-dependent controls cannot currently be reached in the real browser.
- Foreign/maintenance HTMX refusals are not put in the operator-visible result
  region on 4xx/5xx. An HTTP refusal alone is insufficient feedback.
- Advanced/buzzer submissions have no immediate in-flight feedback.
- Replacement insert does not reject a live rebuilding drive.
- Locate and some drive precheck refusals omit a failed/rejected audit outcome.

Configuration is not a safety classification. Spin Down, Hot Spare and all other
listed POST controls change operational state. A label such as Safe or
Read-only on these controls is a product defect; rendered label assertions reject it.
Do not loosen assertions to accommodate these issues. Fix them in separately
reviewed product changes, then rerun the full suite.

## Running and reviewing

```sh
PATH="$PWD/.venv/bin:$PATH" bash scripts/ci.sh
.venv/bin/pytest -o 'addopts=' tests/integration --no-cov -q
.venv/bin/pytest -o 'addopts=' tests/e2e --browser chromium --no-cov -q
```

The integration marker is excluded by default and run explicitly in CI. All three
commands are required. Install Chromium with `playwright install chromium`; local
HTTPS also requires `openssl`. CI supplies both. No deployment, scheduler or
production request is part of these commands.

A new control must update the semantic matrix, source inventory, exact command
scenario (if mutating), and a real browser scenario. The default test suite checks
the source baseline, route/scenario equality and browser references. Integration
and Chromium CI then exercise each scenario. Static inventory includes hidden
conditional branches so a new state-only control cannot evade the guard.

Before requesting merge: rebase this branch on main **after the locate fix (#126) merges**,
resolve inventory and fixture conflicts with the actual final UI, review the
matrix, and rerun complete CI, isolated integration and Chromium. Keep PR #125 and
the locate branch untouched. This PR must remain unmerged while acceptance failures
remain. The read-only production procedure is separate: `PRODUCTION_READ_ONLY_SMOKE.md`.
