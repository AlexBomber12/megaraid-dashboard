from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.db import (
    Base,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
    get_sessionmaker,
)
from megaraid_dashboard.web.metrics import _encode_pd_state, _encode_vd_state, create_metrics_app


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


def test_drive_metrics_reflect_seeded_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(_snapshot_with_drives())
        session.commit()

    response_text = _scrape_metrics(session_factory)

    assert (
        'megaraid_drive_temperature_celsius{enclosure="252",model="ST4000NM000",'
        'serial="SN0001",slot="0"} 31.0'
    ) in response_text
    assert (
        'megaraid_drive_temperature_celsius{enclosure="252",model="ST4000NM001",'
        'serial="SN0002",slot="1"} 42.0'
    ) in response_text
    assert (
        'megaraid_drive_temperature_celsius{enclosure="252",model="ST4000NM002",'
        'serial="SN0003",slot="2"} 55.0'
    ) in response_text
    assert response_text.count("megaraid_drive_temperature_celsius{") == 3

    assert (
        'megaraid_physical_drive_state{enclosure="252",model="ST4000NM000",'
        'serial="SN0001",slot="0"} 0.0'
    ) in response_text
    assert (
        'megaraid_physical_drive_state{enclosure="252",model="ST4000NM001",'
        'serial="SN0002",slot="1"} 1.0'
    ) in response_text
    assert (
        'megaraid_physical_drive_state{enclosure="252",model="ST4000NM002",'
        'serial="SN0003",slot="2"} 2.0'
    ) in response_text
    assert response_text.count("megaraid_physical_drive_state{") == 3

    assert (
        'megaraid_virtual_drive_state{name="system",raid_level="RAID5",vd_id="0"} 0.0'
    ) in response_text
    assert (
        'megaraid_virtual_drive_state{name="backup",raid_level="RAID1",vd_id="1"} 1.0'
    ) in response_text
    assert response_text.count("megaraid_virtual_drive_state{") == 2


def test_drive_metrics_absent_without_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    response_text = _scrape_metrics(session_factory)

    assert "megaraid_exporter_up 1.0" in response_text
    assert "megaraid_drive_temperature_celsius" not in response_text
    assert "megaraid_physical_drive_state" not in response_text
    assert "megaraid_virtual_drive_state" not in response_text


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("Onln", 0),
        ("UGood", 0),
        ("Optl", 0),
        ("Rbld", 1),
        ("UReb", 1),
        ("Missing", 1),
        ("Offln", 2),
        ("Failed", 2),
        ("UBad", 2),
        ("Unexpected", 2),
    ],
)
def test_physical_drive_state_encoding(state: str, expected: int) -> None:
    assert _encode_pd_state(state) == expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("Optl", 0),
        ("Optimal", 0),
        ("Dgrd", 1),
        ("Degraded", 1),
        ("Pdgd", 1),
        ("Partially-Degraded", 1),
        ("Offln", 2),
        ("Failed", 2),
        ("Unexpected", 2),
    ],
)
def test_virtual_drive_state_encoding(state: str, expected: int) -> None:
    assert _encode_vd_state(state) == expected


def test_drive_metrics_cache_reuses_families_for_same_snapshot(
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        session.add(_snapshot_with_drives())
        session.commit()

    physical_drive_queries = 0

    @event.listens_for(engine, "before_cursor_execute")
    def _count_physical_drive_queries(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal physical_drive_queries
        if "FROM pd_snapshots" in statement:
            physical_drive_queries += 1

    try:
        metrics_app = create_metrics_app(session_factory)
        with TestClient(metrics_app) as client:
            first = client.get("/metrics")
            second = client.get("/metrics")
    finally:
        event.remove(engine, "before_cursor_execute", _count_physical_drive_queries)

    assert first.status_code == 200
    assert second.status_code == 200
    assert physical_drive_queries == 1


def _scrape_metrics(session_factory: sessionmaker[Session]) -> str:
    metrics_app = create_metrics_app(session_factory)
    with TestClient(metrics_app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    return response.text


def _snapshot_with_drives() -> ControllerSnapshot:
    snapshot = ControllerSnapshot(
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state="Off",
        cv_present=True,
        bbu_present=True,
    )
    snapshot.physical_drives = [
        _physical_drive(
            slot_id=0,
            device_id=32,
            model="ST4000NM000",
            serial="SN0001",
            state="Onln",
            temperature=31,
        ),
        _physical_drive(
            slot_id=1,
            device_id=33,
            model="ST4000NM001",
            serial="SN0002",
            state="Rbld",
            temperature=42,
        ),
        _physical_drive(
            slot_id=2,
            device_id=34,
            model="ST4000NM002",
            serial="SN0003",
            state="Offln",
            temperature=55,
        ),
    ]
    snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=0,
            name="system",
            raid_level="RAID5",
            size_bytes=1_000_000,
            state="Optl",
            access="RW",
            cache="RWBD",
        ),
        VirtualDriveSnapshot(
            vd_id=1,
            name="backup",
            raid_level="RAID1",
            size_bytes=2_000_000,
            state="Dgrd",
            access="RW",
            cache="RWBD",
        ),
    ]
    return snapshot


def _physical_drive(
    *,
    slot_id: int,
    device_id: int,
    model: str,
    serial: str,
    state: str,
    temperature: int,
) -> PhysicalDriveSnapshot:
    return PhysicalDriveSnapshot(
        enclosure_id=252,
        slot_id=slot_id,
        device_id=device_id,
        model=model,
        serial_number=serial,
        firmware_version="SN04",
        size_bytes=4_000_000_000_000,
        interface="SAS",
        media_type="HDD",
        state=state,
        temperature_celsius=temperature,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address=f"5000c5000000000{slot_id}",
    )
