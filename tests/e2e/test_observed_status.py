"""Read-only browser regressions for observed topology and monitoring status."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot, get_engine, get_sessionmaker
from megaraid_dashboard.web import routes
from tests.e2e.conftest import login_and_navigate
from tests.safety.harness import FakeStorcli


@pytest.mark.parametrize(
    ("mode", "badge"), [("auto", "Enabled"), ("manual", "Disabled"), ("unknown", "Unknown")]
)
def test_observed_status_across_pages(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    fake_storcli: FakeStorcli,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    badge: str,
) -> None:
    physical = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/storcli/redacted/eall_sall_show_all.json"
        ).read_text()
    )
    key = "Drive /c0/e252/s0"
    response = physical["Controllers"][0]["Response Data"]
    response[key][0]["DID"] = 0
    response[f"{key} - Detailed Information"][f"{key} Device attributes"]["SN"] = "WCA275678000"
    engine = get_engine(snapshot_with_drives)
    try:
        with get_sessionmaker(engine)() as session, session.begin():
            snapshot = session.scalars(select(ControllerSnapshot)).one()
            snapshot.raw_json = {
                "physical_drives": physical,
                "patrol_read": {"mode": mode, "state": "stopped"},
            }
    finally:
        engine.dispose()
    last_run = datetime.now(UTC) - timedelta(minutes=2, seconds=5)
    monkeypatch.setattr(
        routes.scheduler_state,
        "get_last_collector_run_at",
        lambda: last_run,
    )
    calls = fake_storcli.calls
    page = authenticated_page
    for path in ("/", "/controller", "/drives/252:0"):
        login_and_navigate(page, f"{live_server}{path}")
        expect(page.locator(".status-bar-v2")).to_contain_text("Collector 2 min ago")
        if path == "/controller":
            schedule = page.locator("[data-controller-section='scheduled-tasks']")
            expect(schedule.locator(".status-badge")).to_have_text(badge)
            if mode == "auto":
                expect(schedule).to_contain_text("Next: unknown.")
                expect(
                    page.locator("[data-controller-section='live-operations']")
                ).not_to_contain_text("168h")
        if path == "/drives/252:0":
            expect(page.locator("[data-drive-detail-section='physical-position']")).to_contain_text(
                "0 : 0 : 4"
            )
    assert fake_storcli.calls == calls
