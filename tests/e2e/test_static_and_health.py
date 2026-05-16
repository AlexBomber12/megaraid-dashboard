"""End-to-end coverage of the unauthenticated static asset path.

``/static/`` is whitelisted from basic auth (see
``src/megaraid_dashboard/web/_whitelist.py``) so the dashboard CSS loads
cleanly on the login challenge page. This test uses the bare ``page``
fixture (no HTTP credentials configured on the browser context) to
verify that ``/static/css/app.css`` is served with a 200 status and a
``text/css`` content type.

Healthz is already covered by ``test_smoke.py``; this file intentionally
only exercises the static path so we do not duplicate coverage.
"""

from __future__ import annotations

from playwright.sync_api import Page


def test_static_css_served_without_auth(live_server: str, page: Page) -> None:
    response = page.request.get(f"{live_server}/static/css/app.css")
    assert response.status == 200, f"expected 200, got {response.status}"
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("text/css"), f"expected text/css, got {content_type!r}"
