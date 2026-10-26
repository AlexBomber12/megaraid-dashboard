"""Real collector/detector/persistence/notifier pipeline; only storcli and HTTP are mocked."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from megaraid_dashboard.alerts.telegram import TelegramConfig, TelegramTransport
from megaraid_dashboard.db.dao import (
    get_latest_snapshot,
    get_temp_state,
    record_event,
)
from megaraid_dashboard.db.engine import get_sessionmaker
from megaraid_dashboard.db.models import Event, EventDelivery
from megaraid_dashboard.services.event_detector import EventDetector
from megaraid_dashboard.services.scheduler import CollectorService
from megaraid_dashboard.services.telegram_notifier import run_telegram_cycle_async
from megaraid_dashboard.storcli import StorcliCommandFailed, StorcliSnapshot
from tests.test_alerts.test_telegram import CHAT_ID, FAKE_TOKEN
from tests.test_services.test_telegram_notifier import NOW, _settings

FIXTURES = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"


def _detector() -> EventDetector:
    return EventDetector(
        temp_warning=55,
        temp_critical=60,
        temp_hysteresis=5,
        roc_temp_warning=95,
        roc_temp_critical=105,
        roc_temp_hysteresis=5,
    )


class Pipeline:
    def __init__(self, session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
        self.session = session
        self.now = NOW
        self.payloads = {
            command: json.loads((FIXTURES / filename).read_text())
            for command, filename in (
                ("/c0", "c0_show_all.json"),
                ("/c0/vall", "vall_show_all.json"),
                ("/c0/eall/sall", "eall_sall_show_all.json"),
                ("/c0/cv", "cv_show_all.json"),
                ("/c0/bbu", "bbu_show_all.json"),
                ("/c0/fall", "c0_fall_show_all_absent.json"),
            )
        }
        self.payloads["/c0"]["Controllers"][0]["Response Data"]["HwCfg"][
            "ROC temperature(Degree Celsius)"
        ] = 80
        for key, detail in self.response("/c0/eall/sall").items():
            if key.endswith(" - Detailed Information"):
                detail[key.removesuffix(" - Detailed Information") + " State"][
                    "Drive Temperature"
                ] = "40C"
        self.original = copy.deepcopy(self.payloads)
        self.requests: list[dict[str, Any]] = []
        self.sender = TelegramTransport(
            TelegramConfig(chat_id=CHAT_ID, token=FAKE_TOKEN),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self._http)),
        )
        self.settings = _settings(collector_stale_alert_minutes=10)
        bind = session.get_bind()
        self.service = CollectorService(
            settings=self.settings,
            session_factory=get_sessionmaker(bind),
            event_detector=_detector(),
            clock=lambda: self.now,
        )

        async def run(args: list[str], **kwargs: Any) -> dict[str, Any]:
            result = self.payloads[args[0]]
            if isinstance(result, Exception):
                raise result
            return copy.deepcopy(result)

        from megaraid_dashboard.services import collector, scheduler
        from megaraid_dashboard.services.collector import collect_storcli_snapshot

        async def collect(**kwargs: Any) -> tuple[StorcliSnapshot, dict[str, Any]]:
            snapshot, raw = await collect_storcli_snapshot(**kwargs)
            return snapshot.model_copy(update={"captured_at": self.now}), raw

        def record(session: Session, **kwargs: Any) -> Event:
            event = record_event(session, **kwargs)
            event.occurred_at = self.now
            session.flush()
            return event

        monkeypatch.setattr(collector, "run_storcli", run)
        monkeypatch.setattr(scheduler, "collect_storcli_snapshot", collect)
        monkeypatch.setattr(scheduler, "record_event", record)

    def response(self, command: str) -> dict[str, Any]:
        return self.payloads[command]["Controllers"][0]["Response Data"]

    def _http(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    async def poll(self, minutes: int = 1) -> bool:
        self.now += timedelta(minutes=minutes)
        result = await self.service.run_once()
        self.session.expire_all()
        return result

    async def deliver(self) -> Any:
        return await run_telegram_cycle_async(
            self.session,
            settings=self.settings,
            now=self.now,
            transport_factory=lambda _: self.sender,
        )

    def remove(self, *, both: bool = False) -> None:
        controller = self.response("/c0")
        if both:
            controller.update(
                {"Physical Drives": 0, "PD LIST": [], "Virtual Drives": 0, "VD LIST": []}
            )
            self.payloads["/c0/eall/sall"]["Controllers"][0]["Response Data"] = {}
            self.payloads["/c0/vall"]["Controllers"][0]["Response Data"] = {}
        else:
            controller["Physical Drives"] -= 1
            controller["PD LIST"] = [d for d in controller["PD LIST"] if d["EID:Slt"] != "252:0"]
            physical = self.response("/c0/eall/sall")
            del physical["Drive /c0/e252/s0"]
            del physical["Drive /c0/e252/s0 - Detailed Information"]

    def restore(self) -> None:
        self.payloads = copy.deepcopy(self.original)

    def restart(self) -> None:
        self.service.event_detector = _detector()
        self.session.close()

    def events(self, category: str, severity: str = "critical") -> list[Event]:
        return list(
            self.session.scalars(
                select(Event)
                .where(Event.category == category, Event.severity == severity)
                .order_by(Event.id)
            )
        )


@pytest.fixture
def pipeline(session: Session, monkeypatch: pytest.MonkeyPatch) -> Pipeline:
    return Pipeline(session, monkeypatch)


@pytest.mark.parametrize("both", [False, True])
async def test_confirmed_disappearance_delivery_restart_recovery_and_recurrence(
    pipeline: Pipeline,
    both: bool,
) -> None:
    assert await pipeline.poll()
    original = get_latest_snapshot(pipeline.session).physical_drives[0].serial_number
    pipeline.remove(both=both)
    assert await pipeline.poll()
    missing = pipeline.events("pd_state")[0]
    missing_id = missing.id
    assert missing.after_json == {"present": False, "state": "Missing", "serial_number": original}
    assert (
        get_temp_state(pipeline.session, enclosure_id=252, slot_id=0, serial_number=original)
        is None
    )
    count = 9 if both else 1
    # More than five disappeared objects drain across bounded cycles.
    sent = 0
    for _ in range(2):
        sent += (await pipeline.deliver()).sent
        assert await pipeline.poll()
    assert sent == count
    pipeline.restart()
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 0
    assert len(pipeline.events("pd_state")) == (8 if both else 1)
    pipeline.restore()
    assert await pipeline.poll()
    pipeline.remove(both=both)
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == min(count, 5)
    # No markup or links, and no SMTP marker was changed by Telegram.
    assert all(
        set(request) == {"chat_id", "text", "disable_web_page_preview"}
        for request in pipeline.requests
    )
    assert all(
        request["chat_id"] == CHAT_ID and request["disable_web_page_preview"] is True
        for request in pipeline.requests
    )
    assert all("http" not in request["text"] for request in pipeline.requests)
    assert pipeline.session.get(Event, missing_id).notified_at is None


@pytest.mark.parametrize("replacement", [False, True])
async def test_delayed_missing_incident_is_obsolete_after_recovery_or_replacement(
    pipeline: Pipeline,
    replacement: bool,
) -> None:
    assert await pipeline.poll()
    pipeline.remove()
    assert await pipeline.poll()
    pipeline.restore()
    if replacement:
        detail = pipeline.response("/c0/eall/sall")["Drive /c0/e252/s0 - Detailed Information"]
        detail["Drive /c0/e252/s0 Device attributes"]["SN"] = "REPLACEMENT"
    assert await pipeline.poll()
    pipeline.restart()
    assert (await pipeline.deliver()).sent == 0
    delivery = pipeline.session.scalar(select(EventDelivery))
    assert delivery.status == "skipped" and delivery.detail == "obsolete"


async def test_queued_before_disappearance_revalidates_against_absence(pipeline: Pipeline) -> None:
    controller_pd = pipeline.response("/c0")["PD LIST"][0]
    controller_pd["State"] = " Missing "
    pipeline.response("/c0/eall/sall")["Drive /c0/e252/s0"][0]["State"] = "mSnG"
    assert await pipeline.poll()
    old = pipeline.events("pd_state")[0]
    # A separate queued counter incident must not survive loss of its current reading.
    counter = record_event(
        pipeline.session,
        severity="critical",
        category="media_errors",
        subject=old.subject,
        summary="counter",
        after={"media_errors": 1, "serial_number": old.after_json["serial_number"]},
    )
    counter.occurred_at = pipeline.now
    pipeline.session.commit()
    pipeline.remove()
    assert await pipeline.poll()
    result = await pipeline.deliver()
    assert result.sent == 1
    assert "диск не обнаружен." in pipeline.requests[0]["text"]
    assert len(pipeline.requests) == 1
    assert (
        pipeline.session.scalar(
            select(EventDelivery).where(EventDelivery.event_id == counter.id)
        ).detail
        == "obsolete"
    )


@pytest.mark.parametrize("critical", [False, True])
async def test_initial_hot_controller_confirms_once_survives_restart_and_recurs(
    pipeline: Pipeline,
    critical: bool,
) -> None:
    reading = 110 if critical else 98
    pipeline.response("/c0")["HwCfg"]["ROC temperature(Degree Celsius)"] = reading
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == int(critical)
    pipeline.restart()
    assert await pipeline.poll(minutes=15)
    assert (await pipeline.deliver()).sent == int(not critical)
    assert len(pipeline.requests) == 1
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 0
    pipeline.response("/c0")["HwCfg"]["ROC temperature(Degree Celsius)"] = 80
    assert await pipeline.poll()
    pipeline.response("/c0")["HwCfg"]["ROC temperature(Degree Celsius)"] = reading
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == int(critical)
    if not critical:
        assert await pipeline.poll(minutes=15)
        assert (await pipeline.deliver()).sent == 1
    assert len(pipeline.requests) == 2


@pytest.mark.parametrize("error", ["command", "firmware", "unsupported", "malformed"])
async def test_cv_probe_uncertainty_preserves_incident_and_becomes_outage(
    pipeline: Pipeline,
    error: str,
) -> None:
    pipeline.response("/c0/cv")["Cachevault_Info"][2]["Value"] = " FAILED "
    assert await pipeline.poll()
    fault_id = pipeline.events("cv_state")[0].id
    before = get_latest_snapshot(pipeline.session).id
    if error == "command":
        pipeline.payloads["/c0/cv"] = StorcliCommandFailed("probe error")
    elif error == "malformed":
        pipeline.payloads["/c0/cv"]["Controllers"][0]["Response Data"] = {}
    else:
        pipeline.payloads["/c0/cv"] = {
            "Controllers": [
                {
                    "Command Status": {
                        "Status": "Failure",
                        "Description": "use /cx/bbu"
                        if error == "unsupported"
                        else "firmware fault",
                    }
                }
            ]
        }
    for _ in range(3):
        assert not await pipeline.poll(minutes=5)
    pipeline.restart()
    assert get_latest_snapshot(pipeline.session).id == before
    assert len(pipeline.events("cv_state")) == 1
    assert (await pipeline.deliver()).sent == 1  # Confirmed monitoring outage only.
    assert "мониторинг недоступен" in pipeline.requests[0]["text"]
    pending = pipeline.session.scalar(
        select(EventDelivery).where(EventDelivery.event_id == fault_id)
    )
    assert pending.status == "retry" and pending.attempts == 0
    pipeline.restore()
    pipeline.response("/c0/cv")["Cachevault_Info"][2]["Value"] = "failed"
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 1
    assert len(pipeline.events("cv_state")) == 1


async def test_confirmed_cachevault_loss_is_distinct_from_unknown_probe(pipeline: Pipeline) -> None:
    assert await pipeline.poll()
    pipeline.response("/c0")["Cachevault_Info"] = []
    pipeline.payloads["/c0/cv"]["Controllers"][0]["Response Data"] = {"Cachevault_Info": []}
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 1
    assert "защита кэша не обнаружена." in pipeline.requests[0]["text"]
    pipeline.restart()
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 0
    pipeline.restore()
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 0


@pytest.mark.parametrize(
    "damage",
    [
        "no_counts",
        "partial_pd",
        "partial_vd",
        "both_vanish",
        "bad_state",
        "duplicate",
        "missing_serial",
        "bad_count",
        "unknown_status",
        "wrong_controller",
        "cv_disagree",
        "bad_pd_list",
        "bad_cv_info",
        "duplicate_vd",
        "vd_identity",
        "pd_identity",
        "missing_status",
        "multiple_controllers",
        "cv_wrong_controller",
    ],
)
async def test_incomplete_inventory_is_uncertainty_never_hardware_failure(
    pipeline: Pipeline,
    damage: str,
) -> None:
    assert await pipeline.poll()
    before = get_latest_snapshot(pipeline.session).id
    controller = pipeline.response("/c0")
    if damage == "no_counts":
        del controller["Physical Drives"]
    elif damage == "partial_pd":
        del pipeline.response("/c0/eall/sall")["Drive /c0/e252/s0"]
    elif damage in {"partial_vd", "both_vanish"}:
        pipeline.payloads["/c0/vall"]["Controllers"][0]["Response Data"] = {}
        if damage == "both_vanish":
            pipeline.payloads["/c0/eall/sall"]["Controllers"][0]["Response Data"] = {}
    elif damage == "bad_pd_list":
        controller["PD LIST"] = {}
    elif damage == "bad_cv_info":
        controller["Cachevault_Info"] = "Unknown"
    elif damage == "duplicate_vd":
        controller["Virtual Drives"] = 2
        controller["VD LIST"] *= 2
        pipeline.response("/c0/vall")["/c0/v0"] *= 2
    elif damage == "vd_identity":
        controller["VD LIST"][0]["DG/VD"] = "0/1"
    elif damage == "pd_identity":
        controller["PD LIST"][0]["DID"] = 999
    elif damage == "missing_status":
        del pipeline.payloads["/c0/eall/sall"]["Controllers"][0]["Command Status"]
    elif damage == "multiple_controllers":
        pipeline.payloads["/c0/eall/sall"]["Controllers"] *= 2
    elif damage == "cv_wrong_controller":
        pipeline.payloads["/c0/cv"]["Controllers"][0]["Command Status"]["Controller"] = 1
    elif damage == "bad_state":
        controller["PD LIST"][0]["State"] = 42
    elif damage == "duplicate":
        row = pipeline.response("/c0/eall/sall")["Drive /c0/e252/s1"][0]
        row["EID:Slt"] = "252:0"
    elif damage == "missing_serial":
        pipeline.response("/c0/eall/sall")["Drive /c0/e252/s0 - Detailed Information"][
            "Drive /c0/e252/s0 Device attributes"
        ]["SN"] = " "
    elif damage == "bad_count":
        controller["Physical Drives"] = "8"
    elif damage in {"unknown_status", "wrong_controller"}:
        status = pipeline.payloads["/c0/eall/sall"]["Controllers"][0]["Command Status"]
        status["Status" if damage == "unknown_status" else "Controller"] = (
            "Unsupported" if damage == "unknown_status" else 1
        )
    else:
        controller["Cachevault_Info"] = []
    assert not await pipeline.poll()
    assert get_latest_snapshot(pipeline.session).id == before
    assert (
        not pipeline.events("pd_state")
        and not pipeline.events("vd_state")
        and not pipeline.events("cv_state")
    )
    assert (await pipeline.deliver()).sent == 0
    assert len(pipeline.events("system", "warning")) == 1


async def test_failed_probe_invalidates_even_a_young_reliable_snapshot(pipeline: Pipeline) -> None:
    pipeline.response("/c0/cv")["Cachevault_Info"][2]["Value"] = "Failed"
    assert await pipeline.poll()
    fault_id = pipeline.events("cv_state")[0].id
    pipeline.payloads["/c0/cv"] = StorcliCommandFailed("temporary failure")
    assert not await pipeline.poll()
    pipeline.restart()
    result = await pipeline.deliver()
    assert result.sent == 0 and result.deferred == 1
    pending = pipeline.session.scalar(
        select(EventDelivery).where(EventDelivery.event_id == fault_id)
    )
    assert pending.detail == "awaiting_fresh_data" and pending.attempts == 0
    pipeline.restore()
    pipeline.response("/c0/cv")["Cachevault_Info"][2]["Value"] = " failed "
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 1
    assert len(pipeline.events("cv_state")) == 1


@pytest.mark.parametrize("recovered_before_delivery", [False, True])
async def test_cachevault_absence_recovery_and_recurrence(
    pipeline: Pipeline,
    recovered_before_delivery: bool,
) -> None:
    assert await pipeline.poll()
    pipeline.response("/c0")["Cachevault_Info"] = []
    pipeline.payloads["/c0/cv"]["Controllers"][0]["Response Data"] = {"Cachevault_Info": []}
    assert await pipeline.poll()
    first_id = pipeline.events("cv_state")[0].id
    if not recovered_before_delivery:
        assert (await pipeline.deliver()).sent == 1
    pipeline.restore()
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 0
    if recovered_before_delivery:
        row = pipeline.session.scalar(
            select(EventDelivery).where(EventDelivery.event_id == first_id)
        )
        assert row.detail == "obsolete"
    pipeline.response("/c0")["Cachevault_Info"] = []
    pipeline.payloads["/c0/cv"]["Controllers"][0]["Response Data"] = {"Cachevault_Info": []}
    assert await pipeline.poll()
    pipeline.restart()
    assert (await pipeline.deliver()).sent == 1


@pytest.mark.parametrize(
    ("first", "alias"),
    [
        ("Failed", " FAILED "),
        ("Missing", " msng "),
        ("Offline", " Offln "),
        ("Optimal", " Optl "),
    ],
)
async def test_disk_alias_pipeline_relevance_dedup_and_recurrence(
    pipeline: Pipeline,
    first: str,
    alias: str,
) -> None:
    def state(value: str) -> None:
        pipeline.response("/c0")["PD LIST"][0]["State"] = value
        pipeline.response("/c0/eall/sall")["Drive /c0/e252/s0"][0]["State"] = value

    state(first)
    assert await pipeline.poll()
    state(alias)
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == int(first != "Optimal")
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == 0
    assert len(pipeline.events("pd_state")) == int(first != "Optimal")
    state("Onln")
    assert await pipeline.poll()
    state(alias)
    assert await pipeline.poll()
    assert (await pipeline.deliver()).sent == int(first != "Optimal")


@pytest.mark.parametrize(("category", "state"), [("pd_state", "Missing"), ("vd_state", "Offline")])
async def test_unknown_identity_or_returned_object_cannot_receive_stale_absence(
    pipeline: Pipeline,
    category: str,
    state: str,
) -> None:
    assert await pipeline.poll()
    pipeline.remove(both=True)
    assert await pipeline.poll()
    # Direct legacy metadata without a serial must still wait, even for absent slots.
    if category == "pd_state":
        event = pipeline.events("pd_state")[0]
        event.after_json = {"state": state, "present": False}
        pipeline.session.commit()
        from megaraid_dashboard.services.telegram_relevance import delivery_relevance

        assert (
            delivery_relevance(
                pipeline.session, event, settings=pipeline.settings, now=pipeline.now
            )
            == "awaiting_drive_identity"
        )
    pipeline.restore()
    # Return the object in the exact unhealthy state: presence recovery still ends absence.
    if category == "pd_state":
        pipeline.response("/c0")["PD LIST"][0]["State"] = state
        pipeline.response("/c0/eall/sall")["Drive /c0/e252/s0"][0]["State"] = state
    else:
        pipeline.response("/c0")["VD LIST"][0]["State"] = state
        pipeline.response("/c0/vall")["/c0/v0"][0]["State"] = state
    assert await pipeline.poll()
    result = await pipeline.deliver()
    assert result.sent == 1


async def test_queued_array_offline_survives_disappearance_as_one_incident(
    pipeline: Pipeline,
) -> None:
    pipeline.response("/c0")["VD LIST"][0]["State"] = " OfLn "
    pipeline.response("/c0/vall")["/c0/v0"][0]["State"] = "offline"
    assert await pipeline.poll()
    old_id = pipeline.events("vd_state")[0].id
    pipeline.response("/c0").update({"Virtual Drives": 0, "VD LIST": []})
    pipeline.payloads["/c0/vall"]["Controllers"][0]["Response Data"] = {}
    assert await pipeline.poll()
    pipeline.restart()
    result = await pipeline.deliver()
    assert result.sent == 1 and result.skipped == 1
    assert pipeline.requests[0]["text"].endswith(f"· №{old_id}")
    assert "массив недоступен." in pipeline.requests[0]["text"]


async def test_zero_counts_prove_empty_inventory_when_empty_tables_are_omitted(
    pipeline: Pipeline,
) -> None:
    assert await pipeline.poll()
    pipeline.remove(both=True)
    del pipeline.response("/c0")["PD LIST"]
    del pipeline.response("/c0")["VD LIST"]
    assert await pipeline.poll()
    assert len(pipeline.events("pd_state")) == 8
    assert len(pipeline.events("vd_state")) == 1
