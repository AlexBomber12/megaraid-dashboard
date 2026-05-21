from __future__ import annotations

import asyncio
import os
import stat
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from structlog.testing import capture_logs

from megaraid_dashboard import app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db import get_engine, get_sessionmaker
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


def _set_required_app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")


def test_alembic_paths_use_source_checkout_when_available() -> None:
    config_path, script_location = app._alembic_paths()

    assert config_path.name == "alembic.ini"
    assert config_path.exists()
    assert script_location.name == "migrations"
    assert script_location.exists()


def test_alembic_paths_fall_back_to_packaged_files(monkeypatch: pytest.MonkeyPatch) -> None:
    missing_root = Path("/tmp/megaraid-dashboard-missing-root")
    monkeypatch.setattr(app, "_project_root", lambda: missing_root)

    config_path, script_location = app._alembic_paths()

    package_root = Path(app.__file__).resolve().parent
    assert config_path == package_root / "alembic.ini"
    assert script_location == package_root / "migrations"


def test_redacted_database_url_hides_password() -> None:
    redacted_url = app._redacted_database_url("postgresql://user:secret@example.test/db")

    assert "secret" not in redacted_url
    assert redacted_url == "postgresql://user:***@example.test/db"


def test_configparser_value_escapes_percent_for_alembic() -> None:
    database_url = "postgresql://user:p%40ss@example.test/db"
    config = Config()

    config.set_main_option("sqlalchemy.url", app._configparser_value(database_url))

    assert config.get_main_option("sqlalchemy.url") == database_url


def test_create_app_orders_security_middleware(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()

    try:
        test_app = app.create_app()

        assert [middleware.cls.__name__ for middleware in test_app.user_middleware[:4]] == [
            "ForwardedPrefixMiddleware",
            "AuthRateLimitMiddleware",
            "BasicAuthMiddleware",
            "CsrfMiddleware",
        ]
        assert "SecurityHeadersMiddleware" not in [
            middleware.cls.__name__ for middleware in test_app.user_middleware
        ]
        stack = test_app.build_middleware_stack()
        assert type(stack).__name__ == "SecurityHeadersMiddleware"
    finally:
        get_settings.cache_clear()


def test_upgrade_database_uses_existing_in_memory_connection() -> None:
    engine = get_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            app._upgrade_database("sqlite:///:memory:", connection=connection)

        assert "controller_snapshots" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_upgrade_database_wraps_revision_discovery_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_from_config(_config: Config) -> object:
        msg = "migration scripts unavailable"
        raise ValueError(msg)

    monkeypatch.setattr(app.ScriptDirectory, "from_config", fail_from_config)

    with pytest.raises(RuntimeError, match="database migration failed") as exc_info:
        app._upgrade_database("sqlite:///:memory:")

    assert isinstance(exc_info.value.__cause__, ValueError)


def test_collector_lock_is_exclusive(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "collector.lock")
    first_lock = app._try_acquire_collector_lock(lock_path)
    assert first_lock is not None

    try:
        assert app._try_acquire_collector_lock(lock_path) is None
    finally:
        app._release_collector_lock(first_lock)

    second_lock = app._try_acquire_collector_lock(lock_path)
    assert second_lock is not None
    app._release_collector_lock(second_lock)


def test_collector_lock_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("preserve", encoding="utf-8")
    lock_path = tmp_path / "collector.lock"
    lock_path.symlink_to(target)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        app._try_acquire_collector_lock(str(lock_path))

    assert target.read_text(encoding="utf-8") == "preserve"


def test_collector_lock_creates_missing_parent_directory(tmp_path: Path) -> None:
    parent = tmp_path / "var-lib"
    lock_path = parent / "collector.lock"
    assert not parent.exists()

    lock_fd = app._try_acquire_collector_lock(str(lock_path))
    try:
        assert lock_fd is not None
        assert parent.is_dir()
        assert stat.S_IMODE(parent.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO) == 0
    finally:
        if lock_fd is not None:
            app._release_collector_lock(lock_fd)


def test_collector_lock_logs_when_parent_create_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "blocked"
    lock_path = parent / "collector.lock"

    def fail_makedirs(path: str, mode: int = 0o777, exist_ok: bool = False) -> None:
        del path, mode, exist_ok
        raise PermissionError("denied")

    monkeypatch.setattr(app.os, "makedirs", fail_makedirs)

    with capture_logs() as logs, pytest.raises(FileNotFoundError):
        app._try_acquire_collector_lock(str(lock_path))

    assert any(entry["event"] == "lock_parent_directory_create_failed" for entry in logs)


def test_ensure_lock_parent_directory_noop_when_directory_exists(tmp_path: Path) -> None:
    parent = tmp_path / "existing"
    parent.mkdir(mode=0o750)
    before_mode = stat.S_IMODE(parent.stat().st_mode)

    app._ensure_lock_parent_directory(str(parent / "collector.lock"), lock_name="collector")

    assert stat.S_IMODE(parent.stat().st_mode) == before_mode


def test_lifespan_skips_collector_when_lock_is_already_held(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    lock_path = str(tmp_path / "collector.lock")
    held_lock = app._try_acquire_collector_lock(lock_path)
    assert held_lock is not None

    async def fail_start(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("collector scheduler should not start when lock is held")

    monkeypatch.setattr(app.CollectorService, "start", fail_start)
    test_app = app.create_app()

    try:
        with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert isinstance(test_app.state.start_time, datetime)
        assert test_app.state.collector is None
        assert test_app.state.scheduler is None
        assert test_app.state.collector_lock_fd is None
    finally:
        app._release_collector_lock(held_lock)
        get_settings.cache_clear()


def test_lifespan_retries_collector_lock_after_holder_releases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_COLLECTOR_LOCK_RETRY_SECONDS", 0.01)
    get_settings.cache_clear()
    lock_path = str(tmp_path / "collector.lock")
    held_lock: int | None = app._try_acquire_collector_lock(lock_path)
    assert held_lock is not None
    started = threading.Event()
    stopped = threading.Event()
    scheduler = object()

    async def fake_start(self: object) -> object:
        del self
        started.set()
        return scheduler

    async def fake_shutdown(self: object, scheduler_arg: object) -> None:
        del self
        assert scheduler_arg is scheduler
        stopped.set()

    monkeypatch.setattr(app.CollectorService, "start", fake_start)
    monkeypatch.setattr(app.CollectorService, "shutdown", fake_shutdown)
    test_app = app.create_app()

    try:
        with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
            response = client.get("/health")
            assert response.status_code == 200
            assert test_app.state.collector is None

            app._release_collector_lock(held_lock)
            held_lock = None

            assert started.wait(timeout=2)
            assert test_app.state.collector is not None
            assert test_app.state.scheduler is scheduler
    finally:
        if held_lock is not None:
            app._release_collector_lock(held_lock)
        get_settings.cache_clear()

    assert stopped.wait(timeout=2)


def test_lifespan_skips_metrics_server_when_lock_is_already_held(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    get_settings.cache_clear()
    settings = get_settings()
    held_lock = app._try_acquire_metrics_lock(settings.metrics_lock_path)
    assert held_lock is not None

    def fail_server(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("metrics server should not start when lock is held")

    monkeypatch.setattr(app.uvicorn, "Server", fail_server)
    test_app = app.create_app()

    try:
        with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert test_app.state.metrics_server is None
        assert test_app.state.metrics_task is None
        assert test_app.state.metrics_lock_fd is None
    finally:
        app._release_metrics_lock(held_lock)
        get_settings.cache_clear()


async def test_start_metrics_server_releases_lock_on_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    test_app = FastAPI()
    runtime = app._MetricsRuntime()

    class FailedStartupServer:
        started = False
        should_exit = False

        def __init__(self, config: object) -> None:
            self.config = config

        async def serve(self) -> None:
            await asyncio.sleep(0)
            raise OSError("address already in use")

    monkeypatch.setattr(app.uvicorn, "Server", FailedStartupServer)

    with pytest.raises(OSError, match="address already in use"):
        await app._start_metrics_server(app=test_app, settings=settings, runtime=runtime)

    reacquired_lock = app._try_acquire_metrics_lock(settings.metrics_lock_path)
    try:
        assert reacquired_lock is not None
        assert test_app.state.metrics_lock_fd is None
        assert runtime.server is None
        assert runtime.task is None
        assert runtime.lock_fd is None
    finally:
        if reacquired_lock is not None:
            app._release_metrics_lock(reacquired_lock)
        get_settings.cache_clear()


async def test_start_collector_scheduler_releases_lock_on_start_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    engine = get_engine(settings.database_url)
    session_factory = get_sessionmaker(engine)
    test_app = FastAPI()
    runtime = app._CollectorRuntime()

    async def cancel_start(self: object) -> object:
        del self
        raise asyncio.CancelledError

    monkeypatch.setattr(app.CollectorService, "start", cancel_start)

    try:
        with pytest.raises(asyncio.CancelledError):
            await app._start_collector_scheduler(
                app=test_app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            )

        assert test_app.state.collector_lock_fd is None
        reacquired_lock = app._try_acquire_collector_lock(settings.collector_lock_path)
        assert reacquired_lock is not None
        app._release_collector_lock(reacquired_lock)
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_resolve_sqlite_db_path_for_absolute_url(tmp_path: Path) -> None:
    db_file = tmp_path / "megaraid.db"
    db_file.touch()

    resolved = app._resolve_sqlite_db_path(f"sqlite:///{db_file}")

    assert resolved == db_file


def test_resolve_sqlite_db_path_returns_none_for_memory_url() -> None:
    assert app._resolve_sqlite_db_path("sqlite:///:memory:") is None


def test_resolve_sqlite_db_path_returns_none_for_non_sqlite_url() -> None:
    assert app._resolve_sqlite_db_path("postgresql://user:pw@example.test/db") is None


def test_tighten_sqlite_db_permissions_chmods_world_readable_file(tmp_path: Path) -> None:
    db_file = tmp_path / "megaraid.db"
    db_file.touch()
    db_file.chmod(0o644)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{db_file}")

    assert stat.S_IMODE(db_file.stat().st_mode) == 0o600
    assert any(entry["event"] == "db_chmod_tightened" for entry in logs)


def test_tighten_sqlite_db_permissions_noop_when_already_restrictive(tmp_path: Path) -> None:
    db_file = tmp_path / "megaraid.db"
    db_file.touch()
    db_file.chmod(0o600)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{db_file}")

    assert stat.S_IMODE(db_file.stat().st_mode) == 0o600
    assert all(entry["event"] != "db_chmod_tightened" for entry in logs)


def test_tighten_sqlite_db_permissions_chmods_group_other_readable_under_0o600(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "megaraid.db"
    db_file.touch()
    db_file.chmod(0o444)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{db_file}")

    assert stat.S_IMODE(db_file.stat().st_mode) == 0o600
    assert any(entry["event"] == "db_chmod_tightened" for entry in logs)


def test_tighten_sqlite_db_permissions_logs_warning_on_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "megaraid.db"
    db_file.touch()
    db_file.chmod(0o644)

    def fail_chmod(self: Path, mode: int, **_kwargs: Any) -> None:
        del self, mode
        raise OSError("read-only filesystem")

    monkeypatch.setattr(Path, "chmod", fail_chmod)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{db_file}")

    assert any(entry["event"] == "db_chmod_failed" for entry in logs)


def test_tighten_sqlite_db_permissions_skips_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "absent.db"

    app._tighten_sqlite_db_permissions(f"sqlite:///{missing}")

    assert not missing.exists()


def test_tighten_sqlite_db_permissions_skips_directory(tmp_path: Path) -> None:
    db_dir = tmp_path / "megaraid-dashboard"
    db_dir.mkdir(mode=0o755)
    original_mode = stat.S_IMODE(db_dir.stat().st_mode)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{db_dir}")

    assert stat.S_IMODE(db_dir.stat().st_mode) == original_mode
    assert any(entry["event"] == "db_chmod_skipped_not_regular_file" for entry in logs)
    assert all(entry["event"] != "db_chmod_tightened" for entry in logs)


def test_tighten_sqlite_db_permissions_skips_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.db"
    target.touch()
    target.chmod(0o644)
    link = tmp_path / "megaraid.db"
    link.symlink_to(target)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{link}")

    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert any(entry["event"] == "db_chmod_skipped_not_regular_file" for entry in logs)
    assert all(entry["event"] != "db_chmod_tightened" for entry in logs)


def test_lifespan_tightens_db_after_first_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "megaraid.db"
    assert not db_file.exists()
    _set_required_app_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    get_settings.cache_clear()

    previous_umask = os.umask(0o022)
    try:
        test_app = app.create_app()
        with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
            response = client.get("/health")
        assert response.status_code == 200
        assert db_file.exists()
        assert stat.S_IMODE(db_file.stat().st_mode) == 0o600
    finally:
        os.umask(previous_umask)
        get_settings.cache_clear()
