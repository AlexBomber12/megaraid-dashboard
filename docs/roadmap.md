# MegaRAID Dashboard — Product Roadmap

Document version: 2026-05-02
Author: Aleksei Ratnikov
Status: Active
License: MIT

This document is the source of truth for what is left to build, in what order, and why. Every PR proposed below maps to a single task file under `tasks/PR-NNN.md` once scheduled. Task files conform to the schema in `AGENTS.md` / `TASK_SCHEMA.md`.

## 1. Where we are today (baseline, 2026-05-02)

Production deployment on NAS (192.168.50.2) under `/opt/megaraid-dashboard`, running as systemd unit `megaraid-dashboard.service` under user `megaraid`. SQLite DB at `/var/lib/megaraid-dashboard/megaraid.db`, secrets at `/etc/megaraid-dashboard/env` (mode 600). storcli invoked via `sudo` (NOPASSWD rule for `/usr/local/sbin/storcli64`). FastAPI app served by uvicorn on `0.0.0.0:8090`.

Already shipped (PR-001 through PR-004 + earlier 005-series):
- storcli wrapper with JSON parsing.
- SQLite schema with three migrations (initial, pd_temp_state, events.notified_at).
- APScheduler-driven background collector (300 s interval, file-locked).
- Event detector with rules across categories: `controller`, `cv_state`, `pd_state`, `vd_state`, `smart_alert`, `temperature`.
- Read-only web UI: Dashboard, Drives, Drive detail with charts, Events log with cursor pagination.
- Email alerts pipeline: Proton SMTP transport, CLI test command, notifier service with dedup and soft throttle, scheduler integration.
- Overview alert status panel showing last sent, pending, sent-in-last-hour, notifier health.

## 2. Guiding principles

1. **One screen, no scroll.** Critical signal above the fold on 1080p / typical laptop.
2. **Read-status at a glance.** Two seconds to know whether intervention is needed. Color, icon, shape — in that order.
3. **Local time always, dynamically detected.** Server emits UTC ISO 8601, browser converts to user-local via JS. No hardcoded timezone.
4. **No duplicated information.** Dashboard summarizes; other pages detail.
5. **Read-only by default, dangerous actions explicit.** Destructive operations require confirmation, audit log, tighter auth.
6. **Small, sequential PRs.** Target under 300 lines net new. Split when it grows. One concern per task file.
7. **Production-target = NAS 192.168.50.2.** Multi-host support is not a goal.
8. **Operability first.** A new operator deploys from `INSTALL.md` alone. If a step requires the original author present, it is broken.
9. **Open-source discipline.** MIT license, public repo, written for someone other than the author.

## 3. UI design system

The current implementation has solid foundations (design tokens in CSS, dark theme, semantic colors). This section is the design system that all redesign PRs reference.

### 3.1 Design principles

1. **Information density.** Tufte's data-ink ratio: maximize pixels devoted to data, minimize chrome. Reduce hero typography to 0.875 rem labels and 1.5 rem values.
2. **Visual hierarchy through size, color, position — in that order.**
3. **Progressive disclosure.** Dashboard summarizes; click drills into detail.
4. **Consistent affordances.** Clickable elements look clickable everywhere.
5. **Recognition over recall.** State labels match storcli vocabulary so the operator does not translate.
6. **Status at every level.** Tile color, badge style, icon. Color-glance reads system state without text.
7. **Whitespace as a tool.** 8 px within groups, 24 px between groups.
8. **Typography scale.** Three sizes maximum per page: 0.75 rem labels, 1 rem body, 1.5 rem values.
9. **Color with meaning, not decoration.** Five colors: optimal, warning, critical, info, neutral.
10. **Direct manipulation.** Locate-LED button on the drive entity, not buried in settings.
11. **Forgiveness.** Destructive actions require confirmation; cancel is default focus.
12. **Feedback latency budget.** Click response under 100 ms; command result under 2 s; spinner if longer.

### 3.2 Design tokens (additions to existing `app.css`)

```
--tile-padding:           12px;
--tile-radius:            8px;
--tile-border-width:      1px;
--tile-min-width:         180px;
--tile-height:            72px;
--icon-size-sm:           16px;
--icon-size-md:           20px;
--badge-padding:          2px 8px;
--badge-radius:           12px;
--font-size-label:        0.75rem;
--font-size-body:         1rem;
--font-size-value:        1.5rem;
--font-weight-label:      500;
--font-weight-value:      600;
--letter-spacing-label:   0.04em;
--text-transform-label:   uppercase;
--color-info:             #6cb4ff;
--color-neutral:          #6e7782;
--surface-hover:          rgba(255, 255, 255, 0.04);
--transition-fast:        120ms ease-out;
```

### 3.3 Component vocabulary

- **StatusTile** — fixed dimensions, label uppercase top, value below, optional icon at left, colored left-border for state.
- **Badge** — pill-shaped, color-coded.
- **MetricRow** — horizontal label-value pair.
- **Timeline** — vertical event list with severity icon, primary text, timestamp.
- **DataTable** — dense table with sortable columns, status indicators.
- **ActionButton** — primary action with icon, label, spinner-on-pending.
- **Banner** — full-width strip for system state.

### 3.4 Iconography

Lucide subset (MIT licensed, vector). Inlined as SVG sprite, max 4 KB total. Icons: `check-circle`, `alert-triangle`, `x-circle`, `help-circle`, `info`, `bell`, `bell-off`, `hard-drive`, `cpu`, `thermometer`, `lightbulb`, `refresh-cw`, `clock`.

### 3.5 Color usage rules

- Optimal (green), warning (yellow), critical (red), info (blue), neutral (gray).
- Color is never the sole carrier of meaning; always paired with icon or text.
- Hover changes brightness ±5%, never hue shift.

### 3.6 Anti-patterns to avoid

- Hero typography over 2 rem.
- Repeated information across pages.
- Verbose timezone suffixes ("CEST", "UTC+02:00") — replaced with implicit local via JS.
- Modal dialogs for non-destructive actions.
- Auto-refresh that scrolls or rearranges content.
- Toast notifications.
- Per-page CSS files.

## 4. Severity tiers

- **P0** — production safety blockers. Currently 9 PRs.
- **P1** — standard delivery. Currently 24 PRs.
- **P1.5** — observability stack (Prometheus + Grafana). Currently 11 PRs.
- **Security** — 2 audit PRs after P1 completes.
- **P2** — optional / deferred. Currently 8 PRs.

## 5. Deliverables

Each entry below maps to a `tasks/PR-NNN.md` file conforming to the schema in `AGENTS.md`. Numbers are tentative and adjusted at queue time. All PRs target under 300 lines net new where feasible.

### P0 — Production safety blockers (9 PRs)

#### PR-005 — Basic auth middleware
- ~120 lines. Type: feature, Complexity: low, Coder: any, Deps: none.
- Starlette HTTPBasic middleware, bcrypt verify, whitelist `/healthz` and `/static/*`.
- Files: `web/middleware.py`, `app.py`, `tests/test_web/test_auth.py`.

#### PR-006a — RoC parser + storage column
- ~150 lines. Type: feature, Complexity: low, Coder: any, Deps: PR-005.
- Parse `ROC temperature(Degree Celsius)` from storcli JSON; new column `controller_snapshots.roc_temperature_celsius`; migration 0004.
- Files: `storcli/parser.py`, `db/models.py`, `migrations/versions/0004_*.py`, tests.

#### PR-006b — RoC config fields + validation
- ~80 lines. Type: config, Complexity: low, Coder: any, Deps: PR-006a.
- `ROC_TEMP_WARNING_CELSIUS=95`, `ROC_TEMP_CRITICAL_CELSIUS=105`. Validator: critical greater than warning, both within `[40, 130]`.
- Files: `config.py`, `tests/test_config.py`.

#### PR-006c — RoC event detector rule + per-category suppress override
- ~250 lines. Type: feature, Complexity: medium, Coder: any, Deps: PR-006b.
- Detector rule `roc_temperature` with hysteresis. Notifier per-category override: `controller_temperature` keeps 24-hour suppress window regardless of global setting.
- Files: `services/event_detector.py`, `services/notifier.py`, tests.

#### PR-006d — RoC tile in current overview (interim)
- ~100 lines. Type: ux, Complexity: low, Coder: any, Deps: PR-006c.
- Adds a tile to existing status strip; superseded by PR-009c when redesign lands.
- Files: `services/overview.py`, `templates/partials/overview_data.html`, tests.

#### PR-007 — Dynamic local time via JS
- ~150 lines. Type: ux, Complexity: low, Coder: any, Deps: none.
- Server emits `<time datetime="ISO-8601-UTC">`. Inline JS (~30 lines) converts to browser-local on page load and on `htmx:afterSwap`. Header clock updates every second. `<noscript>` fallback shows UTC explicitly.
- Files: `web/templates.py`, `static/js/local-time.js`, `templates/layouts/base.html`, tests.

### P0 — Documentation (deferred to project end)

These three PRs are P0 in priority but **scheduled at the very end of the cycle**, after P1 ships and the system is debugged. Documentation written before the system is stable would be wrong by the time the operator reads it.

**Reminder block — do not skip these.** Position in a calendar reminder for whenever P1 wave 4 (operator actions) merges:

#### PR-DOCS-01a — INSTALL.md
- ~400 lines markdown. Type: docs, Complexity: medium, Coder: any, Deps: all P1 merged.
- Linear, no branches. Pre-flight + 7 steps + verify + smoke tests.

#### PR-DOCS-01b — RUNBOOK.md + ARCHITECTURE.md
- ~500 lines markdown total. Type: docs, Complexity: medium, Coder: any.
- Operational tasks + one-page system overview.

#### PR-DOCS-01c — TROUBLESHOOTING.md
- ~400 lines markdown. Type: docs, Complexity: medium, Coder: any, Deps: PR-DOCS-01a.
- Symptom-to-cause table for failure modes encountered during deploy and likely future ones.

### P1 — Standard delivery (24 PRs)

#### PR-008 — Health endpoint
- ~120 lines. Type: feature, Complexity: low, Deps: PR-005.
- `GET /healthz`, JSON body, 200/503 responses, whitelisted in auth.

#### PR-MIGRATE-01 — Alembic auto-upgrade in systemd
- ~80 lines. Type: config, Complexity: low, Deps: none (referenced in eventual RUNBOOK).
- `scripts/preflight.sh` runs `alembic upgrade head` and DB-writable check; systemd `ExecStartPre=` invokes it.
- Files: `scripts/preflight.sh`, `deploy/megaraid-dashboard.service` (sample).

#### PR-PROXY-01 — nginx reverse proxy + TLS docs
- ~250 lines. Type: docs, Complexity: medium, Deps: PR-005, PR-008.
- nginx config sample for `server.alexbomber.com/raid/`, wildcard `*.alexbomber.com` cert reuse, `proxy_set_header X-Forwarded-Prefix /raid`, HSTS, security headers, `limit_req` zone for auth endpoint.
- Repo contains documentation and config samples; nginx itself lives outside repo (host-level config).
- Files: `docs/PROXY-SETUP.md`, sample nginx fragment.

#### PR-CSRF-01 — CSRF token middleware (cookie-based)
- ~250 lines. Type: feature, Complexity: medium, Deps: PR-005.
- Token via secure `__Host-csrf` cookie. Auto-injection into htmx headers via small JS shim. Protects all POST/PUT/DELETE/PATCH.
- Files: `web/middleware.py`, `static/js/csrf.js`, `templates/layouts/base.html`, tests.

#### PR-RATE-01 — Rate limiting on auth
- ~150 lines. Type: feature, Complexity: low, Deps: PR-005.
- slowapi (in-memory). 5 attempts/minute/IP on auth-required endpoints. 429 with Retry-After.
- Files: `app.py`, `config.py`, tests.

#### PR-DISK-01 — Disk space monitoring
- ~200 lines. Type: feature, Complexity: low, Deps: PR-005.
- Hourly check of `/var/lib/megaraid-dashboard` free space. Warning under 500 MB, critical under 100 MB.
- Files: `services/disk_monitor.py`, `services/scheduler.py`, `config.py`, tests.

#### PR-LOGROTATE-01 — Journald retention
- ~80 lines. Type: config, Complexity: low, Deps: none.
- `SystemMaxUse=200M` for the journald unit. Documentation sample.
- Files: `deploy/journald-megaraid.conf`, eventual RUNBOOK reference.

#### PR-009a — Design tokens + layout shell
- ~250 lines. Type: ux, Complexity: medium, Deps: PR-005, PR-006d, PR-007.
- New CSS tokens from §3.2; header strip; footer strip; layout grid skeleton.
- Files: `static/css/app.css`, `templates/layouts/base.html`.

#### PR-009b — StatusTile component + CSS
- ~200 lines. Type: ux, Complexity: low, Deps: PR-009a.
- Tile partial; SVG icon sprite inlined.
- Files: `templates/partials/status_tile.html`, `static/css/app.css`, `static/icons.svg`.

#### PR-009c — Dashboard status strip (6 tiles)
- ~250 lines. Type: ux, Complexity: medium, Deps: PR-009b.
- 6 tiles in one row: Controller / VD / RAID / BBU / MaxTemp / RoC. Replaces hero block.
- Files: `services/overview.py`, `templates/pages/overview.html`, tests.

#### PR-009d — Compact alert status row
- ~150 lines. Type: ux, Complexity: low, Deps: PR-009c.
- 4 cells horizontally instead of table layout.
- Files: `templates/partials/alert_status.html`, `static/css/app.css`, tests.

#### PR-009e — Recent activity timeline
- ~250 lines. Type: ux, Complexity: medium, Deps: PR-009d.
- Last 8 events; click on category navigates to filtered Events page.
- Files: `services/overview.py`, `templates/partials/activity_timeline.html`, `services/events.py`, tests.

#### PR-009f — Drop drive table from Dashboard
- ~80 lines. Type: refactor, Complexity: low, Deps: PR-009e.
- Drive table belongs on /drives; Dashboard summarizes.
- Files: `templates/pages/overview.html`, tests.

#### PR-009g — Drives page redesign
- ~300 lines. Type: ux, Complexity: medium, Deps: PR-009f.
- Dense data table with sortable columns, status indicators, slot links to drive detail.
- Files: `templates/pages/drives.html`, `templates/partials/physical_drive_table.html`, `static/css/app.css`, tests.

#### PR-009h — Events page redesign + auto-refresh fix
- ~250 lines. Type: ux, Complexity: medium, Deps: PR-009f.
- Timeline component everywhere. Auto-refresh no longer resets pagination position. Severity badges consistent.
- Files: `templates/pages/events.html`, `templates/partials/events_*.html`, `web/routes.py`, tests.

#### PR-010a — Polish: grammar + badge logic
- ~80 lines. Type: bugfix, Complexity: low, Deps: PR-009g.
- "1 drive elevated" singular case; mutual exclusion warning vs critical badges.
- Files: `services/overview.py`, tests.

#### PR-010b — Polish: slot links + tooltip
- ~80 lines. Type: ux, Complexity: low, Deps: PR-009g.
- Active slot links; RoC tooltip showing current vs thresholds.
- Files: `services/overview.py`, `templates/partials/physical_drive_table.html`, tests.

#### PR-INSTALL-01a — install.sh: pre-flight + user/dirs
- ~200 lines. Type: feature, Complexity: medium, Deps: PR-MIGRATE-01.
- Pre-flight checks (Ubuntu 24.04, Python 3.12, storcli, ports, network); create system user; create directories with correct ownership.
- Files: `scripts/install.sh`, tests.

#### PR-INSTALL-01b — install.sh: venv + pip install + smoke
- ~150 lines. Type: feature, Complexity: medium, Deps: PR-INSTALL-01a.
- Idempotent venv creation; pip install -e .; smoke tests (import, version).
- Files: `scripts/install.sh`, tests.

#### PR-INSTALL-01c — install.sh: config wizard (interactive)
- ~200 lines. Type: feature, Complexity: medium, Deps: PR-INSTALL-01b.
- Interactive prompts for admin password, SMTP host/user/token, ALERT_TO. Generate `.env` with mode 600.
- Files: `scripts/install.sh`, tests.

#### PR-INSTALL-01d — install.sh: sudoers + systemd + final smoke
- ~200 lines. Type: feature, Complexity: medium, Deps: PR-INSTALL-01c.
- sudoers fragment; systemd unit; alembic upgrade; web port responds; final summary print. `scripts/uninstall.sh` companion.
- Files: `scripts/install.sh`, `scripts/uninstall.sh`, tests.

#### PR-011a — Drive locate LED route + command builder
- ~250 lines. Type: feature, Complexity: medium, Deps: PR-005, PR-CSRF-01.
- `POST /drives/{e}:{s}/locate/start` and `.../stop`. Strict integer validation. Storcli command builder with whitelist.
- Files: `web/routes.py`, `services/drive_actions.py`, tests.

#### PR-011b — Drive locate LED UI button
- ~150 lines. Type: ux, Complexity: low, Deps: PR-011a.
- Button with confirmation dialog; htmx integration; spinner-on-pending.
- Files: `templates/pages/drive_detail.html`, `static/js/drive-actions.js`, tests.

#### PR-011c — Operator action audit category
- ~150 lines. Type: feature, Complexity: low, Deps: PR-011a.
- New event category `operator_action`; new nullable column `events.operator_username`; migration 0005.
- Files: `db/models.py`, `migrations/versions/0005_*.py`, `services/event_detector.py`, tests.

#### PR-013 — Audit log filter on Events page
- ~150 lines. Type: feature, Complexity: low, Deps: PR-011c, PR-009h.
- Filter chip "operator_action" on Events page. Optional `/audit` route filters by category.
- Files: `web/routes.py`, `services/events.py`, `templates/pages/events.html`, tests.

#### PR-012a — Drive replace Step 1 backend (offline + missing)
- ~250 lines. Type: feature, Complexity: high, Coder: claude, Deps: PR-011a, PR-013.
- Step 1 only — `set offline` then `set missing`. State machine. Command builder rejects anything outside template.
- **Risk: HIGH.** Hand-verification of storcli commands mandatory before merge.
- Files: `web/routes.py`, `services/drive_actions.py`, tests.

#### PR-012b — Drive replace Step 1 UI
- ~200 lines. Type: ux, Complexity: medium, Deps: PR-012a.
- Multi-step UI with explicit confirm screen; cancel default focus; dry-run mode.
- Files: `templates/pages/drive_detail.html`, JS, CSS, tests.

#### PR-012c — Drive replace Step 2-3 (insert + replace missing)
- ~300 lines. Type: feature, Complexity: high, Coder: claude, Deps: PR-012b.
- **Risk: HIGH.** Mandatory hand-verification + real hardware test before deployment.
- Files: `web/routes.py`, `services/drive_actions.py`, tests.

#### PR-012d — Drive replace Step 4 (rebuild progress polling)
- ~200 lines. Type: feature, Complexity: medium, Deps: PR-012c.
- Polling endpoint; UI progress percent.
- Files: `web/routes.py`, `services/drive_actions.py`, template, tests.

#### PR-014a — Maintenance mode storage
- ~150 lines. Type: feature, Complexity: low, Deps: PR-005.
- `system_state` key-value table; migration 0006; DAO.
- Files: `db/models.py`, `migrations/versions/0006_*.py`, `db/dao.py`, tests.

#### PR-014b — Maintenance mode endpoints + notifier integration
- ~200 lines. Type: feature, Complexity: medium, Deps: PR-014a, PR-011c.
- Start/stop endpoints; notifier respects window; auto-expiration. Audit events.
- Files: `web/routes.py`, `services/notifier.py`, tests.

#### PR-014c — Maintenance mode UI banner
- ~150 lines. Type: ux, Complexity: low, Deps: PR-014b.
- Banner on Dashboard with stop button; visible across all pages.
- Files: `templates/layouts/base.html`, banner partial, CSS, tests.

### P1.5 — Observability stack (11 PRs)

Lives in a separate repository: `homelab-monitoring`. Megaraid-dashboard exposes metrics; the stack consumes them. Decision: separate repo because the same stack will serve sms-relay and other future products.

#### PR-MON-01a — Prometheus exporter scaffold
- ~150 lines. Type: feature, Complexity: medium, Deps: PR-005.
- `prometheus_client` dependency; `GET /metrics` on separate port 8091; LAN-only firewall rule.
- Files: `pyproject.toml`, `web/metrics.py`, tests.

#### PR-MON-01b — Drive metrics
- ~200 lines. Type: feature, Complexity: low, Deps: PR-MON-01a.
- `megaraid_drive_temperature_celsius`, `megaraid_physical_drive_state`, `megaraid_virtual_drive_state`.
- Files: `web/metrics.py`, tests.

#### PR-MON-01c — Controller metrics
- ~200 lines. Type: feature, Complexity: low, Deps: PR-MON-01a.
- `megaraid_controller_health`, `megaraid_controller_roc_temperature_celsius`, `megaraid_cv_capacitance_percent`.
- Files: `web/metrics.py`, tests.

#### PR-MON-01d — Event + alert metrics
- ~250 lines. Type: feature, Complexity: medium, Deps: PR-MON-01c.
- `megaraid_events_total{severity, category}`, `megaraid_alerts_sent_total`, `megaraid_collector_cycle_duration_seconds`, `megaraid_collector_last_run_timestamp`.
- Files: `web/metrics.py`, `services/scheduler.py`, tests.

#### PR-MON-02a — homelab-monitoring repo bootstrap
- ~250 lines. Type: config, Complexity: medium, Deps: PR-MON-01d.
- New repo. Docker Compose. README in INSTALL.md style.

#### PR-MON-02b — Prometheus scrape config + retention
- ~150 lines. Type: config, Complexity: low, Deps: PR-MON-02a.
- 90-day retention; megaraid-dashboard scrape target; placeholder for sms-relay.

#### PR-MON-02c — Grafana provisioning + datasource
- ~200 lines. Type: config, Complexity: medium, Deps: PR-MON-02b.
- File-based provisioning; Prometheus datasource configured.

#### PR-MON-02d — Caddy + TLS + basic auth
- ~250 lines. Type: config, Complexity: medium, Deps: PR-MON-02c.
- Caddy reverse proxy in front of Grafana; automatic HTTPS; basic auth.

#### PR-MON-03a — Grafana Overview dashboard
- ~300 lines JSON. Type: ux, Complexity: medium, Deps: PR-MON-02c.
- Single-screen: controller health, RoC temp, max disk temp, VD state, CV capacitance.

#### PR-MON-03b — Temperature trends dashboard
- ~250 lines JSON. Type: ux, Complexity: medium, Deps: PR-MON-03a.
- 24h / 7d / 30d charts; per-drive; RoC overlay.

#### PR-MON-03c — Events + capacity dashboards
- ~300 lines JSON. Type: ux, Complexity: medium, Deps: PR-MON-03a.
- Events analytics; capacity & wear.

### Security audit (2 PRs after P1 completes)

#### PR-SEC-01 — Audit pass #1 (codebase + config + deps)
- ~300 lines documentation + remediation MICRO PRs as findings dictate.
- Type: docs, Complexity: medium, Deps: all P1 merged.
- Threat model document; codebase review (SQL injection / command injection / path traversal / XSS / auth bypass); configuration audit (.env permissions, sudoers narrow, systemd hardening, sqlite ACLs); `pip-audit` results. Findings filed as MICRO PRs.
- Files: `docs/SECURITY-AUDIT-2026-Q2.md`, `docs/THREAT-MODEL.md`.

#### PR-SEC-02 — Audit pass #2 (manual penetration)
- ~250 lines documentation + remediation MICRO PRs.
- Type: docs, Complexity: medium, Deps: PR-SEC-01.
- Curl-based attacks: malformed inputs, injection attempts, auth bypass, CSRF without header, rate limit bypass, audit log integrity.
- Files: `docs/SECURITY-AUDIT-2026-Q2-pen.md`.

### Documentation (3 PRs at the very end)

These are the P0-priority docs deferred to project end. **Calendar reminder for whenever security audit PR-SEC-02 merges** — without these the system is one-author-locked.

#### PR-DOCS-01a — INSTALL.md (see P0 above for full description).
#### PR-DOCS-01b — RUNBOOK.md + ARCHITECTURE.md.
#### PR-DOCS-01c — TROUBLESHOOTING.md.

### P2 — Optional / deferred (8 PRs)

#### PR-VERSION-01 — Versioning + reproducible builds
- ~150 lines. Type: config, Complexity: low.

#### PR-COMMUNITY-01 — Open source readiness
- ~250 lines. Type: docs, Complexity: low.
- CONTRIBUTING.md, CODE_OF_CONDUCT.md, issue templates, PR template.

#### PR-PRECOMMIT-01 — Pre-commit hooks
- ~80 lines. Type: config, Complexity: low.

#### PR-016 — HTML email alternative
- ~200 lines. Type: feature, Complexity: low.
- After PR-MON-03 lands, possibly redundant.

#### PR-017 — Mobile layout
- ~300 lines CSS. Type: ux, Complexity: medium.

#### PR-018 — Foreign config import workflow
- ~600 lines (split if scheduled). Type: feature, Complexity: high.
- Even higher risk than drive replace.

#### PR-019 — Patrol read scheduling
- ~150 lines. Type: feature, Complexity: low.

#### PR-020 — Consistency check scheduling
- ~150 lines. Type: feature, Complexity: low.

## 6. Manual / runbook items (not PRs)

- **Backup script** — daily cron on NAS. `sqlite3 .backup` to `/mnt/backup/megaraid-dashboard/`. Encrypt `.env` backup separately. 30-day retention. Quarterly restore drill.
- **DNS strategy follow-up** — Mullvad DNS for all VPN endpoints (decision pending in separate chat).
- **RoC heatsink installation** — when 40×40 mm kit arrives. Document before/after temps in `docs/hardware-mods.md`. Lower thresholds to 80/95 after install.
- **NTP verification** — confirm `systemd-timesyncd` active on NAS. Already part of pre-flight in PR-INSTALL-01a.
- **Quarterly security review** — re-run PR-SEC-01 checks every quarter; document deltas.
- **Pre-commit installation on developer machines** — `pip install pre-commit && pre-commit install` once per dev environment.

## 7. Open questions for the operator

1. PR-009 layout: confirm or amend the 6-tile status strip and timeline below.
2. PR-011 locate-LED: verify storcli command syntax on this controller version on hardware before merge.
3. PR-012 replace flow: confirm replacement always at same enclosure:slot, or hot-spare promotion preferred.
4. PR-014 maintenance mode: pause notifier only; collector continues recording.
5. PR-MON-04 (Alertmanager): skipped — defense in depth not justified for homelab.
6. URL: `server.alexbomber.com/raid/` (path-based proxy).
7. CSRF: cookie-based token (industry standard).

## 8. Definition of done

The product is complete when:
- All P0 (excluding deferred docs) merged and deployed.
- All P1 merged and deployed.
- Observability stack (PR-MON-01..03) operational and producing dashboards.
- Security audit PR-SEC-01 and PR-SEC-02 complete; all findings remediated.
- PR-DOCS-01a/b/c written **after** debug period.
- INSTALL.md validated by fresh-eyes test (operator reproduces deploy in under 60 minutes).
- Backup runbook executed at least once.
- DNS strategy decided and applied.
- Heatsink installed; RoC thresholds lowered to spec.
- One operator action (locate or replace) successfully exercised on real hardware end-to-end.
- Final dashboard screenshot in `docs/screenshots/`.

## 9. Document hygiene

This roadmap is a living document. Update it when:
- A PR lands — add merge date and PR number link in the relevant section.
- Priority shifts — move the entry between tiers.
- A new requirement emerges — add to the relevant tier.
- An assumption proves wrong — correct in place; do not delete history.

Quarterly review: assess deferred P2 items, new requirements, design system evolution.

## 10. Deferred docs reminder

**Important reminder block** — PR-DOCS-01a/b/c are P0-priority documents deferred to project end. They are easy to forget because no code blocker depends on them. Without these, the system is one-author-locked.

Schedule trigger: when PR-SEC-02 merges, immediately queue PR-DOCS-01a, b, c. Do not ship the system to anyone external until all three exist.

## 11. Counts

| Tier | PR count | Approx LoC | Where |
|------|----------|------------|-------|
| P0 (code) | 6 | ~850 | this repo |
| P0 (docs, deferred) | 3 | ~1300 | this repo |
| P1 | 24 | ~4250 | this repo |
| P1.5 | 11 | ~2700 | homelab-monitoring repo |
| Security | 2 | ~550 | this repo |
| P2 | 8 | ~1880 | this repo |
| **Total** | **54** | **~11500** | |
