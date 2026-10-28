from __future__ import annotations

import base64
import json
import os
import socket
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bcrypt
import pytest

os.environ.setdefault("MEGARAID_SKIP_STORCLI_PATH_VALIDATION", "1")
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from megaraid_dashboard.db import Base, get_engine, get_sessionmaker
from megaraid_dashboard.storcli import (
    StorcliSnapshot,
    parse_bbu,
    parse_cachevault,
    parse_controller_show_all,
    parse_physical_drives,
    parse_virtual_drives,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "storcli" / "redacted"
TEST_ADMIN_PASSWORD = "test-password"
TEST_ADMIN_PASSWORD_HASH = bcrypt.hashpw(TEST_ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode()
TEST_AUTH_HEADER = {
    "Authorization": "Basic "
    + base64.b64encode(f"admin:{TEST_ADMIN_PASSWORD}".encode()).decode("ascii")
}


@pytest.fixture(autouse=True)
def isolated_metrics_listener_port(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Exercise real metrics listeners without sharing the production default port.

    Concurrent checkouts/test processes must not compete for localhost:8091.
    Tests inspecting configuration defaults explicitly remove this test override.
    """
    from megaraid_dashboard.config import get_settings

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    monkeypatch.setenv("METRICS_PORT", str(port))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def admin_password_hash() -> str:
    return TEST_ADMIN_PASSWORD_HASH


@pytest.fixture
def auth_header() -> dict[str, str]:
    return dict(TEST_AUTH_HEADER)


@pytest.fixture
def csrf_headers() -> Callable[[TestClient], dict[str, str]]:
    def build_headers(client: TestClient, *, path: str = "/") -> dict[str, str]:
        response = client.get(path)
        token = client.cookies.get("__Host-csrf")
        assert response.status_code < 400
        assert token is not None
        return {"X-CSRF-Token": token}

    return build_headers


@pytest.fixture
def db_url() -> str:
    return "sqlite:///:memory:"


@pytest.fixture
def engine(db_url: str) -> Iterator[Engine]:
    engine = get_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    Base.metadata.create_all(engine)
    session_factory = get_sessionmaker(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture
def sample_snapshot() -> StorcliSnapshot:
    return StorcliSnapshot(
        controller=parse_controller_show_all(_load_fixture("c0_show_all.json")),
        virtual_drives=parse_virtual_drives(_load_fixture("vall_show_all.json")),
        physical_drives=parse_physical_drives(_load_fixture("eall_sall_show_all.json")),
        cachevault=parse_cachevault(_load_fixture("cv_show_all.json")),
        bbu=parse_bbu(_load_fixture("bbu_show_all.json")),
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    )


def _load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.fixture
def scripted_storcli(tmp_path: Path) -> Callable[[dict[str, tuple[str, int, str]]], Path]:
    """Run a disposable executable with exact scripted argv; never reach hardware."""
    import sys

    def create(responses: dict[str, tuple[str, int, str]]) -> Path:
        binary = tmp_path / "scripted-storcli"
        binary.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            f"responses = json.loads({json.dumps(responses)!r})\n"
            "out, code, err = responses[' '.join(sys.argv[1:])]\n"
            "sys.stdout.write(out)\n"
            "sys.stderr.write(err)\n"
            "sys.exit(code)\n"
        )
        binary.chmod(0o755)
        return binary

    return create


@pytest.fixture
def legacy_controller_handlers(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[None]:
    """Exercise existing inner checks behind the newly closed rollout gate.

    Only legacy handler/UI regression modules opt in. Rollout acceptance tests
    never use this fixture. Read-only protection remains real in both suites.
    """
    from megaraid_dashboard.config import get_settings

    def inner_gate(settings: Any, family: str) -> str | None:
        return (
            "Controller actions disabled: read-only rollout."
            if settings.controller_read_only
            else None
        )

    monkeypatch.setenv("CONTROLLER_READ_ONLY", "false")
    monkeypatch.setattr("megaraid_dashboard.web.routes.action_disabled_reason", inner_gate)
    monkeypatch.setattr("megaraid_dashboard.web.templates.action_disabled_reason", inner_gate)
    get_settings.cache_clear()
    if "_live_server_handle" in request.fixturenames:
        handle = request.getfixturevalue("_live_server_handle")
        original = handle.app.state.settings
        handle.app.state.settings = original.model_copy(update={"controller_read_only": False})
        try:
            yield
        finally:
            handle.app.state.settings = original
    else:
        yield
    get_settings.cache_clear()
