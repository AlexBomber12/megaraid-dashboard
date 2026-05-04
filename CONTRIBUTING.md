# Contributing

MegaRAID Dashboard is a small, hardware-adjacent project. Contributions are welcome when
they keep the core contract intact: the application talks to MegaRAID hardware only through
`storcli -J`, validates JSON into typed models, and keeps unsafe operations behind explicit
operator controls.

Before opening a change, read [AGENTS.md](AGENTS.md) for the project rules and
[INSTALL.md](INSTALL.md) for production environment assumptions.

## Reporting bugs

Use the bug report issue template when something behaves incorrectly.

Include:

- the exact command or page that failed;
- the expected behavior;
- the actual behavior;
- relevant logs or traceback snippets;
- whether the failure happened in development, CI, or on a production host;
- sanitized `storcli -J` fixture data when the bug is in parsing or status detection.

Do not paste secrets, SMTP credentials, basic-auth passwords, private IP inventories, or
unredacted serial numbers from production hardware. If hardware output is needed, redact it
first and keep the JSON shape intact.

Security-sensitive issues should not be filed with exploit details in a public issue. Open a
minimal issue asking for a private contact path, or use GitHub's private vulnerability
reporting flow if it is enabled for the repository.

## Suggesting features

Use the feature request issue template for new behavior.

Good feature requests describe:

- the operational problem being solved;
- the controller state or operator workflow involved;
- whether the feature is read-only or performs a write operation;
- which `storcli -J` command would provide the data or perform the action;
- what safety controls are needed for maintenance or destructive workflows.

The current project scope is intentionally narrow. Multi-controller support, non-MegaRAID
controllers, SAS expander topology, ZFS, mdraid, and mobile-first UI are out of scope for
now.

## Setting up dev env

Use Python 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Pre-commit setup

Pre-commit is optional for local development:

```bash
pip install pre-commit
pre-commit install
```

The hooks run on `git commit` and mirror fast local checks. CI remains the canonical
validation gate for pull requests.

Run the app locally with:

```bash
python -m megaraid_dashboard
```

The development UI listens on <http://127.0.0.1:8090/>.

Development and tests must not require real hardware. Tests should mock the `storcli`
wrapper boundary or use redacted JSON fixtures under `tests/fixtures/storcli/`.

Production setup is documented in [INSTALL.md](INSTALL.md). That guide explains the
expected unprivileged service user, `.env` configuration, systemd unit, sudoers allowlist,
nginx proxy, and smoke checks. Development changes should stay compatible with those
deployment assumptions.

## Code style

`ruff` is the only formatter and linter.

```bash
ruff check .
ruff format .
```

Keep line length at 100 characters. Prefer explicit code over clever code. Public functions
and methods need type hints, and `mypy` runs in strict mode.

Do not use `print` statements in library code. Use `structlog` when runtime logging is
needed.

FastAPI routes should stay thin. Put business rules in service modules, keep data access in
the database layer, and keep every `storcli` invocation inside `src/megaraid_dashboard/storcli/`.
The wrapper must use JSON output and must not parse text tables.

## Testing

Run the same checks that CI runs:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

The project uses `pytest` with asyncio mode `auto`.

Tests must never call real `storcli`, require a MegaRAID controller, or touch production
hardware. Mock the wrapper boundary and use fixtures for parser and service behavior.

Integration tests live under `tests/integration/` and are skipped by default with a marker.
Only add integration coverage when the behavior genuinely crosses module or process
boundaries.

When changing database behavior, include the SQLAlchemy model change, Alembic migration, and
focused tests. When changing the security model, include coverage for the safety control and
call out the risk in the pull request.

## Submitting a PR

All changes go through pull requests targeting `main`. Do not commit directly to `main`.

Use Conventional Commits for branch names, commit messages, and PR titles when creating
human-driven changes:

```text
feat/add-controller-status
fix/event-filter-empty-state
docs/update-runbook
```

Keep PRs focused. Avoid mixing feature work, refactors, formatting churn, and dependency
updates in one review unless the task explicitly requires it.

Before opening a PR:

- sync with `main`;
- run the full local check set;
- confirm no generated artifacts, `.env` files, logs, patches, or hardware dumps are staged;
- update docs when behavior, configuration, deployment, or operator workflow changes.

The PR body should include:

- `PR_ID` or task reference when one exists;
- what changed;
- how it was verified;
- manual test steps when useful;
- a `Risk` heading when the change touches the security model, the `storcli` wrapper
  contract, or the database schema.

CI must be green before merge. Approving human reviews are not required for this solo repo,
but review feedback and Codex review comments should be addressed before merging.

## Working with the orchestrator

Some PRs are dispatched by the pipeline orchestrator. The daemon-managed runbooks live in
[AGENTS.md](AGENTS.md), including:

- `PLANNED PR` for queue-selected task files;
- `MICRO PR: <description>` for tiny changes;
- `FIX FEEDBACK` for CI or review fixes;
- branch naming, artifact generation, and review-gate rules.

Those sections describe how automated work is dispatched and reviewed. Project-specific
rules in the top half of [AGENTS.md](AGENTS.md) still control architecture, security,
testing, style, and hardware assumptions.
