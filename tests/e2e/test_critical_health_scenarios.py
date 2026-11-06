"""Critical health and honest RAID wording through the real isolated browser app."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import delete, update

from megaraid_dashboard.db import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
)
from tests.e2e.conftest import _LiveServerHandle
from tests.safety.harness import FakeStorcli


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize(
    ("case", "grade", "reason"),
    [
        ("cv-failed", "CRITICAL", "CacheVault reports Failed."),
        ("cv-replace", "CRITICAL", "CacheVault replacement is required."),
        ("cv-absent", "CRITICAL", "CacheVault is not detected."),
        ("cv-low", "WARNING", "CacheVault capacitance is below the warning threshold."),
        ("roc-warning", "WARNING", "Controller temperature is elevated."),
        ("roc-critical", "CRITICAL", "Controller temperature is critical."),
        ("custom-thresholds", "OPTIMAL", "All systems nominal."),
        ("empty-inventory", "CRITICAL", "No virtual drives reported"),
    ],
)
def test_component_fault_is_visible_and_recovers_on_all_summaries(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    width: int,
    case: str,
    grade: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del snapshot_with_drives
    app = _live_server_handle.app
    page = authenticated_page
    page.set_viewport_size({"width": width, "height": 1000})
    with app.state.session_factory() as session, session.begin():
        if case == "cv-absent":
            session.execute(delete(CacheVaultSnapshot))
            session.execute(update(ControllerSnapshot).values(cv_present=False))
        elif case == "cv-failed":
            session.execute(update(CacheVaultSnapshot).values(state="Failed"))
        elif case == "cv-replace":
            session.execute(update(CacheVaultSnapshot).values(replacement_required=True))
        elif case == "cv-low":
            session.execute(update(CacheVaultSnapshot).values(capacitance_percent=60))
        elif case == "empty-inventory":
            session.execute(delete(PhysicalDriveSnapshot))
            session.execute(delete(VirtualDriveSnapshot))
        else:
            session.execute(
                update(ControllerSnapshot).values(
                    roc_temperature_celsius=96 if case == "roc-warning" else 106
                )
            )
    if case == "custom-thresholds":
        monkeypatch.setattr(
            app.state,
            "settings",
            app.state.settings.model_copy(
                update={"roc_temp_warning_celsius": 110, "roc_temp_critical_celsius": 120}
            ),
        )
    page.goto(f"{live_server}/")
    expect(page.locator(".controller-state")).to_have_text(grade)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    page.get_by_role("link", name="Controller", exact=True).click()
    health = page.locator('[data-controller-section="health-snapshot"]')
    expect(health.locator(".health-card__state")).to_have_text(grade)
    expect(health.locator(".health-card__detail")).to_contain_text(reason)
    expect(health).not_to_contain_text("state degraded")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    if case == "cv-replace" and (folder := os.environ.get("SCENARIO_SCREENSHOT_DIR")):
        directory = Path(folder)
        directory.mkdir(parents=True, exist_ok=True)
        health.screenshot(path=directory / f"cachevault-replacement-{width}.png")
    assert fake_storcli.calls == []
    if case in {"cv-absent", "empty-inventory"}:
        return
    with app.state.session_factory() as session, session.begin():
        session.execute(update(ControllerSnapshot).values(roc_temperature_celsius=80))
        session.execute(
            update(CacheVaultSnapshot).values(
                state="Optimal", replacement_required=False, capacitance_percent=89
            )
        )
    page.get_by_role("button", name="Refresh now").click()
    expect(health.locator(".health-card__state")).to_have_text("OPTIMAL")
    expect(health.locator(".health-card__detail")).to_contain_text("All systems nominal.")
    page.get_by_role("link", name="Overview", exact=True).first.click()
    expect(page.locator(".controller-state")).to_have_text("OPTIMAL")
