from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.services.audit import record_operator_action


def test_record_operator_action_writes_event(session: Session) -> None:
    occurred_at = datetime(2026, 5, 3, 9, 30, tzinfo=UTC)

    event = record_operator_action(
        session,
        username="admin",
        message="locate start drive 2:0",
        occurred_at=occurred_at,
    )

    assert event.category == "operator_action"
    assert event.severity == "info"
    assert event.subject == "Operator action"
    assert event.summary == "locate start drive 2:0"
    assert event.operator_username == "admin"
    assert event.occurred_at == occurred_at


def test_record_operator_action_defaults_to_current_utc(session: Session) -> None:
    before = datetime.now(UTC)

    event = record_operator_action(session, username="admin", message="locate stop drive 2:0")

    after = datetime.now(UTC)
    assert before <= event.occurred_at <= after
    assert event.occurred_at.tzinfo is UTC


def test_record_operator_action_rejects_naive_occurred_at(session: Session) -> None:
    with pytest.raises(ValueError):
        record_operator_action(
            session,
            username="admin",
            message="locate start drive 2:0",
            occurred_at=datetime(2026, 5, 3, 9, 30),
        )
