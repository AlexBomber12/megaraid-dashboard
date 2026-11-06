"""Users see when a displayed healthy RAID sample is no longer reliable/current."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import delete, update

from megaraid_dashboard.db import ControllerSnapshot, Event
from tests.e2e.conftest import _LiveServerHandle
from tests.safety.harness import FakeStorcli


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("failure", ["stale", "failed-poll", "first-poll"])
def test_monitoring_warning_is_visible_and_clears_after_reliable_sample(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    failure: str,
) -> None:
    del snapshot_with_drives
    page = authenticated_page
    page.set_viewport_size({"width": width, "height": 1000})
    app = _live_server_handle.app
    monkeypatch.setattr(
        app.state, "settings", app.state.settings.model_copy(update={"collector_enabled": True})
    )
    now = datetime.now(UTC)
    with app.state.session_factory() as session, session.begin():
        if failure == "first-poll":
            session.execute(delete(ControllerSnapshot))
        else:
            age = timedelta(minutes=20) if failure == "stale" else timedelta(seconds=20)
            session.execute(update(ControllerSnapshot).values(captured_at=now - age))
            if failure == "failed-poll":
                session.add(
                    Event(
                        occurred_at=now - timedelta(seconds=10),
                        severity="warning",
                        category="system",
                        subject="Controller",
                        summary="Collection failed: fake",
                    )
                )
    paths = ["/", "/controller", "/drives"]
    if failure != "first-poll":
        paths.append("/drives/252:4")
    for path in paths:
        page.goto(live_server + path)
        warning = page.locator("[data-observation-warning]")
        expect(warning).to_be_visible()
        expect(warning).to_contain_text(
            {
                "stale": "Monitoring data is stale. RAID state is unknown.",
                "failed-poll": "Collection failed. Showing last known data",
                "first-poll": "No reliable collection yet. RAID state is unknown.",
            }[failure]
        )
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    if failure == "first-poll":
        assert fake_storcli.calls == []
        return
    if (
        failure == "stale"
        and width == 1440
        and (folder := os.environ.get("SCENARIO_SCREENSHOT_DIR"))
    ):
        directory = Path(folder)
        directory.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=directory / "monitoring-stale.png")
    with app.state.session_factory() as session, session.begin():
        session.execute(update(ControllerSnapshot).values(captured_at=datetime.now(UTC)))
    page.get_by_role("button", name="Refresh now").click()
    expect(page.locator("[data-observation-warning]")).to_have_count(0)
    assert fake_storcli.calls == []
