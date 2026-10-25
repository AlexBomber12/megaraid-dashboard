# Task: apply UI/UX fix pack

## Context
The folder `handoff/` at the repository root contains a ready UI/UX fix pack for megaraid-dashboard.
`handoff/src/...` mirrors the repo paths 1:1. `handoff/HANDOFF.md` describes every change, decisions, manual steps and affected tests — read it first.

## What to do
1. Create branch `ui/consistency-fix-pack` from `main`.
2. Copy every file from `handoff/src/megaraid_dashboard/` over `src/megaraid_dashboard/` (overwrite). Do not copy `handoff/src/megaraid_dashboard/static/icons.svg` if it is identical to the existing one.
3. Do the "Manual steps" from `handoff/HANDOFF.md`:
   - add `system_health` to the Drives, Events and Foreign config view models (same shape as on the Overview page), or expose it via a context processor;
   - verify the GET route for the foreign config page is reachable as `url_for("controller_foreign_config")`;
   - Title Case: `roc_history_chart.html` → "RoC Temperature History", dataset label "RoC °C"; `temperature_chart.html` → "Temperature History";
   - make sure `active_nav` is available in the events partial renders.
4. Run the full test suite. Update tests that fail only because of the intended markup/copy changes listed in "Tests to update" in `HANDOFF.md`. Do not weaken security tests (CSRF, auth, rate limit, destructive-mode checks). If a failure is not explained by HANDOFF.md, fix the code, not the test.
5. Add tests:
   - `bytes_iec` filter (None, 0, 1023, 1024, 4000787030016 → "3.64 TiB");
   - Controller page contains `id="maintenance"` and the start-maintenance form; Overview page does not;
   - Drive detail destructive advanced-action forms have `data-confirm-phrase="<enc>:<slot>"`;
   - pages Overview, Controller, Drives, Drive detail render at least one `[data-live-region]` with an id.
6. Run linters/formatters used by the repo (see AGENTS.md / CONTRIBUTING.md). Bump `static_asset_version` if the repo uses one, so browsers reload CSS/JS.
7. Manually smoke-test in a browser at 1440 px and 375 px width: Overview, Controller, Drives, Drive detail (open the replace wizard and the confirm dialog), Events, Foreign config. Check no console errors and that the header shows "Live · HH:MM:SS" and updates every 30 s.
8. Delete the `handoff/` folder, update CHANGELOG.md, open a PR (or 4 PRs as suggested in HANDOFF.md) with a short summary and before/after screenshots.

## Acceptance criteria
- All tests and linters pass.
- No template references a CSS class that is missing from `app.css`.
- Every page works without horizontal page scroll at 375 px.
- Destructive drive actions cannot run without the typed-slot confirmation.
- Auto-refresh does not interrupt typing in inputs or an open replace wizard.
