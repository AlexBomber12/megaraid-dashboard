"""Real canvas paint after HTMX settles, plus observation times bound to live drive state."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot, PhysicalDriveSnapshot
from tests.e2e.conftest import _LiveServerHandle
from tests.safety.harness import FakeStorcli

_PAINTED_CANVASES = """() => {
  const canvases = [...document.querySelectorAll('#chart-area canvas')];
  return canvases.length === 2 && canvases.every(canvas => {
    const rect = canvas.getBoundingClientRect();
    if (canvas.width < rect.width * devicePixelRatio - 2 ||
        canvas.height < rect.height * devicePixelRatio - 2) return false;
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  });
}"""


@pytest.fixture(params=[1.0, 1.5], ids=["standard-dpi", "windows-150-percent"])
def raster_page(
    browser: Browser, test_admin_creds: dict[str, str], request: pytest.FixtureRequest
) -> Iterator[Page]:
    context = browser.new_context(
        ignore_https_errors=True,
        device_scale_factor=request.param,
        http_credentials={
            "username": test_admin_creds["username"],
            "password": test_admin_creds["password"],
        },
    )
    context.route(
        "**/*",
        lambda route: (
            route.continue_()
            if urlsplit(route.request.url).hostname in {"localhost", "127.0.0.1", "::1"}
            else route.abort()
        ),
    )
    try:
        yield context.new_page()
    finally:
        context.close()


def test_range_swaps_finish_with_painted_canvases_without_forcing_redraw(
    raster_page: Page, live_server: str, snapshot_with_drives: str, fake_storcli: FakeStorcli
) -> None:
    page = raster_page
    page.goto(live_server + "/drives/252:4")
    page.wait_for_function(_PAINTED_CANVASES)
    # Animation frames can mask a bitmap reset. No resize()/update() may repair it in this test.
    page.evaluate("""() => {
      Chart.defaults.animation = false;
      document.body.dataset.chartSettles = '0';
      document.body.addEventListener('htmx:afterSettle', () => {
        document.body.dataset.chartSettles = String(Number(document.body.dataset.chartSettles) + 1);
      });
    }""")
    for days in (7, 365, 30, 7):
        previous = page.locator("body").get_attribute("data-chart-settles")
        page.get_by_role("tab", name=f"{days}d", exact=True).click()
        expect(page.locator("#chart-area")).to_have_attribute("data-range-days", str(days))
        page.wait_for_function(
            "previous => Number(document.body.dataset.chartSettles) > Number(previous)",
            arg=previous,
            timeout=5000,
        )
        page.wait_for_function(_PAINTED_CANVASES, timeout=5000)
        expect(page.get_by_role("tab", name=f"{days}d", exact=True)).to_have_attribute(
            "aria-selected", "true"
        )
        expect(page.get_by_role("heading", name="Temperature History", exact=True)).to_have_count(1)
        expect(page.get_by_role("heading", name="Error Counter History", exact=True)).to_have_count(
            1
        )
        assert page.evaluate("Object.keys(Chart.instances).length") == 2
    assert fake_storcli.calls == []


@pytest.mark.parametrize("automatic", [False, True], ids=["refresh-button", "automatic-timer"])
def test_drive_observed_header_refreshes_with_health(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: _LiveServerHandle,
    fake_storcli: FakeStorcli,
    automatic: bool,
) -> None:
    page = authenticated_page
    app = _live_server_handle.app
    before = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=1)
    after = before + timedelta(minutes=1)
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        assert snapshot is not None
        snapshot.captured_at = before
    page.clock.install(time=datetime.now(UTC))
    page.goto(live_server + "/drives/252:4")
    stamp = page.locator(".page-header time")
    expect(stamp).to_have_attribute("datetime", before.strftime("%Y-%m-%dT%H:%M:%SZ"))
    with app.state.session_factory() as session, session.begin():
        snapshot = session.scalar(select(ControllerSnapshot))
        drive = session.scalar(
            select(PhysicalDriveSnapshot).where(PhysicalDriveSnapshot.slot_id == 4)
        )
        assert snapshot is not None and drive is not None
        snapshot.captured_at = after
        drive.temperature_celsius = 41
    if automatic:
        page.clock.run_for(30_001)
    else:
        page.get_by_role("button", name="Refresh now", exact=True).click()
    expect(page.get_by_text("41°C", exact=True)).to_be_visible()
    expect(stamp).to_have_attribute("datetime", after.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert fake_storcli.calls == []
