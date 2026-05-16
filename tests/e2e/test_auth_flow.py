"""End-to-end coverage of the auth security perimeter via Playwright.

Each test resets the in-memory rate-limit attempt bucket via the autouse
``_reset_auth_rate_limit_attempts`` fixture in ``conftest.py``, so they can run
in any order against the session-scoped ``live_server``.
"""

from __future__ import annotations

import base64

from playwright.sync_api import Browser, Page


def test_unauthenticated_returns_401(live_server: str, page: Page) -> None:
    response = page.request.get(f"{live_server}/")
    assert response.status == 401
    headers = {key.lower() for key in response.headers}
    assert "www-authenticate" in headers


def test_login_succeeds_with_correct_credentials(
    live_server: str,
    browser: Browser,
    test_admin_creds: dict[str, str],
) -> None:
    context = browser.new_context(
        http_credentials={
            "username": test_admin_creds["username"],
            "password": test_admin_creds["password"],
        }
    )
    try:
        page = context.new_page()
        response = page.goto(f"{live_server}/")
        assert response is not None
        assert response.status == 200
        content = page.content()
        assert "MegaRAID" in content
    finally:
        context.close()


def test_wrong_password_rejected(live_server: str, browser: Browser) -> None:
    context = browser.new_context(
        http_credentials={"username": "admin", "password": "wrong-password"}
    )
    try:
        page = context.new_page()
        response = page.request.get(f"{live_server}/")
        assert response.status == 401
    finally:
        context.close()


def test_rate_limit_triggers_after_fifth_failure(live_server: str, page: Page) -> None:
    # With AUTH_RATE_LIMIT_PER_MINUTE=5 and AUTH_RATE_LIMIT_BURST=0 (set by the
    # e2e env in conftest.py), 5 failed attempts are allowed; the 6th is
    # rate-limited. The autouse reset fixture guarantees an empty bucket.
    token = base64.b64encode(b"admin:wrong-password").decode("ascii")
    auth_header = {"Authorization": f"Basic {token}"}
    statuses: list[int] = []
    for _ in range(6):
        response = page.request.get(f"{live_server}/", headers=auth_header)
        statuses.append(response.status)
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429
