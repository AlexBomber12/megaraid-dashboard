from __future__ import annotations

from playwright.sync_api import Page


def test_healthz_returns_200_via_playwright(live_server: str, page: Page) -> None:
    response = page.request.get(f"{live_server}/healthz")
    assert response.status == 200
    body = response.json()
    assert body["status"] == "ok"
