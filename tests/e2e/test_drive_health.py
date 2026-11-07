"""Persisted faulty samples -> consistent, readable health on every drive surface."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import update

from megaraid_dashboard.db import PhysicalDriveSnapshot, get_engine
from tests.safety.harness import FakeStorcli


@pytest.mark.parametrize("width", [1440, 1024, 390])
@pytest.mark.parametrize(
    ("state", "smart", "predictive", "media", "temperature", "severity", "label", "reason"),
    [
        ("Onln", True, 0, 0, 35, "critical", "Critical", "S.M.A.R.T. alert is active."),
        ("Onln", False, 1, 0, 35, "critical", "Critical", "1 predictive failure."),
        (
            "UBad",
            False,
            0,
            0,
            35,
            "critical",
            "Critical",
            "Controller marks drive unusable (UBad).",
        ),
        ("Onln", False, 0, 1, 35, "warning", "Attention", "1 historical media error."),
        ("UGood", False, 0, 0, 35, "optimal", "Ready", ""),
        ("DHS", False, 0, 0, 35, "optimal", "Hot spare", ""),
        ("GHS", False, 0, 0, 35, "optimal", "Hot spare", ""),
        ("Onln", False, 0, 0, 56, "warning", "Attention", "Drive temperature is elevated."),
        ("Onln", False, 0, 0, 61, "critical", "Critical", "Drive temperature is critical."),
        ("Mystery", False, 0, 0, 35, "warning", "Attention", "Unrecognized drive state: Mystery."),
    ],
)
def test_fault_health_and_recovery_across_all_views(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
    width: int,
    state: str,
    smart: bool,
    predictive: int,
    media: int,
    temperature: int,
    severity: str,
    label: str,
    reason: str,
) -> None:
    page = authenticated_page
    page.set_viewport_size({"width": width, "height": 1000})
    engine = get_engine(snapshot_with_drives)

    def save_sample(*, recovered: bool = False) -> None:
        with engine.begin() as connection:
            connection.execute(
                update(PhysicalDriveSnapshot)
                .where(PhysicalDriveSnapshot.slot_id == 4)
                .values(
                    state="Onln" if recovered else state,
                    smart_alert=False if recovered else smart,
                    predictive_failures=0 if recovered else predictive,
                    media_errors=0 if recovered else media,
                    temperature_celsius=35 if recovered else temperature,
                )
            )

    try:
        save_sample()
        page.goto(f"{live_server}/")
        tile = page.locator('.drive-grid-v2 [data-physical-slot="S0"]')
        expect(tile).to_have_class(f"drive-tile-v2 drive-tile-v2--{severity}")
        expect(tile.locator(".drive-health-label")).to_have_text(label)
        expect(tile.locator(".drive-health-raw")).to_have_text(state)
        expected_summary = "OPTIMAL" if severity == "optimal" else severity.upper()
        expect(page.locator(".controller-state")).to_have_text(expected_summary)
        if reason:
            expect(tile.locator(".drive-health-reason")).to_have_text(reason)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        previews = os.environ.get("MEGARAID_SCREENSHOT_DIR")
        if previews and smart:
            directory = Path(previews)
            directory.mkdir(parents=True, exist_ok=True)
            page.locator(".drive-grid-section-v2").screenshot(
                path=directory / f"smart-overview-{width}.png"
            )
        tile.click()
        page.wait_for_url("**/drives/252:4")
        health = page.locator("[data-drive-detail-section='health-snapshot']")
        expect(health.locator(".health-card")).to_have_class(f"health-card health-card--{severity}")
        expect(health.locator(".health-card__state")).to_have_text(label)
        expect(health.locator(".drive-health-raw")).to_contain_text(state.upper())
        if reason:
            expect(health).to_contain_text(reason)
        expect(page.locator('.backplane-slot[data-physical-slot="S0"]')).to_have_class(
            f"backplane-slot backplane-slot--{severity} backplane-slot--this"
        )
        if previews and smart:
            health.screenshot(path=Path(previews) / f"smart-detail-{width}.png")
        page.goto(f"{live_server}/drives")
        row = page.locator("#physical-drives-table tbody tr").filter(
            has=page.get_by_role("link", name="252:4", exact=True)
        )
        expect(row).to_have_attribute("data-row-state", severity)
        expect(row.locator(".status-badge")).to_contain_text(label)
        expect(row.locator(".drive-health-raw")).to_have_text(state)
        if reason:
            expect(row.locator(".drive-health-reason")).to_have_text(reason)
        if previews and smart:
            page.locator("#drives-live").screenshot(
                path=Path(previews) / f"smart-drives-{width}.png"
            )
        save_sample(recovered=True)
        page.get_by_role("button", name="Refresh now").click()
        expect(row).to_have_attribute("data-row-state", "optimal")
        expect(row.locator(".status-badge")).to_contain_text("Healthy")
        expect(row.locator(".drive-health-reason")).to_have_count(0)
        page.goto(f"{live_server}/")
        expect(page.locator(".controller-state")).to_have_text("OPTIMAL")
        expect(tile).to_have_class("drive-tile-v2 drive-tile-v2--optimal")
        expect(tile.locator(".drive-health-label")).to_have_text("Healthy")
        page.goto(f"{live_server}/drives/252:4")
        expect(health.locator(".health-card")).to_have_class("health-card health-card--optimal")
        expect(health.locator(".health-card__state")).to_have_text("Healthy")
        assert fake_storcli.calls == []
    finally:
        engine.dispose()
