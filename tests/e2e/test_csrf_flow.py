"""End-to-end CSRF round-trip tests via Playwright.

Exercises the `CsrfMiddleware`:
* A GET issues the ``__Host-csrf`` cookie.
* A POST without the ``X-CSRF-Token`` header is rejected with 403.
* A POST that echoes the cookie value in ``X-CSRF-Token`` passes the middleware.
"""

from __future__ import annotations

from playwright.sync_api import Page


def test_csrf_post_without_header_returns_403(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    # GET issues the cookie.
    get_response = authenticated_page.request.get(f"{live_server}/")
    assert get_response.status == 200
    set_cookie = get_response.headers.get("set-cookie", "")
    assert "__Host-csrf=" in set_cookie

    # The next POST sends the cookie automatically (Playwright manages the jar)
    # but omits the X-CSRF-Token header, so CsrfMiddleware must reject it.
    post_response = authenticated_page.request.post(
        f"{live_server}/maintenance/start",
        data={"duration_minutes": 5, "reason": "e2e csrf check"},
    )
    assert post_response.status == 403


def test_csrf_post_with_matching_header_passes_middleware(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    authenticated_page.goto(f"{live_server}/")
    cookies = authenticated_page.context.cookies(live_server)
    csrf_cookie = next(c for c in cookies if c["name"] == "__Host-csrf")
    token = csrf_cookie["value"]

    response = authenticated_page.request.post(
        f"{live_server}/maintenance/start",
        data={"duration_minutes": 5, "reason": "e2e csrf check"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status == 200
    body = response.json()
    assert body["active"] is True
