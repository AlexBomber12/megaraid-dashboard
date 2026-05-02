from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bcrypt
import pytest
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


@pytest.fixture
def admin_password_hash() -> str:
    return TEST_ADMIN_PASSWORD_HASH


@pytest.fixture
def auth_header() -> dict[str, str]:
    return dict(TEST_AUTH_HEADER)


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
