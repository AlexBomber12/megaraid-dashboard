"""End-to-end coverage of the drive locate LED start/stop flow via Playwright.

Relies on the ``snapshot_with_drives`` fixture so the addressed slots (252:0
and 252:1) resolve to real DB rows. The session storcli stub returns success
for any args, so the route produces a 200 with an audit record.

The CSRF cookie is parsed from the ``Set-Cookie`` header and re-sent via an
explicit ``Cookie`` header on each POST, matching the pattern used by the CSRF
and maintenance flow tests.
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


def test_locate_start_then_stop(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    del snapshot_with_drives
    token = _csrf_token_from_get(authenticated_page, live_server)
    csrf_headers = {
        "Cookie": f"__Host-csrf={token}",
        "X-CSRF-Token": token,
    }

    start = authenticated_page.request.post(
        f"{live_server}/drives/252:0/locate/start",
        headers=csrf_headers,
    )
    assert start.status == 200, f"locate start returned {start.status}"
    start_body = start.json()
    assert start_body["action"] == "start"
    assert start_body["enclosure"] == 252
    assert start_body["slot"] == 0

    stop = authenticated_page.request.post(
        f"{live_server}/drives/252:0/locate/stop",
        headers=csrf_headers,
    )
    assert stop.status == 200, f"locate stop returned {stop.status}"
    stop_body = stop.json()
    assert stop_body["action"] == "stop"
    assert stop_body["enclosure"] == 252
    assert stop_body["slot"] == 0
