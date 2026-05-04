from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import (
    get_maintenance_state,
    get_state,
    set_maintenance_state,
)


def test_empty_maintenance_state_is_inactive(session: Session) -> None:
    state = get_maintenance_state(session, now=datetime(2026, 5, 4, 12, 0, tzinfo=UTC))

    assert state.active is False
    assert state.expires_at is None
    assert state.started_by is None


def test_maintenance_state_round_trips_active_payload(session: Session) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    expires_at = now + timedelta(hours=2)

    set_maintenance_state(
        session,
        active=True,
        expires_at=expires_at,
        started_by="admin",
    )
    session.commit()

    state = get_maintenance_state(session, now=now)

    assert state.active is True
    assert state.expires_at == expires_at
    assert state.started_by == "admin"


def test_maintenance_state_expired_payload_reads_inactive(session: Session) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    expires_at = now - timedelta(minutes=1)
    set_maintenance_state(
        session,
        active=True,
        expires_at=expires_at,
        started_by="admin",
    )
    session.commit()

    state = get_maintenance_state(session, now=now)

    assert state.active is False
    assert state.expires_at == expires_at
    assert state.started_by == "admin"


def test_setting_maintenance_inactive_deletes_row(session: Session) -> None:
    set_maintenance_state(
        session,
        active=True,
        expires_at=datetime(2026, 5, 4, 14, 0, tzinfo=UTC),
        started_by="admin",
    )
    session.commit()

    set_maintenance_state(session, active=False, expires_at=None, started_by=None)
    session.commit()

    assert get_state(session, "maintenance_mode") is None


def test_maintenance_state_rejects_naive_now(session: Session) -> None:
    with pytest.raises(ValueError):
        get_maintenance_state(session, now=datetime(2026, 5, 4, 12, 0))


def test_set_maintenance_state_rejects_naive_expires_at(session: Session) -> None:
    with pytest.raises(ValueError):
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime(2026, 5, 4, 12, 0),
            started_by="admin",
        )
