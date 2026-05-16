"""End-to-end coverage of the maintenance start/stop flow via Playwright.

Walks the browser through the same sequence an operator would use: GET to
obtain the CSRF cookie, POST ``/maintenance/start``, confirm the banner shows,
POST ``/maintenance/stop``, confirm the banner is gone.
"""

from __future__ import annotations

from playwright.sync_api import Page


def _csrf_token(page: Page, live_server: str) -> str:
    page.goto(f"{live_server}/")
    cookies = page.context.cookies(live_server)
    return next(c["value"] for c in cookies if c["name"] == "__Host-csrf")


def test_maintenance_start_shows_banner(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    token = _csrf_token(authenticated_page, live_server)

    start = authenticated_page.request.post(
        f"{live_server}/maintenance/start",
        data={"duration_minutes": 5, "reason": "e2e maintenance start"},
        headers={"X-CSRF-Token": token},
    )
    assert start.status == 200

    authenticated_page.goto(f"{live_server}/")
    content = authenticated_page.content().lower()
    assert "maintenance" in content


def test_maintenance_stop_removes_banner(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    token = _csrf_token(authenticated_page, live_server)

    start = authenticated_page.request.post(
        f"{live_server}/maintenance/start",
        data={"duration_minutes": 5, "reason": "e2e maintenance stop setup"},
        headers={"X-CSRF-Token": token},
    )
    assert start.status == 200

    stop = authenticated_page.request.post(
        f"{live_server}/maintenance/stop",
        headers={"X-CSRF-Token": token},
    )
    assert stop.status == 200
    assert stop.json()["active"] is False

    authenticated_page.goto(f"{live_server}/")
    content = authenticated_page.content().lower()
    # The banner copy from PR-042 identifies an active maintenance window; once
    # /maintenance/stop returns, that exact marker must no longer be rendered.
    assert "maintenance is active" not in content
