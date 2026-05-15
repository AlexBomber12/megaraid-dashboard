from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from megaraid_dashboard import app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db import get_engine, get_sessionmaker
from tests.conftest import TEST_ADMIN_PASSWORD_HASH


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


class _StubServer:
    def __init__(self, *, started: bool = False, should_exit: bool = False) -> None:
        self.started = started
        self.should_exit = should_exit


async def test_wait_for_metrics_server_startup_raises_when_task_finishes_silently() -> None:
    async def silent_exit() -> None:
        return None

    task = asyncio.create_task(silent_exit())
    await task

    with pytest.raises(RuntimeError, match="exited before startup completed"):
        await app._wait_for_metrics_server_startup(server=_StubServer(), task=task)


async def test_wait_for_metrics_server_startup_raises_when_server_should_exit() -> None:
    async def brief_run() -> None:
        await asyncio.sleep(0.01)

    task = asyncio.create_task(brief_run())
    try:
        with pytest.raises(RuntimeError, match="stopped before startup completed"):
            await app._wait_for_metrics_server_startup(
                server=_StubServer(should_exit=True),
                task=task,
            )
    finally:
        if not task.done():
            await task


async def test_wait_for_metrics_server_startup_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def long_run() -> None:
        await asyncio.sleep(5)

    task = asyncio.create_task(long_run())
    monkeypatch.setattr(app, "_METRICS_SERVER_STARTUP_TIMEOUT_SECONDS", -1.0)

    try:
        with pytest.raises(TimeoutError, match="timed out"):
            await app._wait_for_metrics_server_startup(server=_StubServer(), task=task)
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_start_metrics_server_cancels_pending_task_on_startup_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    test_app = FastAPI()
    runtime = app._MetricsRuntime()

    class HangingServer:
        started = False
        should_exit = False

        def __init__(self, config: object) -> None:
            self.config = config

        async def serve(self) -> None:
            await asyncio.sleep(60)

    monkeypatch.setattr(app.uvicorn, "Server", HangingServer)

    async def fail_wait(*, server: object, task: asyncio.Task[None]) -> None:
        del server, task
        raise OSError("startup probe failed")

    monkeypatch.setattr(app, "_wait_for_metrics_server_startup", fail_wait)

    try:
        with pytest.raises(OSError, match="startup probe failed"):
            await app._start_metrics_server(
                app=test_app,
                settings=settings,
                runtime=runtime,
            )

        assert test_app.state.metrics_lock_fd is None
        assert runtime.server is None
        assert runtime.task is None
        assert runtime.lock_fd is None
        reacquired = app._try_acquire_metrics_lock(settings.metrics_lock_path)
        try:
            assert reacquired is not None
        finally:
            if reacquired is not None:
                app._release_metrics_lock(reacquired)
    finally:
        get_settings.cache_clear()


async def test_start_collector_scheduler_returns_immediately_when_already_started(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    engine = get_engine(settings.database_url)
    session_factory = get_sessionmaker(engine)
    test_app = FastAPI()
    runtime = app._CollectorRuntime(collector=object())

    async def fail_acquire(_path: str) -> int | None:
        raise AssertionError("lock acquisition should be skipped")

    monkeypatch.setattr(app, "_try_acquire_collector_lock", fail_acquire)

    try:
        assert (
            await app._start_collector_scheduler(
                app=test_app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            )
            is True
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


async def test_start_collector_scheduler_releases_lock_on_unexpected_exception(
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

    async def fail_start(self: object) -> object:
        del self
        msg = "collector start failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(app.CollectorService, "start", fail_start)

    try:
        with pytest.raises(RuntimeError, match="collector start failed"):
            await app._start_collector_scheduler(
                app=test_app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            )

        assert test_app.state.collector_lock_fd is None
        reacquired = app._try_acquire_collector_lock(settings.collector_lock_path)
        try:
            assert reacquired is not None
        finally:
            if reacquired is not None:
                app._release_collector_lock(reacquired)
    finally:
        engine.dispose()
        get_settings.cache_clear()


async def test_retry_collector_scheduler_exits_when_collector_already_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_required_app_env(monkeypatch, tmp_path)
    get_settings.cache_clear()
    settings = get_settings()
    engine = get_engine(settings.database_url)
    session_factory = get_sessionmaker(engine)
    test_app = FastAPI()
    runtime = app._CollectorRuntime(collector=object())

    async def fail_start(**_kwargs: object) -> bool:
        raise AssertionError("retry should not call _start_collector_scheduler")

    monkeypatch.setattr(app, "_start_collector_scheduler", fail_start)
    monkeypatch.setattr(app, "_COLLECTOR_LOCK_RETRY_SECONDS", 0.0)

    try:
        await app._retry_collector_scheduler_start(
            app=test_app,
            settings=settings,
            session_factory=session_factory,
            runtime=runtime,
        )
    finally:
        engine.dispose()
        get_settings.cache_clear()


async def test_retry_collector_scheduler_loops_until_lock_available(
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
    sentinel_collector = object()
    sentinel_scheduler = object()
    call_count = {"n": 0}

    async def staged_start(**kwargs: Any) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return False
        target_runtime = kwargs["runtime"]
        target_runtime.collector = sentinel_collector
        target_runtime.scheduler = sentinel_scheduler
        return True

    monkeypatch.setattr(app, "_start_collector_scheduler", staged_start)
    monkeypatch.setattr(app, "_COLLECTOR_LOCK_RETRY_SECONDS", 0.0)

    try:
        with capture_logs() as logs:
            await app._retry_collector_scheduler_start(
                app=test_app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            )

        assert call_count["n"] == 2
        assert runtime.collector is sentinel_collector
        assert any(entry["event"] == "collector_scheduler_started_after_retry" for entry in logs)
    finally:
        engine.dispose()
        get_settings.cache_clear()


async def test_retry_collector_scheduler_logs_unexpected_exceptions_and_retries(
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
    sentinel = object()
    call_count = {"n": 0}

    async def flaky_start(**kwargs: Any) -> bool:
        call_count["n"] += 1
        if call_count["n"] == 1:
            msg = "transient retry failure"
            raise RuntimeError(msg)
        target_runtime = kwargs["runtime"]
        target_runtime.collector = sentinel
        target_runtime.scheduler = object()
        return True

    monkeypatch.setattr(app, "_start_collector_scheduler", flaky_start)
    monkeypatch.setattr(app, "_COLLECTOR_LOCK_RETRY_SECONDS", 0.0)

    try:
        with capture_logs() as logs:
            await app._retry_collector_scheduler_start(
                app=test_app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            )

        assert call_count["n"] == 2
        assert runtime.collector is sentinel
        assert any(entry["event"] == "collector_scheduler_retry_failed" for entry in logs)
    finally:
        engine.dispose()
        get_settings.cache_clear()


async def test_retry_collector_scheduler_propagates_cancelled_error(
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

    async def cancelling_start(**_kwargs: Any) -> bool:
        raise asyncio.CancelledError

    monkeypatch.setattr(app, "_start_collector_scheduler", cancelling_start)
    monkeypatch.setattr(app, "_COLLECTOR_LOCK_RETRY_SECONDS", 0.0)

    try:
        with pytest.raises(asyncio.CancelledError):
            await app._retry_collector_scheduler_start(
                app=test_app,
                settings=settings,
                session_factory=session_factory,
                runtime=runtime,
            )
    finally:
        engine.dispose()
        get_settings.cache_clear()


def test_upgrade_database_logs_debug_when_already_at_head(tmp_path: Path) -> None:
    db_file = tmp_path / "head.db"
    url = f"sqlite:///{db_file}"
    engine = get_engine(url)
    try:
        with engine.begin() as connection:
            app._upgrade_database(url, connection=connection)
        with engine.begin() as connection, capture_logs() as logs:
            app._upgrade_database(url, connection=connection)
        assert any(entry["event"] == "database_at_head_revision" for entry in logs)
    finally:
        engine.dispose()


def test_upgrade_database_skips_log_when_called_without_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def noop_upgrade(_config: object, _target: str) -> None:
        return None

    monkeypatch.setattr(app.command, "upgrade", noop_upgrade)

    with capture_logs() as logs:
        app._upgrade_database("sqlite:///:memory:")

    assert all(entry["event"] != "database_at_head_revision" for entry in logs)
    assert all(entry["event"] != "database_migration_applied" for entry in logs)


def test_validate_process_lock_file_rejects_non_regular_file() -> None:
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError, match="must be a regular file"):
            app._validate_process_lock_file(read_fd, "/dev/pipe", lock_name="collector")
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_validate_process_lock_file_rejects_unexpected_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "collector.lock"
    lock_path.touch()
    fd = os.open(str(lock_path), os.O_RDWR)
    other_uid = os.getuid() + 1
    try:
        monkeypatch.setattr(app.os, "getuid", lambda: other_uid)
        with pytest.raises(RuntimeError, match="must be owned by the current user"):
            app._validate_process_lock_file(fd, str(lock_path), lock_name="collector")
    finally:
        os.close(fd)


def test_validate_process_lock_file_rejects_hard_links(tmp_path: Path) -> None:
    lock_path = tmp_path / "collector.lock"
    lock_path.touch()
    extra_link = tmp_path / "collector.lock.dup"
    os.link(lock_path, extra_link)
    fd = os.open(str(lock_path), os.O_RDWR)
    try:
        with pytest.raises(RuntimeError, match="must not have hard links"):
            app._validate_process_lock_file(fd, str(lock_path), lock_name="collector")
    finally:
        os.close(fd)


def test_try_acquire_process_lock_releases_fd_when_validation_raises(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "collector.lock"
    lock_path.touch()
    extra_link = tmp_path / "collector.lock.dup"
    os.link(lock_path, extra_link)

    with pytest.raises(RuntimeError, match="must not have hard links"):
        app._try_acquire_process_lock(str(lock_path), lock_name="collector")

    extra_link.unlink()
    lock_fd = app._try_acquire_process_lock(str(lock_path), lock_name="collector")
    assert lock_fd is not None
    app._release_process_lock(lock_fd)


def test_resolve_sqlite_db_path_returns_none_for_unparseable_url() -> None:
    assert app._resolve_sqlite_db_path("not a valid url") is None


def test_tighten_sqlite_db_permissions_logs_warning_when_lstat_raises_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_file = tmp_path / "perm.db"
    db_file.touch()

    def fail_lstat(_path: object) -> Any:
        raise PermissionError("denied")

    monkeypatch.setattr(app.os, "lstat", fail_lstat)

    with capture_logs() as logs:
        app._tighten_sqlite_db_permissions(f"sqlite:///{db_file}")

    assert any(entry["event"] == "db_chmod_stat_failed" for entry in logs)


def test_redacted_database_url_returns_placeholder_for_unparseable_url() -> None:
    assert app._redacted_database_url("not a valid url") == "<invalid database url>"
