"""Controller health remains consistent for failed, missing and uncertain components."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.services.controller_detail import load_controller_detail_view_model
from megaraid_dashboard.services.controller_health import assess_controller_components
from megaraid_dashboard.storcli import StorcliSnapshot
from tests.test_services.test_critical_user_scenarios import _healthy
from tests.test_services.test_telegram_notifier import _settings


@pytest.mark.parametrize(
    ("controller", "cv", "missing", "configured", "severity", "reason"),
    [
        ({}, {}, False, True, "optimal", ""),
        ({"roc_temperature_celsius": None}, {}, False, True, "optimal", ""),
        ({}, {}, True, True, "warning", "unverified"),
        ({"cv_present": False}, {}, False, True, "warning", "conflicts"),
        ({"cv_present": False}, {}, True, True, "critical", "not detected"),
        ({}, {"state": "Unknown"}, False, True, "critical", "reports Unknown"),
        (
            {},
            {"state": "Failed", "replacement_required": True},
            False,
            True,
            "critical",
            "required",
        ),
        ({}, {"capacitance_percent": None}, False, True, "optimal", ""),
        ({}, {"capacitance_percent": 70}, False, True, "optimal", ""),
        ({}, {"capacitance_percent": 69}, False, True, "warning", "capacitance"),
        ({"roc_temperature_celsius": 106}, {}, False, False, "optimal", ""),
    ],
)
def test_component_evidence(
    session: Session,
    sample_snapshot: StorcliSnapshot,
    controller: dict[str, Any],
    cv: dict[str, Any],
    missing: bool,
    configured: bool,
    severity: str,
    reason: str,
) -> None:
    sample = _healthy(sample_snapshot)
    assert sample.cachevault is not None
    sample = sample.model_copy(
        update={
            "controller": sample.controller.model_copy(update=controller),
            "cachevault": None if missing else sample.cachevault.model_copy(update=cv),
        }
    )
    stored = insert_snapshot(session, sample)
    result = assess_controller_components(stored, settings=_settings() if configured else None)
    assert result.severity == severity
    assert reason in " ".join(result.reasons)


def test_configured_component_thresholds_are_used(
    session: Session, sample_snapshot: StorcliSnapshot
) -> None:
    sample = _healthy(sample_snapshot)
    stored = insert_snapshot(
        session,
        sample.model_copy(
            update={
                "controller": sample.controller.model_copy(update={"roc_temperature_celsius": 100})
            }
        ),
    )
    settings = _settings(
        roc_temp_warning_celsius=105,
        roc_temp_critical_celsius=115,
        cv_capacitance_warning_percent=90,
    )
    result = assess_controller_components(stored, settings=settings)
    assert result.severity == "warning"
    assert result.reasons == ("CacheVault capacitance is below the warning threshold.",)


def test_empty_inventory_summary_never_claims_nominal_or_degraded_array(
    session: Session,
    sample_snapshot: StorcliSnapshot,
) -> None:
    sample = _healthy(sample_snapshot).model_copy(
        update={"physical_drives": [], "virtual_drives": []}
    )
    insert_snapshot(session, sample)
    view = load_controller_detail_view_model(
        session,
        settings=_settings(),
        scheduler=None,
        collector_enabled=True,
        app_start_time=sample.captured_at,
        app_version="scenario",
        roc_history_chart_url="/controller/roc-history",
    )
    assert view.health.state == "CRITICAL"
    assert "No virtual drives reported" in view.health.summary_text
    assert "healthy" not in view.health.summary_text
    assert "state degraded" not in view.health.summary_text
