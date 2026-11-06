"""Real UI/API/prewrite/parser/persistence chain with fake hardware only."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import expect
from sqlalchemy import select

from megaraid_dashboard.db import ControllerSnapshot, Event
from megaraid_dashboard.db.dao import insert_snapshot
from megaraid_dashboard.services.collector import collect_storcli_snapshot
from megaraid_dashboard.storcli import run_storcli
from tests.e2e.conftest import login_and_navigate
from tests.safety.harness import FakeStorcli, prepare
from tests.safety.scenarios import ACTIONS

pytestmark = pytest.mark.usefixtures("full_controller_handlers")


def _collect(app: Any, fake: FakeStorcli, monkeypatch: pytest.MonkeyPatch) -> str:
    fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted"
    payloads = {
        command: json.loads((fixture / filename).read_text())
        for command, filename in {
            "/c0/vall show all": "vall_show_all.json",
            "/c0/eall/sall show all": "eall_sall_show_all.json",
            "/c0/cv show all": "cv_show_all.json",
            "/c0/bbu show all": "bbu_show_all.json",
            "/c0/fall show all": "c0_fall_show_all_absent.json",
        }.items()
    }

    async def read(args: list[str], **_: Any) -> dict[str, Any]:
        command = " ".join(args)
        assert "show" in args
        if command in payloads:
            return payloads[command]
        return await run_storcli(args, use_sudo=False, binary_path=str(fake.path))

    monkeypatch.setattr("megaraid_dashboard.services.collector.run_storcli", read)
    # Playwright's synchronous facade has its own event loop in this thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        snapshot, raw = pool.submit(
            lambda: asyncio.run(collect_storcli_snapshot(settings=app.state.settings))
        ).result(timeout=15)
    with app.state.session_factory() as session, session.begin():
        previous = session.scalar(
            select(ControllerSnapshot).order_by(ControllerSnapshot.id.desc()).limit(1)
        )
        # Simulate separate collector polls without waiting on real wall-clock cadence.
        snapshot.captured_at = max(
            datetime.now(UTC), previous.captured_at.replace(tzinfo=UTC) + timedelta(seconds=1)
        )
        saved = insert_snapshot(session, snapshot, raw_payload=raw)
        assert saved.alarm_state == snapshot.controller.alarm_state
    return snapshot.controller.alarm_state


def test_disable_collect_enable_collect_roundtrip(
    authenticated_page: Any,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: Any,
    fake_storcli: FakeStorcli,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = next(a for a in ACTIONS if a.name == "buzzer-disable")
    prepare(action, _live_server_handle, fake_storcli)
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    row = page.locator('[data-action-key="Alarm Buzzer"]')
    enable = row.locator('[data-buzzer-action="enable"] button')
    disable = row.locator('[data-buzzer-action="disable"] button')
    app = _live_server_handle.app
    for target, observed in (("disable", "Disable"), ("enable", "On")):
        button = disable if target == "disable" else enable
        with page.expect_response(
            lambda response, target=target: (
                response.url.endswith(f"/buzzer/{target}") and response.request.method == "POST"
            )
        ) as response:
            button.click()
        assert response.value.status == 303
        expect(row.locator("[data-controller-action-status]")).to_contain_text("Command accepted")
        # Selected appearance remains the previous saved observation until collection.
        expect(enable if target == "disable" else disable).to_have_attribute("aria-pressed", "true")
        assert _collect(app, fake_storcli, monkeypatch) == observed
        page.get_by_role("button", name="Refresh now", exact=True).click()
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(row.locator("[data-controller-action-status]")).to_be_hidden()
    assert fake_storcli.mutations == [
        ["/c0", "set", "alarm=off", "J"],
        ["/c0", "set", "alarm=on", "J"],
    ]
    with app.state.session_factory() as session:
        states = list(
            session.scalars(select(ControllerSnapshot.alarm_state).order_by(ControllerSnapshot.id))
        )
        summaries = list(session.scalars(select(Event.summary)))
    assert states[-2:] == ["Disable", "On"]
    assert len(summaries) == 2 and all("succeeded" in summary for summary in summaries)


def test_unknown_live_buzzer_is_a_known_no_write_refusal(
    authenticated_page: Any,
    live_server: str,
    snapshot_with_drives: str,
    _live_server_handle: Any,
    fake_storcli: FakeStorcli,
) -> None:
    action = next(a for a in ACTIONS if a.name == "buzzer-disable")
    prepare(action, _live_server_handle, fake_storcli)
    state = json.loads((fake_storcli.path.parent / "state.json").read_text())
    state["controller"]["HwCfg"]["Alarm"] = "Unknown"
    fake_storcli.configure(controller=state["controller"])
    page = authenticated_page
    login_and_navigate(page, f"{live_server}/controller")
    row = page.locator('[data-action-key="Alarm Buzzer"]')
    button = row.locator('[data-buzzer-action="disable"] button')
    with page.expect_response(
        lambda response: response.url.endswith(action.path) and response.request.method == "POST"
    ) as response:
        button.click()
    assert response.value.status == 409
    assert response.value.json()["execution"] == "not_sent"
    expect(row.locator("[data-controller-action-error]")).to_contain_text("Command not sent.")
    expect(row.locator("[data-controller-action-status]")).to_be_hidden()
    expect(row.locator('[data-buzzer-action="enable"] button')).to_have_attribute(
        "aria-pressed", "true"
    )
    expect(button).to_be_enabled()
    assert fake_storcli.mutations == []
