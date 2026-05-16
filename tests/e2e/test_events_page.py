"""End-to-end coverage of the events timeline page render.

``snapshot_with_drives`` seeds a controller snapshot and two physical
drives but no event rows, so this test exercises the empty-state path:
the route must still return 200 and the rendered HTML must reference
"event" (either in the page heading, the timeline header, or the
empty-state placeholder copy).
"""

from __future__ import annotations

from playwright.sync_api import Page


def test_events_page_renders_with_authenticated_user(
    authenticated_page: Page,
    live_server: str,
    snapshot_with_drives: str,
) -> None:
    del snapshot_with_drives
    response = authenticated_page.goto(f"{live_server}/events")
    assert response is not None
    assert response.status == 200, f"expected 200, got {response.status}"
    content = authenticated_page.content().lower()
    assert "event" in content, "events page should render with events table or empty state"
