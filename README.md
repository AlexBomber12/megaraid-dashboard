# MegaRAID Dashboard

MegaRAID Dashboard is a web dashboard and email alerter for LSI MegaRAID controllers, JSON-driven via `storcli`, intended as a sustainable replacement for the unmaintained MegaRAID Storage Manager (MSM).

## Why

MSM has been unmaintained since 2018 and is broken on modern Linux kernels. `storcli` is supported by Broadcom and is stable across kernel and OS upgrades.

## Hardware Tested

- LSI MegaRAID SAS 9270CV-8i (chip SAS 2208)
- Ubuntu 24.04
- Kernel 6.8
- `megaraid_sas` driver

## Requirements

- Python 3.12
- `storcli64` in `PATH`
- MegaRAID controller accessible to the host
- `sudo` with a whitelist of `storcli` commands when write operations are enabled

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m megaraid_dashboard
```

## Development

```bash
ruff check .
ruff format .
mypy src
pytest
```

## Project Layout

```text
.
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- src/
|   `-- megaraid_dashboard/
|       |-- __init__.py
|       |-- __main__.py
|       |-- app.py
|       |-- config.py
|       `-- storcli/
|           |-- __init__.py
|           |-- exceptions.py
|           |-- models.py
|           |-- parser.py
|           `-- runner.py
|-- tests/
|   |-- fixtures/
|   |   `-- storcli/
|   |       |-- redact.py
|   |       `-- redacted/
|   |           |-- bbu_show_all.json
|   |           |-- c0_show_all.json
|   |           |-- cv_show_all.json
|   |           |-- eall_sall_show_all.json
|   |           `-- vall_show_all.json
|   |-- test_storcli/
|   |   |-- __init__.py
|   |   |-- test_parser.py
|   |   |-- test_redactor.py
|   |   `-- test_runner.py
|   |-- __init__.py
|   |-- test_config.py
|   `-- test_smoke.py
|-- .env.example
|-- .gitignore
|-- AGENTS.md
|-- CLAUDE.md
|-- LICENSE
|-- README.md
`-- pyproject.toml
```

## Roadmap

1. [x] Skeleton and CI.
2. [ ] `storcli` wrapper with JSON parsing and pydantic models. (in progress)
3. SQLite schema and migrations.
4. Background metrics collector.
5. Read-only web dashboard.
6. Email alerts via SMTP.
7. Basic auth.
8. Maintenance mode for locate LED, alarm, patrol read, and consistency check.
9. Destructive mode for drive replace workflow.
10. Production deployment with systemd and nginx.

## Status

Active development, not production-ready yet.
