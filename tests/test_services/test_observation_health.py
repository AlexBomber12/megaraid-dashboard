"""Freshness is distinct from the health of the last observed RAID sample."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.db.models import Event
from megaraid_dashboard.services.observation_health import observation_warning
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_critical_user_scenarios import NOW, _healthy
from tests.test_services.test_telegram_notifier import _settings


@pytest.mark.parametrize(
    ("enabled", "age", "expected"),
    [
        (False, None, None),
        (True, None, "No reliable collection"),
        (True, -60, "No reliable collection"),
        (True, 599, None),
        (True, 600, None),
        (True, 601, "Monitoring data is stale"),
    ],
)
def test_observation_age(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    enabled: bool,
    age: int | None,
    expected: str | None,
) -> None:
    if age is not None:
        insert_snapshot(session, _healthy(sample_snapshot, at=NOW - timedelta(seconds=age)))
    result = observation_warning(session, settings=_settings(collector_enabled=enabled), now=NOW)
    if expected is None:
        assert result is None
    else:
        assert result is not None and expected in result


@pytest.mark.parametrize(
    ("category", "offset", "failed"),
    [
        ("system", -1, True),
        ("system", -11, False),
        ("system", 1, False),
        ("operator_action", -1, False),
    ],
)
def test_only_unrecovered_past_collection_failure_warns(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    category: str,
    offset: int,
    failed: bool,
) -> None:
    insert_snapshot(session, _healthy(sample_snapshot, at=NOW - timedelta(seconds=10)))
    session.add(
        Event(
            occurred_at=NOW + timedelta(seconds=offset),
            category=category,
            severity="warning",
            subject="Controller",
            summary="Collection failed: fake",
        )
    )
    session.flush()
    result = observation_warning(session, settings=_settings(collector_enabled=True), now=NOW)
    if failed:
        assert result is not None and "Collection failed. Showing last known data" in result
    else:
        assert result is None


def test_freshness_has_a_sixty_second_minimum(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    insert_snapshot(session, _healthy(sample_snapshot, at=NOW - timedelta(seconds=60)))
    settings = _settings(collector_enabled=True, metrics_interval_seconds=10)
    assert observation_warning(session, settings=settings, now=NOW) is None
    assert "stale" in (
        observation_warning(session, settings=settings, now=NOW + timedelta(seconds=1)) or ""
    )
