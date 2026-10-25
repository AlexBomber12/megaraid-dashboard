# UI/UX fix pack — megaraid-dashboard

Source: `AlexBomber12/megaraid-dashboard@main` (3bc9e7c). Fixes every finding in `Design Review.dc.html`.
Files under `handoff/` mirror repo paths — copy them over the repo, then do the manual steps below.

## Decisions
- Auto-refresh every 30 s on **all** monitoring pages (Overview, Controller, Drives, Drive detail, Events/Audit status bar).
- Foreign config: **one flow** — the `/controller/foreign-config` page. Controller page shows status + link only.
- Destructive drive actions: **modal confirm with typed slot** (`252:3`); non-destructive advanced actions: plain confirm.
- Controller page stays one column; Hardware Identity collapsed. Maintenance start moved **Overview → Controller** (`#maintenance`).
- English, Title Case headings & buttons. Existing dark palette kept.

## Files

Replaced:
- `static/css/app.css` — v2-only stylesheet. Legacy v1 selectors removed (`.site-header`, `.site-nav*`, `.content`, `.drive-table`, `.stat-card`, `.status-tile`, `.history-table`, `.alert-status*`, `.attribute-grid`, `.chart-panel` …). Adds `:focus-visible`, 5-step type scale, `.kv-grid`, `.alert`, `.disclosure`, `.stepper`, `.confirm-dialog`, `.sort-button`, `.segmented`, auto-fit grids, 1024/760/720/560 breakpoints, reduced-motion.
- `static/js/data-table.js` — sort via `<button>` in `<th>`, `aria-sort`, keeps sort across live refresh.
- `static/js/local-time.js` — header clock removed; re-localizes on `live:updated`.
- `web/templates.py` — new `bytes_iec` filter.
- Templates: `layouts/base.html`, `pages/{main,controller_detail,drives,drive_detail_v2,events,foreign_config}.html`, `partials/{site_header,system_health_bar,maintenance_banner,maintenance_start_form,controller_summary_card,drive_grid,activity_timeline_v2,backplane_diagram,physical_drive_table,events_data}.html`, `partials/controller_detail/{health_snapshot,live_operations,cachevault,raid_config_table,scheduled_tasks,buzzer_control,foreign_config,hardware_identity}.html`, `partials/drive_detail/{health_snapshot,physical_position,identity_grid,connection_grid,advanced_actions,replace_destructive}.html`.

New:
- `static/js/live-refresh.js` — fetches current URL every 30 s, swaps `[data-live-region][id]`; pauses while typing, a dialog is open, a wizard stage is open (`[data-live-pause]`), or tab hidden. Drives header status (Live / Paused / Update failed) + manual refresh button.
- `static/js/confirm-dialog.js` — `<dialog>` confirm driven by `data-confirm-*` attributes on forms; intercepts submit in capture phase.
- `static/js/replace-stepper.js` — syncs the 4-step stepper with the visible wizard stage (no changes to `replace-wizard.js`).

Unchanged and still used: `replace-wizard.js`, `drive-actions.js`, `maintenance.js`, `csrf.js`, `main_page_refresh.html` (now included inside `#overview-live`), `rebuild_progress*.html`, `error_sparkline.html`, `drive_charts.html`.

## Manual steps (backend / small edits)

1. **Status bar on every page.** `base.html` renders the footer only when `view_model.system_health` exists. Add `system_health` to the view models of Drives, Events and Foreign config (or expose it via a context processor in `create_templates`).
2. **Endpoint name check.** Templates use `request.url_for('controller_foreign_config')` for the GET page (routes.py:2997). Confirm the route has no custom `name=`.
3. **Audit title.** `events_data.html` shows "Audit Log" when `active_nav == "audit"` — make sure `events()` passes `active_nav` into the context of the partial renders too (it already does for the page).
4. **Heading copy (Title Case)** — one-line edits not shipped as files:
   - `partials/controller_detail/roc_history_chart.html`: `RoC temperature history` → `RoC Temperature History`; dataset label `RoC C` → `RoC °C`.
   - `partials/drive_detail/temperature_chart.html`: `Temperature chart` → `Temperature History`.
5. **`main_page_refresh` route** is no longer polled by the page (live-refresh.js re-fetches `/`). Keep it for compatibility or delete it with its tests.
6. **Optional:** Drive tile `tile_severity` / RAID `state_severity` / `row_state` should only emit `optimal|warning|critical|unknown`. CSS still aliases `ok|degraded|warn|crit` defensively.

## Tests to update
Expect failures in string-matching tests under `tests/test_web/` and `tests/e2e/`:
- Title Case headings/buttons (`Health Snapshot`, `Live Operations`, `RAID Configuration`, `Drive Backplane`, `Recent Activity`, `Start Maintenance`, `Stop Maintenance`, `Begin Replacement`, `Load Older Events`, `Switch to Auto|Manual`, `Alarm Buzzer`, `Silence Now`).
- Maintenance form moved: `test_maintenance_*`, `test_main_page*` — form now on `/controller`, not `/`.
- Main page: no `hx-trigger="every 30s"`; live region `#overview-live` instead.
- Controller foreign config: no import/clear forms or `confirm()` on `/controller`; link to `/controller/foreign-config` when present.
- Drive detail: locate no longer has `data-confirm`; advanced-action forms carry `data-confirm-*`; prev/next links always rendered (`aria-disabled` when absent).
- Drives table: sortable `<th>` contain `<button class="sort-button">`, default `aria-sort="ascending"` on Slot; column labels `Media Errors`, `Other Errors`.
- Events: severity icon column removed; empty state uses `<h2>`; `<h1>` always present.
- Backplane slot classes normalized to `backplane-slot--{optimal|warning|critical|unknown}` (`position-slot*` removed).
- Header: `.utc-clock` removed; `[data-live-status]` added.

## Suggested PR split
1. CSS + base/header + live-refresh (no behavior change beyond refresh).
2. Overview + Drives + Events.
3. Controller + maintenance move + foreign config page.
4. Drive detail + confirm dialog + replace stepper.
