"""End-to-end coverage of the drive locate LED start/stop flow via Playwright.

Relies on the ``snapshot_with_drives`` fixture so the addressed slots (252:0
and 252:1) resolve to real DB rows. The session storcli stub returns success
for any args, so the route should produce a 200 with an audit record.
"""

from __future__ import annotations

from playwright.sync_api import Page


def _csrf_token(page: Page, live_server: str) -> str:
    page.goto(f"{live_server}/")
    cookies = page.context.cookies(live_server)
    return next(c["value"] for c in cookies if c["name"] == "__Host-csrf")


def test_locate_start_then_stop(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    del snapshot_with_drives
    token = _csrf_token(authenticated_page, live_server)

    start = authenticated_page.request.post(
        f"{live_server}/drives/252:0/locate/start",
        headers={"X-CSRF-Token": token},
    )
    assert start.status == 200
    start_body = start.json()
    assert start_body["action"] == "start"
    assert start_body["enclosure"] == 252
    assert start_body["slot"] == 0

    stop = authenticated_page.request.post(
        f"{live_server}/drives/252:0/locate/stop",
        headers={"X-CSRF-Token": token},
    )
    assert stop.status == 200
    stop_body = stop.json()
    assert stop_body["action"] == "stop"
    assert stop_body["enclosure"] == 252
    assert stop_body["slot"] == 0
