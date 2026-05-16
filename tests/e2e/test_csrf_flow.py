"""End-to-end CSRF round-trip coverage via Playwright.

Exercises the ``CsrfMiddleware``:

* A GET issues the ``__Host-csrf`` cookie via ``Set-Cookie``.
* A POST without the ``X-CSRF-Token`` header is rejected with 403.
* A POST that echoes the cookie value back in ``X-CSRF-Token`` (and re-sends
  the cookie explicitly) passes the middleware and reaches the route.

The cookie is parsed from the response ``Set-Cookie`` header rather than
``context.cookies``; the cookie carries the ``Secure`` attribute and some
browsers refuse to store it on plain ``http://`` even for localhost. Echoing
the value back via an explicit ``Cookie`` header avoids depending on that
browser behaviour.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page

_CSRF_COOKIE_RE = re.compile(r"__Host-csrf=([A-Za-z0-9_-]+)")


def _csrf_token_from_get(page: Page, live_server: str) -> str:
    response = page.request.get(f"{live_server}/")
    assert response.status == 200, f"GET / returned {response.status}"
    set_cookie = response.headers.get("set-cookie", "")
    match = _CSRF_COOKIE_RE.search(set_cookie)
    assert match is not None, f"no __Host-csrf in Set-Cookie: {set_cookie!r}"
    return match.group(1)


def test_csrf_get_issues_host_csrf_cookie(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    response = authenticated_page.request.get(f"{live_server}/")
    assert response.status == 200
    set_cookie = response.headers.get("set-cookie", "")
    assert "__Host-csrf=" in set_cookie
    # __Host- prefix must be paired with Secure + Path=/ + no Domain.
    assert "Secure" in set_cookie
    assert "Path=/" in set_cookie


def test_csrf_post_without_header_returns_403(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    token = _csrf_token_from_get(authenticated_page, live_server)

    # Re-send the cookie explicitly so the only missing piece is the header.
    # CsrfMiddleware must reject because X-CSRF-Token is absent.
    response = authenticated_page.request.post(
        f"{live_server}/maintenance/start",
        headers={"Cookie": f"__Host-csrf={token}"},
        data={"duration_minutes": 5, "reason": "e2e csrf check"},
    )
    assert response.status == 403


def test_csrf_post_with_matching_header_passes_middleware(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    token = _csrf_token_from_get(authenticated_page, live_server)

    response = authenticated_page.request.post(
        f"{live_server}/maintenance/start",
        headers={
            "Cookie": f"__Host-csrf={token}",
            "X-CSRF-Token": token,
        },
        data={"duration_minutes": 5, "reason": "e2e csrf check"},
    )
    assert response.status == 200
    body = response.json()
    assert body["active"] is True
