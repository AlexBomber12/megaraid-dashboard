from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.audit import record_operator_action
from megaraid_dashboard.storcli import StorcliParseError
from megaraid_dashboard.web.routes import (
    _record_rebuild_complete_once_sync,
    _record_rebuild_progress_observed_once_sync,
)
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
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
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_drive_rebuild_status_rejects_non_integer_path() -> None:
    """Cover the ``int()`` ValueError catch in ``drive_rebuild_status``."""
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/abc:0/replace/rebuild-status")

    assert response.status_code == 400


def test_drive_rebuild_status_returns_502_when_parse_fails_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the ``except StorcliParseError`` JSON-response branch."""

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    def fake_parse(_payload: object) -> Any:
        raise StorcliParseError("malformed rebuild payload")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr("megaraid_dashboard.web.routes.parse_rebuild_status", fake_parse)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")

    assert response.status_code == 502
    body = response.json()
    assert body == {
        "error": "storcli parse failed",
        "detail": "malformed rebuild payload",
    }


def test_drive_rebuild_status_returns_html_partial_when_parse_fails_for_htmx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the ``StorcliParseError`` HTMX-partial branch."""

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return {"Controllers": [{"Command Status": {"Status": "Success"}}]}

    def fake_parse(_payload: object) -> Any:
        raise StorcliParseError("malformed rebuild payload")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)
    monkeypatch.setattr("megaraid_dashboard.web.routes.parse_rebuild_status", fake_parse)

    test_app = create_app()
    headers = {**TEST_AUTH_HEADER, "HX-Request": "true"}
    with TestClient(test_app, headers=headers) as client:
        response = client.get("/drives/2:0/replace/rebuild-status")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "storcli parse failed" in response.text
    assert "malformed rebuild payload" in response.text


def test_record_rebuild_progress_skips_record_when_progress_marker_already_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the ``if observed is not None: session.rollback(); return`` branch.

    The endpoint records a single "rebuild progress observed" event per
    replacement cycle, so the second invocation must short-circuit. This pins
    that dedupe path.
    """

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
    ) -> dict[str, Any]:
        del args, use_sudo, binary_path
        return _rebuild_payload(percent=42, state="In progress")

    monkeypatch.setattr("megaraid_dashboard.web.routes.run_storcli", fake_run_storcli)

    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        # Seed a successful replace-step-insert so the cycle marker exists and
        # the endpoint will attempt to write a progress marker.
        _record_operator_action(
            test_app,
            summary=(
                "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded"
            ),
        )
        first = client.get("/drives/2:0/replace/rebuild-status")
        second = client.get("/drives/2:0/replace/rebuild-status")

        assert first.status_code == 200
        assert second.status_code == 200
        summaries = [event.summary for event in _all_events(test_app)]
        # Exactly one progress-observed marker even after two polls.
        assert summaries.count("rebuild progress observed drive 2:0") == 1


def test_record_rebuild_progress_propagates_sqlalchemy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the ``except SQLAlchemyError`` re-raise branch in
    ``_record_rebuild_progress_observed_once_sync``.
    """
    test_app = create_app()

    def fail_record_event(*_args: object, **_kwargs: object) -> Any:
        raise SQLAlchemyError("synthetic progress write failure")

    monkeypatch.setattr("megaraid_dashboard.web.routes.record_event", fail_record_event)

    request = _request_for_app(test_app)
    with (
        TestClient(test_app, headers=TEST_AUTH_HEADER),
        pytest.raises(SQLAlchemyError),
    ):
        _record_rebuild_progress_observed_once_sync(
            request=request,
            enclosure_id=2,
            slot_id=0,
            percent_complete=42,
            state="In progress",
        )


def test_record_rebuild_complete_propagates_sqlalchemy_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the ``except SQLAlchemyError`` re-raise branch in
    ``_record_rebuild_complete_once_sync``.
    """
    test_app = create_app()

    def fail_record_operator_action(*_args: object, **_kwargs: object) -> Any:
        raise SQLAlchemyError("synthetic complete write failure")

    monkeypatch.setattr(
        "megaraid_dashboard.web.routes.record_operator_action",
        fail_record_operator_action,
    )

    request = _request_for_app(test_app)
    with (
        TestClient(test_app, headers=TEST_AUTH_HEADER),
        pytest.raises(SQLAlchemyError),
    ):
        _record_rebuild_complete_once_sync(
            request=request,
            enclosure_id=2,
            slot_id=0,
            require_replacement_cycle=False,
        )


def test_record_rebuild_progress_uses_postgres_advisory_lock_when_dialect_is_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the non-sqlite ``else: session.begin()`` and postgresql advisory
    lock branches in ``_record_rebuild_progress_observed_once_sync``.

    The production deployment uses PostgreSQL where transaction-scoped
    advisory locks are the dedupe primitive; the SQLite fast-path is for
    development. This wraps the in-memory sqlite session with a stub that
    reports ``dialect.name == "postgresql"`` and records the advisory-lock SQL
    so we can prove the postgres path executed.
    """
    test_app = create_app()
    request = _request_for_app(test_app)
    with TestClient(test_app, headers=TEST_AUTH_HEADER):
        captured_sql = _install_postgres_stub_session(monkeypatch, test_app)
        # Seed the replacement cycle marker so the record path actually writes.
        _record_operator_action(
            test_app,
            summary=(
                "replace step insert drive 2:0 serial replacement-2 dg=0 array=0 row=0 succeeded"
            ),
        )
        _record_rebuild_progress_observed_once_sync(
            request=request,
            enclosure_id=2,
            slot_id=0,
            percent_complete=42,
            state="In progress",
        )

        assert any("pg_advisory_xact_lock" in sql for sql in captured_sql), captured_sql
        summaries = [event.summary for event in _all_events(test_app)]
        assert "rebuild progress observed drive 2:0" in summaries


def test_record_rebuild_complete_uses_postgres_advisory_lock_when_dialect_is_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the non-sqlite ``else: session.begin()`` and postgresql advisory
    lock branches in ``_record_rebuild_complete_once_sync``.
    """
    test_app = create_app()
    request = _request_for_app(test_app)
    with TestClient(test_app, headers=TEST_AUTH_HEADER):
        captured_sql = _install_postgres_stub_session(monkeypatch, test_app)
        _record_rebuild_complete_once_sync(
            request=request,
            enclosure_id=2,
            slot_id=0,
            require_replacement_cycle=False,
        )

        assert any("pg_advisory_xact_lock" in sql for sql in captured_sql), captured_sql
        summaries = [event.summary for event in _all_events(test_app)]
        assert "rebuild complete drive 2:0" in summaries


class _FakeBindSession:
    """Wraps a real ``Session`` so it reports a non-SQLite dialect name.

    The production rebuild-record helpers branch on ``dialect.name`` to choose
    between SQLite ``BEGIN IMMEDIATE`` and PostgreSQL advisory-lock primitives.
    To exercise the postgres path on an in-memory sqlite session we intercept
    ``get_bind()`` and short-circuit the postgres-specific advisory-lock SQL.
    Everything else (``add``/``flush``/``commit``/``rollback``/``scalars``/etc.)
    falls through to the real underlying session via ``__getattr__``.
    """

    def __init__(self, real: Session, captured_sql: list[str]) -> None:
        self._real = real
        self._captured_sql = captured_sql

    def __enter__(self) -> _FakeBindSession:
        self._real.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        return self._real.__exit__(*args)

    def get_bind(self) -> Any:
        dialect = type("_FakeDialect", (), {"name": "postgresql"})()
        return type("_FakeBind", (), {"dialect": dialect})()

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        sql_text = str(statement)
        if "pg_advisory_xact_lock" in sql_text:
            self._captured_sql.append(sql_text)
            return None
        return self._real.execute(statement, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _install_postgres_stub_session(monkeypatch: pytest.MonkeyPatch, test_app: FastAPI) -> list[str]:
    """Patch ``routes._session`` so it yields a ``_FakeBindSession`` wrapper
    that reports a postgresql dialect. Returns the list collecting any
    advisory-lock SQL that the production code executed against the wrapper.
    """
    real_factory = test_app.state.session_factory
    captured_sql: list[str] = []

    @contextmanager
    def fake_session(_request: Any) -> Iterator[_FakeBindSession]:
        real = real_factory()
        wrapper = _FakeBindSession(real, captured_sql)
        with wrapper as session:
            yield session

    monkeypatch.setattr("megaraid_dashboard.web.routes._session", fake_session)
    return captured_sql


def _rebuild_payload(*, percent: int, state: str) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "Drive /c0/e2/s0 - Rebuild Progress": [
                        {"Progress%": f"{percent}%", "State": state}
                    ]
                },
            }
        ]
    }


def _all_events(test_app: FastAPI) -> list[Event]:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session:
        assert isinstance(session, Session)
        return list(session.scalars(select(Event)).all())


def _record_operator_action(test_app: FastAPI, *, summary: str) -> None:
    session_factory = test_app.state.session_factory
    assert isinstance(session_factory, sessionmaker)
    with session_factory() as session, session.begin():
        record_operator_action(session, username="admin", message=summary)


def _request_for_app(test_app: FastAPI) -> Any:
    return type(
        "RequestStub",
        (),
        {"app": test_app, "scope": {"user_username": "admin"}},
    )()
