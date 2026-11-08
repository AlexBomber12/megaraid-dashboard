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


def test_controller_raw_states_are_read_only_details(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
) -> None:
    del snapshot_with_drives
    page = authenticated_page
    page.goto(f"{live_server}/controller")
    health = page.locator('[data-controller-section="health-snapshot"]')
    expect(health.locator(".health-card__state")).to_have_text("Healthy")
    # Raw firmware states are visible values, not one-value disclosures.
    expect(page.get_by_text("Controller details", exact=True)).to_have_count(0)
    expect(page.locator('[data-controller-section="raid-config"] .raw-state')).to_have_text("Optl")
    cachevault = page.locator('[data-controller-section="cachevault"]')
    expect(cachevault).to_contain_text("Reported State")
    expect(cachevault).to_contain_text("Optimal")
    expect(page.locator('[data-controller-section="raid-config"] .status-badge')).to_have_text(
        "Healthy"
    )
    assert fake_storcli.calls == []


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize(
    ("case", "grade", "reason"),
    [
        ("cv-failed", "Critical", "CacheVault reports Failed."),
        ("cv-replace", "Critical", "CacheVault replacement is required."),
        ("cv-absent", "Critical", "CacheVault is not detected."),
        ("cv-low", "Attention", "CacheVault capacitance is below the warning threshold."),
        ("roc-warning", "Attention", "Controller temperature is elevated."),
        ("roc-critical", "Critical", "Controller temperature is critical."),
        ("custom-thresholds", "Healthy", "8/8 drives online."),
        ("empty-inventory", "Critical", "No virtual drives reported"),
        ("vd-hyphen", "Critical", "VD 0: Partially-Degraded"),
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
        elif case == "vd-hyphen":
            session.execute(update(VirtualDriveSnapshot).values(state="Partially-Degraded"))
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
        session.execute(update(VirtualDriveSnapshot).values(state="Optl"))
        session.execute(
            update(CacheVaultSnapshot).values(
                state="Optimal", replacement_required=False, capacitance_percent=89
            )
        )
    page.get_by_role("button", name="Refresh now").click()
    expect(health.locator(".health-card__state")).to_have_text("Healthy")
    expect(health.locator(".health-card__detail")).to_contain_text("8/8 drives online.")
    page.get_by_role("link", name="Overview", exact=True).first.click()
    expect(page.locator(".controller-state")).to_have_text("Healthy")
