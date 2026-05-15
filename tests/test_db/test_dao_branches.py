from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db import dao
from megaraid_dashboard.db.dao import (
    MaintenanceState,
    _optional_string,
    _parse_optional_datetime,
    _require_aware_utc,
    _storcli_datetime_to_utc,
    get_alert_by_fingerprint,
    insert_snapshot,
    set_maintenance_state,
    upsert_alert_sent,
    upsert_temp_state,
)
from megaraid_dashboard.storcli import StorcliSnapshot


def test_require_aware_utc_rejects_naive() -> None:
    with pytest.raises(ValueError, match="naive datetimes"):
        _require_aware_utc(datetime(2026, 5, 15, 12, 0))


def test_require_aware_utc_normalizes_non_utc_offset() -> None:
    tz = timezone(timedelta(hours=3))
    value = datetime(2026, 5, 15, 15, 0, tzinfo=tz)
    result = _require_aware_utc(value)
    assert result.tzinfo is UTC
    assert result.hour == 12


def test_parse_optional_datetime_returns_none_for_none() -> None:
    assert _parse_optional_datetime(None) is None


def test_parse_optional_datetime_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="ISO datetime string"):
        _parse_optional_datetime(42)


def test_parse_optional_datetime_parses_iso_string() -> None:
    result = _parse_optional_datetime("2026-05-15T12:00:00+00:00")
    assert result is not None
    assert result.tzinfo is UTC


def test_optional_string_returns_none_for_none() -> None:
    assert _optional_string(None) is None


def test_optional_string_rejects_non_string() -> None:
    with pytest.raises(ValueError, match="started_by must be a string"):
        _optional_string(42)


def test_optional_string_passes_through_string() -> None:
    assert _optional_string("admin") == "admin"


def test_storcli_datetime_to_utc_returns_none_for_none() -> None:
    assert _storcli_datetime_to_utc(None) is None


def test_storcli_datetime_to_utc_attaches_utc_for_naive_datetime() -> None:
    naive = datetime(2026, 5, 15, 12, 0)
    result = _storcli_datetime_to_utc(naive)
    assert result is not None
    assert result.tzinfo is UTC


def test_storcli_datetime_to_utc_converts_offset_aware_datetime() -> None:
    tz = timezone(timedelta(hours=-4))
    value = datetime(2026, 5, 15, 8, 0, tzinfo=tz)
    result = _storcli_datetime_to_utc(value)
    assert result is not None
    assert result.tzinfo is UTC
    assert result.hour == 12


def test_upsert_temp_state_raises_when_session_get_returns_none(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_get = session.get

    def fake_get(model: Any, primary_key: Any, **kwargs: Any) -> Any:
        if hasattr(model, "__tablename__") and model.__tablename__ == "pd_temp_states":
            return None
        return original_get(model, primary_key, **kwargs)

    monkeypatch.setattr(session, "get", fake_get)
    with pytest.raises(RuntimeError, match="temperature state upsert did not return"):
        upsert_temp_state(
            session,
            enclosure_id=252,
            slot_id=1,
            serial_number="SN0001",
            state="warning",
        )


def test_upsert_alert_sent_raises_when_session_get_returns_none(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_get = session.get

    def fake_get(model: Any, primary_key: Any, **kwargs: Any) -> Any:
        if hasattr(model, "__tablename__") and model.__tablename__ == "alerts_sent":
            return None
        return original_get(model, primary_key, **kwargs)

    monkeypatch.setattr(session, "get", fake_get)
    with pytest.raises(RuntimeError, match="alert upsert did not return"):
        upsert_alert_sent(
            session,
            severity="critical",
            category="cv",
            subject="CV failed",
            fingerprint="abc",
            recipient="ops@example.com",
        )


def test_upsert_alert_sent_with_suppressed_until_normalizes_utc(session: Session) -> None:
    tz = timezone(timedelta(hours=2))
    suppressed = datetime(2026, 5, 15, 14, 0, tzinfo=tz)
    alert = upsert_alert_sent(
        session,
        severity="warning",
        category="drive",
        subject="drive offline",
        fingerprint="fp1",
        recipient="ops@example.com",
        suppressed_until=suppressed,
    )
    session.flush()
    assert alert.suppressed_until is not None
    assert alert.suppressed_until.astimezone(UTC).hour == 12


def test_get_alert_by_fingerprint_returns_alert(session: Session) -> None:
    upsert_alert_sent(
        session,
        severity="info",
        category="drive",
        subject="drive online",
        fingerprint="fp2",
        recipient="ops@example.com",
    )
    session.flush()
    alert = get_alert_by_fingerprint(session, "fp2")
    assert alert is not None
    assert alert.fingerprint == "fp2"
    assert get_alert_by_fingerprint(session, "missing") is None


def test_iter_pending_events_rejects_naive_since() -> None:
    bad_session = MagicMock()
    with pytest.raises(ValueError, match="naive datetimes"):
        next(
            dao.iter_pending_events(
                bad_session, severity_threshold="warning", since=datetime(2026, 5, 15)
            )
        )


def test_severities_at_or_above_rejects_unknown_threshold() -> None:
    with pytest.raises(ValueError, match="unknown severity threshold"):
        dao._severities_at_or_above("nope")


def test_severities_at_or_above_known_thresholds() -> None:
    assert dao._severities_at_or_above("info") == {"info", "warning", "critical"}
    assert dao._severities_at_or_above("warning") == {"warning", "critical"}
    assert dao._severities_at_or_above("critical") == {"critical"}


def test_mark_event_notified_raises_for_unknown_event(session: Session) -> None:
    with pytest.raises(LookupError, match="event 999 not found"):
        dao.mark_event_notified(session, 999, datetime.now(UTC))


def test_count_events_notified_since_returns_zero_when_none(session: Session) -> None:
    now = datetime.now(UTC)
    assert dao.count_events_notified_since(session, since=now - timedelta(days=1)) == 0


def test_clear_temp_state_for_slot_returns_count(session: Session) -> None:
    upsert_temp_state(
        session,
        enclosure_id=252,
        slot_id=2,
        serial_number="SN-X",
        state="warning",
    )
    session.flush()
    deleted = dao.clear_temp_state_for_slot(session, enclosure_id=252, slot_id=2)
    assert deleted == 1
    assert dao.clear_temp_state_for_slot(session, enclosure_id=252, slot_id=2) == 0


def test_get_state_returns_none_for_missing(session: Session) -> None:
    assert dao.get_state(session, "missing") is None


def test_set_state_creates_then_updates_and_expires_identity(session: Session) -> None:
    dao.set_state(session, "foo", "bar")
    session.flush()
    assert dao.get_state(session, "foo") == "bar"
    dao.set_state(session, "foo", "baz")
    session.flush()
    assert dao.get_state(session, "foo") == "baz"


def test_delete_state_handles_missing_key(session: Session) -> None:
    dao.delete_state(session, "no-such-key")
    dao.set_state(session, "to-delete", "value")
    session.flush()
    dao.delete_state(session, "to-delete")
    session.flush()
    assert dao.get_state(session, "to-delete") is None


def test_get_maintenance_state_absent(session: Session) -> None:
    state = dao.get_maintenance_state(session, now=datetime.now(UTC))
    assert state == MaintenanceState(active=False, expires_at=None, started_by=None)


def test_get_maintenance_state_expired(session: Session) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    past = now - timedelta(minutes=10)
    set_maintenance_state(session, active=True, expires_at=past, started_by="admin")
    session.flush()
    state = dao.get_maintenance_state(session, now=now)
    assert state.active is False
    assert state.expires_at == past
    assert state.started_by == "admin"


def test_get_maintenance_state_active(session: Session) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    future = now + timedelta(hours=1)
    set_maintenance_state(session, active=True, expires_at=future, started_by="admin")
    session.flush()
    state = dao.get_maintenance_state(session, now=now)
    assert state.active is True
    assert state.expires_at == future
    assert state.started_by == "admin"


def test_get_maintenance_state_rejects_naive_now(session: Session) -> None:
    with pytest.raises(ValueError, match="naive datetimes"):
        dao.get_maintenance_state(session, now=datetime(2026, 5, 15))


def test_set_maintenance_state_rejects_naive_expires_at(session: Session) -> None:
    with pytest.raises(ValueError, match="naive datetimes"):
        set_maintenance_state(
            session,
            active=True,
            expires_at=datetime(2026, 5, 15),
            started_by=None,
        )


def test_set_maintenance_state_inactive_deletes_state(session: Session) -> None:
    set_maintenance_state(
        session,
        active=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        started_by="admin",
    )
    session.flush()
    set_maintenance_state(session, active=False, expires_at=None, started_by=None)
    session.flush()
    assert dao.get_state(session, "maintenance_mode") is None


def test_insert_snapshot_skips_cachevault_when_absent(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    snapshot_without_cv = sample_snapshot.model_copy(update={"cachevault": None})
    inserted = insert_snapshot(session, snapshot_without_cv)
    assert inserted.cachevault is None


def test_set_maintenance_state_active_writes_payload(session: Session) -> None:
    now = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
    set_maintenance_state(
        session,
        active=True,
        expires_at=now + timedelta(hours=1),
        started_by="admin",
    )
    session.flush()
    raw = dao.get_state(session, "maintenance_mode")
    assert raw is not None
    assert '"active": true' in raw
    set_maintenance_state(session, active=True, expires_at=None, started_by=None)
    session.flush()
    raw_no_expires = dao.get_state(session, "maintenance_mode")
    assert raw_no_expires is not None
    assert '"expires_at": null' in raw_no_expires
