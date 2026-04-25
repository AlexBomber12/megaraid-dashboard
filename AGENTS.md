# Mission

Build a sustainable, maintainable replacement for MSM that survives kernel and OS upgrades because it talks to `storcli` over a stable JSON contract instead of legacy ioctls.

# Workflow Rules

- All changes go through PRs targeting `main`.
- No direct commits to `main`.
- PR titles and commit messages follow Conventional Commits: `feat`, `fix`, `chore`, `refactor`, `docs`, `test`, `ci`, `perf`.
- Branch names use the same prefixes plus a short kebab-case description.
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
- Aim for coverage growth, with no hard percentage gate yet.
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
