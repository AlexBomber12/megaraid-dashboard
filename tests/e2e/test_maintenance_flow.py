"""End-to-end coverage of the maintenance start/stop flow via Playwright.

Walks the browser through the same sequence an operator would use:

* GET ``/`` to obtain the ``__Host-csrf`` cookie.
* POST ``/maintenance/start`` with a matching token; confirm the maintenance
  banner is rendered on the overview page.
* POST ``/maintenance/stop`` with the same token; confirm the banner is gone.

The CSRF cookie is parsed from the ``Set-Cookie`` header and re-sent via an
explicit ``Cookie`` header on each POST so the test does not depend on a
browser storing a ``Secure`` cookie issued over plain ``http://``.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page

_CSRF_COOKIE_RE = re.compile(r"__Host-csrf=([A-Za-z0-9_-]+)")
_MAINTENANCE_BANNER_MARKER = 'class="maintenance-banner"'


def _csrf_token_from_get(page: Page, live_server: str) -> str:
    response = page.request.get(f"{live_server}/")
    assert response.status == 200, f"GET / returned {response.status}"
    set_cookie = response.headers.get("set-cookie", "")
    match = _CSRF_COOKIE_RE.search(set_cookie)
    assert match is not None, f"no __Host-csrf in Set-Cookie: {set_cookie!r}"
    return match.group(1)


def _post_maintenance_start(
    page: Page,
    live_server: str,
    token: str,
    *,
    reason: str,
) -> None:
    response = page.request.post(
        f"{live_server}/maintenance/start",
        headers={
            "Cookie": f"__Host-csrf={token}",
            "X-CSRF-Token": token,
        },
        data={"duration_minutes": 5, "reason": reason},
    )
    assert response.status == 200, f"maintenance start returned {response.status}"
    assert response.json()["active"] is True


def test_maintenance_start_renders_banner_on_overview(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    token = _csrf_token_from_get(authenticated_page, live_server)
    _post_maintenance_start(
        authenticated_page,
        live_server,
        token,
        reason="e2e maintenance start",
    )

    authenticated_page.goto(f"{live_server}/")
    content = authenticated_page.content()
    assert _MAINTENANCE_BANNER_MARKER in content
    assert "Maintenance mode active" in content


def test_maintenance_stop_clears_banner(
    authenticated_page: Page,
    live_server: str,
    fresh_db: str,
) -> None:
    del fresh_db
    token = _csrf_token_from_get(authenticated_page, live_server)
    _post_maintenance_start(
        authenticated_page,
        live_server,
        token,
        reason="e2e maintenance stop setup",
    )

    stop = authenticated_page.request.post(
        f"{live_server}/maintenance/stop",
        headers={
            "Cookie": f"__Host-csrf={token}",
            "X-CSRF-Token": token,
        },
    )
    assert stop.status == 200
    assert stop.json()["active"] is False

    authenticated_page.goto(f"{live_server}/")
    content = authenticated_page.content()
    assert _MAINTENANCE_BANNER_MARKER not in content
