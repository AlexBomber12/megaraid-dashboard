from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from megaraid_dashboard.db import (
    Base,
    CacheVaultSnapshot,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
    get_sessionmaker,
)
from megaraid_dashboard.web.metrics import create_metrics_app


@pytest.fixture
def session_factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    Base.metadata.create_all(engine)
    factory = get_sessionmaker(engine)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)


def test_controller_health_metric_is_optimal_for_seeded_healthy_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot())

    response_text = _scrape_metrics(session_factory)

    assert _controller_metric("megaraid_controller_health", 0.0) in response_text


def test_controller_health_metric_is_critical_when_alarm_is_on(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot(alarm_state="On"))

    response_text = _scrape_metrics(session_factory)

    assert _controller_metric("megaraid_controller_health", 2.0) in response_text


def test_controller_health_metric_is_warning_with_degraded_virtual_drive(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot(vd_state="Dgrd"))

    response_text = _scrape_metrics(session_factory)

    assert _controller_metric("megaraid_controller_health", 1.0) in response_text


@pytest.mark.parametrize("state", ["Pdgd", "Partially Degraded"])
def test_controller_health_metric_is_critical_with_partially_degraded_virtual_drive(
    session_factory: sessionmaker[Session],
    state: str,
) -> None:
    _insert(session_factory, _snapshot(vd_state=state))

    response_text = _scrape_metrics(session_factory)

    assert _controller_metric("megaraid_controller_health", 2.0) in response_text


def test_controller_roc_temperature_metric_uses_snapshot_value(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot(roc_temperature_celsius=78))

    response_text = _scrape_metrics(session_factory)

    assert _controller_metric("megaraid_controller_roc_temperature_celsius", 78.0) in response_text


def test_controller_roc_temperature_metric_is_absent_without_snapshot_value(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot(roc_temperature_celsius=None))

    response_text = _scrape_metrics(session_factory)

    assert "megaraid_controller_roc_temperature_celsius" not in response_text


def test_cachevault_capacitance_metric_is_present_when_cachevault_is_present(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot(cv_capacitance_percent=89))

    response_text = _scrape_metrics(session_factory)

    assert _controller_metric("megaraid_cv_capacitance_percent", 89.0) in response_text


def test_cachevault_capacitance_metric_is_absent_without_cachevault(
    session_factory: sessionmaker[Session],
) -> None:
    _insert(session_factory, _snapshot(cachevault_present=False))

    response_text = _scrape_metrics(session_factory)

    assert "megaraid_cv_capacitance_percent" not in response_text


def _insert(
    session_factory: sessionmaker[Session],
    snapshot: ControllerSnapshot,
) -> None:
    with session_factory() as session:
        session.add(snapshot)
        session.commit()


def _scrape_metrics(session_factory: sessionmaker[Session]) -> str:
    metrics_app = create_metrics_app(session_factory)
    with TestClient(metrics_app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    return response.text


def _controller_metric(name: str, value: float) -> str:
    return f'{name}{{model="LSI MegaRAID SAS 9270CV-8i",serial="SV00000001"}} {value}'


def _snapshot(
    *,
    alarm_state: str = "Off",
    vd_state: str = "Optl",
    pd_state: str = "Onln",
    roc_temperature_celsius: int | None = 78,
    cachevault_present: bool = True,
    cv_capacitance_percent: int | None = 89,
) -> ControllerSnapshot:
    snapshot = ControllerSnapshot(
        captured_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
        model_name="LSI MegaRAID SAS 9270CV-8i",
        serial_number="SV00000001",
        firmware_version="23.34.0-0019",
        bios_version="6.36.00.3_4.19.08.00_0x06180203",
        driver_version="07.727.03.00",
        alarm_state=alarm_state,
        cv_present=cachevault_present,
        bbu_present=True,
        roc_temperature_celsius=roc_temperature_celsius,
    )
    snapshot.physical_drives = [_physical_drive(state=pd_state)]
    snapshot.virtual_drives = [
        VirtualDriveSnapshot(
            vd_id=0,
            name="system",
            raid_level="RAID5",
            size_bytes=1_000_000,
            state=vd_state,
            access="RW",
            cache="RWBD",
        )
    ]
    if cachevault_present:
        snapshot.cachevault = CacheVaultSnapshot(
            type="CVPM02",
            state="Optimal",
            temperature_celsius=32,
            pack_energy="OK",
            capacitance_percent=cv_capacitance_percent,
            replacement_required=False,
            next_learn_cycle=None,
        )
    return snapshot


def _physical_drive(*, state: str) -> PhysicalDriveSnapshot:
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
        state=state,
        temperature_celsius=31,
        media_errors=0,
        other_errors=0,
        predictive_failures=0,
        smart_alert=False,
        sas_address="5000c50000000000",
    )
