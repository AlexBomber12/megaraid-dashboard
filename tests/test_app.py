from __future__ import annotations

import threading
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from megaraid_dashboard import app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db import get_engine


def _set_required_app_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-bcrypt-hash")
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "true")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
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


def test_upgrade_database_uses_existing_in_memory_connection() -> None:
    engine = get_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            app._upgrade_database("sqlite:///:memory:", connection=connection)

        assert "controller_snapshots" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


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
        with TestClient(test_app) as client:
            response = client.get("/health")

        assert response.status_code == 200
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
        with TestClient(test_app) as client:
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
