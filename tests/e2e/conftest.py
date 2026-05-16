"""End-to-end test infrastructure.

Fixtures:

* ``live_server`` (session-scoped): one uvicorn-hosted FastAPI app shared across
  every test in the session. Yields the base URL. Tests that need clean DB state
  reset via ``fresh_db``, not by restarting the server.
* ``fresh_db`` (function-scoped): per-test SQLite database with ``alembic upgrade head``
  applied. Yields the SQLAlchemy URL and rebinds the running ``live_server`` app's
  engine, sessionmaker, and health engine to the fresh DB for the duration of the
  test, restoring the originals on teardown. This is what makes the session-scoped
  server safe to share across tests that write data.
* ``test_admin_creds`` (function-scoped): admin credentials plus an env file for
  tests that need to drive auth flows. The credentials are stable across the
  session so they also match what ``live_server`` was started with.
* ``authenticated_page`` (function-scoped): Playwright ``Page`` bound to a fresh
  browser context with valid admin ``http_credentials`` pre-set, ready to drive
  authenticated UI flows.
"""

from __future__ import annotations

import contextlib
import os
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt
import pytest
import uvicorn
from fastapi import FastAPI
from playwright.sync_api import Browser, Page
from sqlalchemy.engine import Engine

E2E_ADMIN_USERNAME = "e2e-admin"
E2E_ADMIN_PASSWORD = "e2e-test-pass-1234"
E2E_ADMIN_PASSWORD_HASH = bcrypt.hashpw(
    E2E_ADMIN_PASSWORD.encode(),
    bcrypt.gensalt(rounds=4),
).decode()

_SERVER_STARTUP_POLL_INTERVAL_SECONDS = 0.1
_SERVER_STARTUP_TIMEOUT_SECONDS = 10.0
_SERVER_SHUTDOWN_TIMEOUT_SECONDS = 10.0
_HEALTH_CHECK_SQLITE_BUSY_TIMEOUT_MS = 250
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
        "AUTH_RATE_LIMIT_PER_MINUTE": "5",
        "AUTH_RATE_LIMIT_BURST": "0",
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


@dataclass
class _LiveServerHandle:
    app: FastAPI
    url: str


@pytest.fixture(scope="session")
def _live_server_handle(_e2e_session_env: dict[str, str]) -> Iterator[_LiveServerHandle]:
    """Run the FastAPI app on a free loopback port for the entire test session.

    Startup polls for ``server.started`` with a hard timeout
    (``_SERVER_STARTUP_TIMEOUT_SECONDS``) so a misconfigured app fails fast
    instead of hanging. Shutdown signals ``server.should_exit`` and joins the
    worker thread with a matching timeout.
    """
    from megaraid_dashboard.app import create_app

    app = create_app()
    port = _free_port()
    config = uvicorn.Config(
        app=app,
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
        yield _LiveServerHandle(app=app, url=f"http://127.0.0.1:{port}")
    finally:
        server.should_exit = True
        thread.join(timeout=_SERVER_SHUTDOWN_TIMEOUT_SECONDS)


@pytest.fixture(scope="session")
def live_server(_live_server_handle: _LiveServerHandle) -> str:
    return _live_server_handle.url


@pytest.fixture
def fresh_db(tmp_path: Path, _live_server_handle: _LiveServerHandle) -> Iterator[str]:
    """Per-test SQLite DB initialised to the latest Alembic revision.

    The running ``live_server`` is session-scoped and its engine and
    sessionmaker were bound to the session DB during the FastAPI lifespan.
    To give each test a clean DB *without* restarting the server, this
    fixture creates a new SQLite file, upgrades it to head, and rebinds
    ``app.state.engine``, ``app.state.session_factory``, and
    ``app.state.health_engine`` on the running app to point at it. Route
    handlers re-read these attributes per request, so subsequent HTTP
    requests through ``live_server.url`` hit the fresh DB. On teardown the
    originals are restored and the per-test engines are disposed.
    """
    from megaraid_dashboard.db import get_engine, get_sessionmaker

    db_path = tmp_path / "megaraid.db"
    database_url = f"sqlite:///{db_path}"
    _alembic_upgrade(database_url)

    app = _live_server_handle.app
    new_engine = get_engine(database_url)
    new_health_engine = get_engine(
        database_url, sqlite_busy_timeout_ms=_HEALTH_CHECK_SQLITE_BUSY_TIMEOUT_MS
    )
    new_session_factory = get_sessionmaker(new_engine)

    original_engine: Engine = app.state.engine
    original_health_engine: Engine = app.state.health_engine
    original_session_factory = app.state.session_factory

    app.state.engine = new_engine
    app.state.health_engine = new_health_engine
    app.state.session_factory = new_session_factory

    try:
        yield database_url
    finally:
        app.state.engine = original_engine
        app.state.health_engine = original_health_engine
        app.state.session_factory = original_session_factory
        new_engine.dispose()
        new_health_engine.dispose()


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


def _find_rate_limit_middleware(app: FastAPI) -> Any:
    from megaraid_dashboard.web.rate_limit import AuthRateLimitMiddleware

    current: Any = app.middleware_stack
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, AuthRateLimitMiddleware):
            return current
        current = getattr(current, "app", None)
    return None


@pytest.fixture(autouse=True)
def _reset_auth_rate_limit_attempts(
    _live_server_handle: _LiveServerHandle,
) -> Iterator[None]:
    # The session-scoped live_server keeps one in-memory attempts bucket across
    # tests; clear it so each test starts from a known state.
    middleware = _find_rate_limit_middleware(_live_server_handle.app)
    if middleware is not None:
        middleware._attempts.clear()
    yield


@pytest.fixture
def authenticated_page(
    browser: Browser,
    test_admin_creds: dict[str, str],
) -> Iterator[Page]:
    context = browser.new_context(
        http_credentials={
            "username": test_admin_creds["username"],
            "password": test_admin_creds["password"],
        }
    )
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()
