"""End-to-end coverage for controller buzzer controls and audit visibility."""

from __future__ import annotations

from playwright.sync_api import Page, Request, expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot, Event, get_engine, get_sessionmaker
from tests.e2e.conftest import login_and_navigate


def _latest_event_summary(database_url: str, category: str) -> str | None:
    engine = get_engine(database_url)
    try:
        session_factory = get_sessionmaker(engine)
        with session_factory() as session:
            return session.scalar(
                select(Event.summary)
                .where(Event.category == category)
                .order_by(Event.occurred_at.desc(), Event.id.desc())
                .limit(1)
            )
    finally:
        engine.dispose()


def _set_latest_alarm_state(database_url: str, alarm_state: str) -> None:
    engine = get_engine(database_url)
    try:
        session_factory = get_sessionmaker(engine)
        with session_factory() as session, session.begin():
            snapshot = session.scalars(
                select(ControllerSnapshot).order_by(ControllerSnapshot.captured_at.desc()).limit(1)
            ).one()
            snapshot.alarm_state = alarm_state
    finally:
        engine.dispose()


def test_buzzer_control_flow_posts_with_csrf_and_updates_audit_log(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
    maintenance_mode: None,
) -> None:
    """Exercise Silence, Disable, and Enable controls with CSRF and audit log checks."""
    del maintenance_mode
    login_and_navigate(authenticated_page, f"{live_server}/controller")

    with (
        authenticated_page.expect_request(
            lambda request: (
                request.method == "POST" and request.url.endswith("/controller/buzzer/silence")
            )
        ) as silence_request_info,
        authenticated_page.expect_navigation(url="**/"),
    ):
        authenticated_page.get_by_role("button", name="Silence").click()
    _assert_csrf_header_present(silence_request_info.value)
    assert _latest_event_summary(snapshot_with_drives, "controller_buzzer_silence") is not None

    login_and_navigate(authenticated_page, f"{live_server}/audit")
    expect(authenticated_page.get_by_text("controller_buzzer_silence")).to_be_visible()

    login_and_navigate(authenticated_page, f"{live_server}/controller")
    with authenticated_page.expect_navigation(url="**/"):
        authenticated_page.get_by_role("button", name="Disable").click()
    assert _latest_event_summary(snapshot_with_drives, "controller_buzzer_disable") is not None

    _set_latest_alarm_state(snapshot_with_drives, "Off")
    login_and_navigate(authenticated_page, f"{live_server}/controller")
    expect(authenticated_page.get_by_text("Off")).to_be_visible()

    with authenticated_page.expect_navigation(url="**/"):
        authenticated_page.get_by_role("button", name="Enable").click()
    assert _latest_event_summary(snapshot_with_drives, "controller_buzzer_enable") is not None


def _assert_csrf_header_present(request: Request) -> None:
    token = request.headers.get("x-csrf-token")
    assert token is not None
    assert len(token) > 20
