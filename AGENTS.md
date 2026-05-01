# Mission

Build a sustainable, maintainable replacement for MSM that survives kernel and OS upgrades because it talks to `storcli` over a stable JSON contract instead of legacy ioctls.

# Workflow Rules

- All changes go through PRs targeting `main`.
- No direct commits to `main`.
- PR titles and commit messages follow Conventional Commits: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`, `ci`, `perf`.
- For ad-hoc human-driven branches: use Conventional Commit prefixes plus a short kebab-case description (e.g. `feat/add-controller-status`).
- For pipeline-orchestrator daemon-driven PRs: see Branch naming section below for `pr-XXX-slug` and `micro-YYYYMMDD-slug` patterns.
- CI must be green before merge.
- Approving reviews are not required because this is a solo repo.

# Code Style

- `ruff` is the only formatter and linter.
- `mypy` runs in strict mode.
- Add type hints on all public functions and methods.
- Line length is 100.
- Prefer explicit over clever.
- Do not use `print` statements in library code; use `structlog`.

# Testing

- Use `pytest` with asyncio mode `auto`.
- Tests must never call real `storcli` or touch real hardware; mock the `storcli` wrapper at the boundary.
- Aim for coverage growth, with no hard percentage gate yet. (To enforce a gate, add `--cov-fail-under=N` to pyproject.toml `[tool.pytest.ini_options]` `addopts` and update `scripts/ci.sh` to mirror.)
- Integration tests live under `tests/integration/` and are skipped by default with a marker.

# Architecture Rules

- Every `storcli` invocation goes through the dedicated wrapper module in `src/megaraid_dashboard/storcli/`.
- The wrapper always uses `storcli -J` JSON output and never parses textual tables.
- Outputs are validated into pydantic models.
- Do not make subprocess calls to `storcli` outside that wrapper.
- Use SQLAlchemy with Alembic migrations.
- Keep FastAPI routes thin, with business logic in service modules.

# Security Model

- Do not put secrets in code; load all config from `.env` via `pydantic-settings`.
- The web app runs as the dedicated unprivileged user `raid-monitor`.
- `sudo` is allowed only through a narrow sudoers entry that whitelists exact `storcli` commands.
- The web layer is protected by HTTP basic auth via nginx in front of FastAPI.
- The app starts in read-only mode by default.
- Write operations require an explicit `maintenance_mode` flag toggled in the UI.
- Destructive operations such as set offline, set missing, and force rebuild require an additional `destructive_mode` flag plus typed confirmation of the affected drive serial number.
- Every write and destructive operation is recorded in an audit table with timestamp, actor, command, and result.

# Hardware Target

- LSI MegaRAID SAS 9270CV-8i, chip SAS 2208, firmware 23.34.0-0019.
- `megaraid_sas` driver 07.727.x on kernel 6.8.
- Production host `192.168.50.2` running Ubuntu 24.04.
- `storcli64` at `/usr/local/sbin/storcli64`.

# Out Of Scope (For Now)

- Multi-controller support.
- SAS expander topology beyond a single enclosure.
- Non-MegaRAID controllers.
- ZFS or mdraid.
- Mobile-first UI.

# Communication

If a change touches the security model, the `storcli` wrapper contract, or the database schema, the PR description must explicitly call this out under a Risk heading.

# Note about pipeline-orchestrator daemon-managed sections

The sections below this note are appended automatically by the pipeline-orchestrator daemon when this repository is onboarded. They describe **how the daemon dispatches and reviews work** — operational protocol shared across all repos managed by the orchestrator. They do not override project-specific rules above.

If a daemon-managed section appears to contradict a project-specific rule (mission, code style, architecture, security, hardware target), **the project-specific rule takes precedence for project domain decisions**. The daemon-managed sections describe how the orchestrator operates around the project, not what the project itself does.

Project-specific equivalents for orchestrator-level concepts:

- Integration tests in this project: `tests/integration/`, skipped by default via marker.
- This project has no external testbed repository.
- CI workflow: `.github/workflows/ci.yml` running `ruff check`, `ruff format --check`, `mypy src`, and `pytest`. No coverage threshold currently enforced (pyproject.toml has `--cov=src/megaraid_dashboard --cov-report=term-missing` but no `--cov-fail-under`).
- This project has no `task_status.py` module; it is not part of pipeline-orchestrator.

<!-- pipeline-orchestrator: managed BEGIN work_modes -->
## Work Modes
Exact trigger phrases:
- `PLANNED PR`
- `MICRO PR: <one sentence description>`
- `FIX FEEDBACK`

Meaning:
- `PLANNED PR`: the default mode. Use the active entry in `tasks/QUEUE.md` to locate the corresponding `tasks/PR-*.md` file, then work strictly from that task file.
- `MICRO PR: ...`: a tiny change. Do not touch `tasks/QUEUE.md` and do not create `tasks/PR-*.md`.
- `FIX FEEDBACK`: apply fixes based on CI failures and/or review feedback on an existing PR branch. The daemon injects the latest CI failure logs (last 5000 chars) and the Codex feedback comments posted after the most recent `@codex review` anchor (the same source that drives `ReviewStatus.CHANGES_REQUESTED`) directly into the prompt, so the coder receives that context inline; the coder may still fetch additional context via `gh` CLI when needed.
<!-- pipeline-orchestrator: managed END work_modes -->

<!-- pipeline-orchestrator: managed BEGIN daemon_mode -->
## Daemon Mode

When triggered by the pipeline orchestrator daemon (non-interactive):
- Do not ask for confirmation or clarification.
- If something is unclear, commit what you have and note the ambiguity in the PR description.
- Log all decisions to stdout for the daemon to capture.
- NEVER use `git add -f` or explicitly stage files matched by .gitignore.
- Artifacts (ci.log, pr.patch, structure.txt) are generated for Codex review but must not appear in commits. The .gitignore must exclude them.
- NEVER commit .patch files to the repository under any circumstances.
<!-- pipeline-orchestrator: managed END daemon_mode -->

<!-- pipeline-orchestrator: managed BEGIN ci_gates -->
## CI gates and merge criteria

The orchestrator enforces a merge contract: a PR is merge-eligible when all of the following are true:

1. CI workflow on the project's main branch is green (the project defines its own CI workflow; the orchestrator only reads the resulting check status).
2. Codex review +1 is valid (non-stale per the review anchor rules in the Codex Review gate section).

The daemon's auto-merge logic reads check status from the GitHub API. The project's CI workflow is responsible for running tests, linting, coverage gates, and any project-specific gates. The orchestrator does not require any specific CI tool, language, or test framework.

Failure handling: if a CI check fails, the coder enters FIX FEEDBACK mode and addresses the failure based on the CI logs. CI failures must be fixed by code change, never by disabling or skipping the failing check. If a check is genuinely flaky and the failure is not reproducible, the human reviewer may rerun the failed job manually via the CI UI.

The coder is responsible for the unit-level CI run (typically `pytest`, `ruff`, `mypy`, or equivalents per the project's stack) being green locally before opening the PR. The CI service then validates the same in a clean environment. Drift between local and CI environments is detected at this point.
<!-- pipeline-orchestrator: managed END ci_gates -->

<!-- pipeline-orchestrator: managed BEGIN codex_review_gate -->
## Codex Review gate (GitHub PR)

The daemon reads Codex Review state via the GitHub API to determine whether a PR is approved.

A Codex Review is **valid** for merge when all of the following are true:

1. Codex posted a review comment with `+1` thumbs-up reaction or explicit approval text after the latest push.
2. The review is non-stale: it was posted after the last push SHA and is anchored to the current head commit.
3. No subsequent CHANGES_REQUESTED review is open.

The daemon waits for `@codex review` mention to be posted by the coder after every push. If Codex review is stale (posted before the most recent push), the daemon reposts the mention. If Codex never responds within the configured timeout window, the daemon escalates to HUNG state.

A coder must NEVER post `@codex review` from their own automation; the daemon handles that. The coder posts the mention exactly once when they first open the PR via `gh pr create`.
<!-- pipeline-orchestrator: managed END codex_review_gate -->

<!-- pipeline-orchestrator: managed BEGIN escalate_protocol -->
## ESCALATE protocol (when coder cannot fix)

If during a `FIX FEEDBACK` cycle you determine that the failure is genuinely
outside your mandate to fix, output an ESCALATE marker on the LAST
non-empty line of stdout. The marker format is exactly:

  `ESCALATE: <one-line reason>`

The match is strict and case-sensitive. Variants such as `escalate:`,
`ESCALATED:`, or an ESCALATE line that is not the last non-empty line of
stdout do NOT trigger the protocol. An empty reason
(`ESCALATE:` alone) is accepted and the daemon substitutes
`(no reason provided)`.

Examples of reasons that warrant ESCALATE (MUST):

- CI logs show "rate limit exceeded" for the GitHub API.
- CI logs show DNS resolution failure or network unreachable.
- CI logs show "secret not found" or auth errors with GitHub.
- The required test fix needs production-code changes outside the stated
  PR scope (the task's "Files NOT to touch" list).
- Task spec contradicts existing code architecture (ambiguity, not a bug).
- Three attempts at the same fix failed with the same error — likely the
  root cause is not what you assumed.

Examples of reasons that do NOT warrant ESCALATE (just fix):

- Banal syntax errors or typos.
- Test assertion failures with a clear, in-scope root cause.
- Linting or formatting issues.
- Missing imports or simple type errors.

Examples that MAY warrant ESCALATE (use judgment):

- Test passes locally but fails in CI consistently → infra suspect.
- Coverage check fails for code already tested elsewhere → tooling issue.
- Behavior matches spec but spec was wrong → architectural decision needed.

After ESCALATE, the daemon will:

1. Post a comment on the PR with your reason text.
2. Apply the `escalated` label to the PR.
3. Transition the runner to IDLE and stop further FIX cycles on this PR.
4. Wait for human review before any further action.

To resume work after a human resolves the issue, click Resume in the
dashboard or close + reopen the PR. The `escalated` label and the
`is_escalated` flag are cleared at that point.

This is the SEMANTIC complement to the MECHANICAL no-push circuit breaker:
the no-push breaker catches stuck coders by counting non-productive cycles;
ESCALATE catches them by listening to an explicit self-report. Both feed
the same downstream parking machinery.
<!-- pipeline-orchestrator: managed END escalate_protocol -->

<!-- pipeline-orchestrator: managed BEGIN branch_naming -->
## Branch naming
- PLANNED: use `Branch:` from the active `tasks/PR-*.md` as the source of truth.
- If `Branch:` is missing, use `pr-<sanitized-pr-id>`:
  - lowercase
  - replace `.` with `-`
  - allow only `[a-z0-9-]`
- MICRO: `micro-YYYYMMDD-<short-slug>`

Note: this naming convention applies only to daemon-driven PRs. Human-driven branches in this repo follow the project's own Conventional Commit prefix style (see Workflow Rules section above).
<!-- pipeline-orchestrator: managed END branch_naming -->

<!-- pipeline-orchestrator: managed BEGIN planned_pr_runbook -->
## PLANNED PR runbook (queue-selected)

### Rules
- Preflight: `git status --porcelain` must be empty. If not, stop and list dirty files.
- Use the active entry in `tasks/QUEUE.md` to determine `PR_ID` and `TASK_FILE`. Do not guess or select a different task locally.
- Read `TASK_FILE` fully before coding.
- Create the branch from `origin/main`.
- Implement only the scope defined in `TASK_FILE`. No extra refactors, upgrades, or bundled features.
- During the PR, do not edit `tasks/PR-*.md` unless the user explicitly requests it.
- The project's CI script and review artifacts are mandatory.
- **PR state:** PRs must be created in ready state, not draft. Use `gh pr create` without `--draft` flag. If accidentally created as draft (e.g. by mistake), run `gh pr ready <PR_NUMBER>` immediately to convert.

### Checklist
- [ ] Preflight clean
- [ ] Recorded `PR_ID` and `TASK_FILE` from the active `tasks/QUEUE.md` entry
- [ ] Read `TASK_FILE`
- [ ] `git fetch origin main` and created branch from `origin/main`
- [ ] Implemented only `TASK_FILE` scope
- [ ] Ran the project's CI script to exit 0 (typically `scripts/ci.sh` or equivalent)
- [ ] Generated review artifacts (e.g. `artifacts/ci.log`, `artifacts/pr.patch`, `artifacts/structure.txt`); these are not committed and must be excluded by `.gitignore`
- [ ] Commit message: `<PR_ID>: <short summary>`
- [ ] Pushed branch
- [ ] Created PR via GitHub CLI (`gh`) or provided manual PR steps
- [ ] Posted `@codex review` comment on the PR (body must be exactly `@codex review`, no other text, prefix, or artifact list)
- [ ] Final report prepared (see below)

### Final report (PR description only)
- PR_ID
- TASK_FILE
- Branch
- What changed (1-5 bullets)
- How verified (exact command)
- Artifacts list (if applicable)
- Manual test steps (if applicable)
<!-- pipeline-orchestrator: managed END planned_pr_runbook -->

<!-- pipeline-orchestrator: managed BEGIN micro_pr_runbook -->
## MICRO PR runbook

### Eligibility (all must be true)
- <= 3 files changed
- <= 100 lines changed (excluding lockfile noise)
- no DB migrations/schema changes
- no dependency upgrades
- no auth/permissions changes
- no large refactors or sweeping formatting

If any condition fails, MICRO is not allowed. Use PLANNED PR.

### Rules
- Do not create `tasks/PR-*.md`
- Do not edit `tasks/QUEUE.md` (auto-generated; manual edits are overwritten on next IDLE cycle)
- **PR state:** PRs must be created in ready state, not draft. Use `gh pr create` without `--draft` flag. If accidentally created as draft (e.g. by mistake), run `gh pr ready <PR_NUMBER>` immediately to convert.

### Checklist
- [ ] Preflight clean
- [ ] `git fetch origin main`
- [ ] Branch `micro-YYYYMMDD-<short-slug>` from `origin/main`
- [ ] Only the requested change
- [ ] Ran the project's CI script to exit 0
- [ ] Generated review artifacts (not committed, excluded by `.gitignore`)
- [ ] Commit: `MICRO: <short summary>`
- [ ] Posted `@codex review` comment on the PR (body must be exactly `@codex review`, no other text, prefix, or artifact list)
- [ ] Pushed branch and opened PR
<!-- pipeline-orchestrator: managed END micro_pr_runbook -->

<!-- pipeline-orchestrator: managed BEGIN review_fix_runbook -->
## REVIEW FIX runbook (existing PR branch)
- Do not select a new task from `tasks/QUEUE.md` (auto-generated; manual edits are overwritten on next IDLE cycle)
- Do not create a new branch
- Stay on the existing PR branch
- Do not edit `tasks/QUEUE.md` (auto-generated; manual edits are overwritten on next IDLE cycle) or `tasks/PR-*.md`
- Fix only the review comments
- Use the Codex Review gate above and stop only when a non-stale Codex thumbs up is present
- Run the project's CI script to exit 0
- Generate review artifacts (not committed, excluded by `.gitignore`)
- Commit and push to the same PR branch
- **PR state:** PRs must be created in ready state, not draft. Use `gh pr create` without `--draft` flag. If accidentally created as draft (e.g. by mistake), run `gh pr ready <PR_NUMBER>` immediately to convert.
<!-- pipeline-orchestrator: managed END review_fix_runbook -->

<!-- pipeline-orchestrator: managed BEGIN queue_stability_rules -->
## Queue stability rules (PLANNED PR only)
- `tasks/PR-*.md` files are the source of truth; `tasks/QUEUE.md` is a derived artifact.
- Do not rewrite tasks retroactively during a PR.
- If the user updates `tasks/` while you are working, stop and ask for explicit direction: continue as-is, incorporate changes, or revert.
<!-- pipeline-orchestrator: managed END queue_stability_rules -->
