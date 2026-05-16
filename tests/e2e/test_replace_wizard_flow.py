"""End-to-end coverage of the replace wizard Step 1 serial-mismatch path.

The destructive replace flow gates on a typed confirmation of the drive's
serial number. This test seeds a drive (snapshot serial ``WCA275678000``)
via the ``snapshot_with_drives`` fixture and POSTs ``/replace/offline``
with a deliberately wrong serial. The route must reject with 409 and the
JSON body must reference ``serial`` or ``mismatch`` so an operator can
see why the action was refused.

The CSRF cookie is parsed from the ``Set-Cookie`` header and re-sent via
an explicit ``Cookie`` header, matching the pattern used by the other
e2e flows that drive POST requests.
"""

from __future__ import annotations

import json
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


def test_replace_wizard_serial_mismatch_rejected(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    del snapshot_with_drives
    token = _csrf_token_from_get(authenticated_page, live_server)

    response = authenticated_page.request.post(
        f"{live_server}/drives/252:0/replace/offline",
        headers={
            "Cookie": f"__Host-csrf={token}",
            "X-CSRF-Token": token,
            "Content-Type": "application/json",
        },
        data=json.dumps({"serial_number": "WRONG-SERIAL-1234"}),
    )
    assert response.status == 409, f"expected 409, got {response.status}"
    body = response.text().lower()
    assert "serial" in body or "mismatch" in body
