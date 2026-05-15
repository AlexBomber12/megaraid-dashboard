from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.db import (
    Base,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
    get_sessionmaker,
)
from megaraid_dashboard.web.metrics import MegaraidCollector, create_metrics_app


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


def test_build_families_returns_empty_when_snapshot_disappears(
    session_factory: sessionmaker[Session],
) -> None:
    collector = MegaraidCollector(session_factory)

    assert collector._build_families(latest_id=9999) == []


def test_collect_returns_empty_when_snapshot_disappears_between_queries(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot = _snapshot()
        session.add(snapshot)
        session.commit()
        latest_id = snapshot.id

    with session_factory() as session:
        session.delete(session.get(ControllerSnapshot, latest_id))
        session.commit()

    collector = MegaraidCollector(session_factory)
    collector._cache = (latest_id - 1 if latest_id else None, [])

    assert list(collector.collect()) == []


def test_physical_drive_metric_omits_temperature_when_value_is_none(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        snapshot = _snapshot()
        snapshot.physical_drives = [_physical_drive(temperature_celsius=None)]
        session.add(snapshot)
        session.commit()

    response_text = _scrape_metrics(session_factory)

    assert "megaraid_drive_temperature_celsius{" not in response_text
    assert 'megaraid_physical_drive_state{enclosure="252"' in response_text


def _scrape_metrics(session_factory: sessionmaker[Session]) -> str:
    metrics_app = create_metrics_app(session_factory)
    with TestClient(metrics_app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    return response.text


def _snapshot() -> ControllerSnapshot:
    snapshot = ControllerSnapshot(
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=False,
        bbu_present=True,
        roc_temperature_celsius=None,
    )
    snapshot.physical_drives = [_physical_drive(temperature_celsius=31)]
    snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=0,
            name="system",
            raid_level="RAID5",
            size_bytes=1_000_000,
            state="Optl",
            access="RW",
            cache="RWBD",
        )
    ]
    return snapshot


def _physical_drive(*, temperature_celsius: int | None) -> PhysicalDriveSnapshot:
    return PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=0,
        device_id=32,
        model="ST4000NM000",
        serial_number="SN0001",
        firmware_version="SN04",
        size_bytes=4_000_000_000_000,
        interface="SAS",
        media_type="HDD",
        state="Onln",
        temperature_celsius=temperature_celsius,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="5000c50000000000",
    )
