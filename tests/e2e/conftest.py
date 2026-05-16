"""End-to-end test infrastructure.

Fixtures:

* ``live_server`` (session-scoped): one uvicorn-hosted FastAPI app shared across
  every test in the session. Yields the base URL. Tests that need clean DB state
  reset via ``fresh_db``, not by restarting the server.
* ``fresh_db`` (function-scoped): per-test SQLite database with ``alembic upgrade head``
  applied. Yields the SQLAlchemy URL.
* ``test_admin_creds`` (function-scoped): admin credentials plus an env file for
  tests that need to drive auth flows. The credentials are stable across the
  session so they also match what ``live_server`` was started with.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import bcrypt
import pytest
import uvicorn

E2E_ADMIN_USERNAME = "e2e-admin"
E2E_ADMIN_PASSWORD = "e2e-test-pass-1234"
E2E_ADMIN_PASSWORD_HASH = bcrypt.hashpw(
    E2E_ADMIN_PASSWORD.encode(),
    bcrypt.gensalt(rounds=4),
).decode()

_SERVER_STARTUP_POLL_INTERVAL_SECONDS = 0.1
_SERVER_STARTUP_TIMEOUT_SECONDS = 10.0
_SERVER_SHUTDOWN_TIMEOUT_SECONDS = 10.0
_STORCLI_STUB_SCRIPT = (
    '#!/usr/bin/env bash\nprintf \'{"Controllers": [{"Response Data": {}}]}\\n\'\nexit 0\n'
)
_STORCLI_STUB_MODE = 0o755


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_storcli_stub(path: Path) -> Path:
    path.write_text(_STORCLI_STUB_SCRIPT, encoding="utf-8")
    path.chmod(_STORCLI_STUB_MODE)
    return path


def _alembic_upgrade(database_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def _build_env(
    *,
    database_url: str,
    storcli_path: str,
    admin_password_hash: str,
) -> dict[str, str]:
    return {
        "ADMIN_USERNAME": E2E_ADMIN_USERNAME,
        "ADMIN_PASSWORD_HASH": admin_password_hash,
        "DATABASE_URL": database_url,
        "STORCLI_PATH": storcli_path,
        "STORCLI_USE_SUDO": "false",
        "ALERT_SMTP_HOST": "smtp.invalid",
        "ALERT_SMTP_PORT": "587",
        "ALERT_SMTP_USER": "test",
        "ALERT_SMTP_PASSWORD": "test",
        "ALERT_FROM": "alerts@example.com",
        "ALERT_TO": "ops@example.com",
        "ALERT_SMTP_USE_STARTTLS": "false",
        "METRICS_INTERVAL_SECONDS": "300",
        "LOG_LEVEL": "WARNING",
        "COLLECTOR_ENABLED": "false",
        "METRICS_ENABLED": "false",
    }


def _format_env_file(values: dict[str, str]) -> str:
    return "".join(f"{key}={value}\n" for key, value in values.items())


@pytest.fixture(scope="session")
def _e2e_session_env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, str]]:
    session_dir = tmp_path_factory.mktemp("e2e-session")
    db_path = session_dir / "megaraid.db"
    database_url = f"sqlite:///{db_path}"
    storcli_stub = _write_storcli_stub(session_dir / "storcli-stub.sh")
    env = _build_env(
        database_url=database_url,
        storcli_path=str(storcli_stub),
        admin_password_hash=E2E_ADMIN_PASSWORD_HASH,
    )

    previous: dict[str, str | None] = {key: os.environ.get(key) for key in env}
    previous["MEGARAID_SKIP_STORCLI_PATH_VALIDATION"] = os.environ.get(
        "MEGARAID_SKIP_STORCLI_PATH_VALIDATION"
    )
    os.environ.update(env)
    os.environ.pop("MEGARAID_SKIP_STORCLI_PATH_VALIDATION", None)

    from megaraid_dashboard.config import get_settings

    get_settings.cache_clear()

    try:
        yield env
    finally:
        for key, prior_value in previous.items():
            if prior_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior_value
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def live_server(_e2e_session_env: dict[str, str]) -> Iterator[str]:
    """Run the FastAPI app on a free loopback port for the entire test session.

    Startup polls for ``server.started`` with a hard timeout
    (``_SERVER_STARTUP_TIMEOUT_SECONDS``) so a misconfigured app fails fast
    instead of hanging. Shutdown signals ``server.should_exit`` and joins the
    worker thread with a matching timeout.
    """
    from megaraid_dashboard.app import create_app

    port = _free_port()
    config = uvicorn.Config(
        app=create_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="e2e-uvicorn", daemon=True)
    thread.start()

    deadline = time.monotonic() + _SERVER_STARTUP_TIMEOUT_SECONDS
    while not server.started:
        if not thread.is_alive():
            msg = "uvicorn worker exited before the server reached the started state"
            raise RuntimeError(msg)
        if time.monotonic() >= deadline:
            server.should_exit = True
            thread.join(timeout=_SERVER_SHUTDOWN_TIMEOUT_SECONDS)
            msg = (
                "timed out waiting for the e2e live_server to start within "
                f"{_SERVER_STARTUP_TIMEOUT_SECONDS:.1f}s"
            )
            raise RuntimeError(msg)
        time.sleep(_SERVER_STARTUP_POLL_INTERVAL_SECONDS)

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=_SERVER_SHUTDOWN_TIMEOUT_SECONDS)


@pytest.fixture
def fresh_db(tmp_path: Path) -> str:
    """Per-test SQLite DB initialised to the latest Alembic revision."""
    db_path = tmp_path / "megaraid.db"
    database_url = f"sqlite:///{db_path}"
    _alembic_upgrade(database_url)
    return database_url


@pytest.fixture
def test_admin_creds(tmp_path: Path, fresh_db: str) -> dict[str, str]:
    """Per-test admin credentials and env file.

    Username and password match what ``live_server`` was started with, so tests
    can authenticate against the session app while pointing other consumers
    (CLI, alembic, etc.) at ``fresh_db`` via ``env_file``.
    """
    storcli_stub = _write_storcli_stub(tmp_path / "storcli-stub.sh")
    env = _build_env(
        database_url=fresh_db,
        storcli_path=str(storcli_stub),
        admin_password_hash=E2E_ADMIN_PASSWORD_HASH,
    )
    env_file = tmp_path / "e2e.env"
    env_file.write_text(_format_env_file(env), encoding="utf-8")
    return {
        "username": E2E_ADMIN_USERNAME,
        "password": E2E_ADMIN_PASSWORD,
        "password_hash": E2E_ADMIN_PASSWORD_HASH,
        "database_url": fresh_db,
        "storcli_path": str(storcli_stub),
        "env_file": str(env_file),
    }
