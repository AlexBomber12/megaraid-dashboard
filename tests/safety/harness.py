"""Shared fake executable and persisted-state observations for browser/integration tests."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from megaraid_dashboard.db import (
    CacheVaultSnapshot,
    ControllerSnapshot,
    Event,
    PhysicalDriveSnapshot,
    VirtualDriveSnapshot,
    get_engine,
    get_sessionmaker,
)
from megaraid_dashboard.storcli.parser import parse_foreign_config
from tests.safety.scenarios import Action


@dataclass
class FakeStorcli:
    path: Path

    @classmethod
    def create(cls, root: Path) -> FakeStorcli:
        root.mkdir(parents=True, exist_ok=True)
        path = root / "storcli-fake"
        source = Path(__file__).with_name("fake_storcli.py").read_text()
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o700)
        fixture = Path(__file__).parents[1] / "fixtures/storcli/redacted"
        foreign = json.loads((fixture / "c0_fall_show_all_present.json").read_text())
        fake = cls(path)
        fake.configure(
            fault="success",
            read_fault="success",
            serial="WCA275678000",
            drive_state="UGood",
            mode="Manual",
            operation_state="Stopped",
            foreign=foreign["Controllers"][0]["Response Data"],
            controller=json.loads((fixture / "c0_show_all.json").read_text())["Controllers"][0][
                "Response Data"
            ],
        )
        return fake

    def configure(self, **values: Any) -> None:
        path = self.path.parent / "state.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        current.update(values)
        replacement = path.with_suffix(".tmp")
        replacement.write_text(json.dumps(current))
        replacement.replace(path)

    @property
    def calls(self) -> list[list[str]]:
        path = self.path.parent / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    @property
    def mutations(self) -> list[list[str]]:
        return [args for args in self.calls if "show" not in args]

    def clear(self) -> None:
        (self.path.parent / "calls.jsonl").unlink(missing_ok=True)

    def unavailable(self) -> None:
        self.path.unlink()


def database_observation(
    database_url: str,
) -> tuple[list[tuple[str, str]], dict[str, list[dict[str, Any]]]]:
    engine = get_engine(database_url)
    try:
        with get_sessionmaker(engine)() as session:
            events = list(session.execute(select(Event.category, Event.summary).order_by(Event.id)))
            snapshots = {}
            for model in (
                ControllerSnapshot,
                PhysicalDriveSnapshot,
                VirtualDriveSnapshot,
                CacheVaultSnapshot,
            ):
                table = model.__table__
                snapshots[table.name] = [
                    dict(row)
                    for row in session.execute(select(table).order_by(table.c.id)).mappings()
                ]
            return [tuple(row) for row in events], snapshots
    finally:
        engine.dispose()


def prepare(action: Action, handle: Any, fake: FakeStorcli) -> dict[str, Any]:
    app = handle.app
    app.state.settings = app.state.settings.model_copy(
        update={
            "maintenance_mode": True,
            "destructive_mode": True,
        }
    )
    fake.configure(
        drive_state=action.state,
        operation_state="Active" if action.name.endswith("-stop") else "Stopped",
    )
    with app.state.session_factory() as session, session.begin():
        if action.name == "buzzer-enable":
            session.execute(update(ControllerSnapshot).values(alarm_state="Off"))
        session.execute(
            update(PhysicalDriveSnapshot)
            .where(PhysicalDriveSnapshot.slot_id == 0)
            .values(state=action.state)
        )
        if action.name == "replace-insert":
            session.add(
                Event(
                    occurred_at=datetime.now(UTC),
                    severity="info",
                    category="operator_action",
                    subject="Operator action",
                    summary="replace step missing drive 252:0 serial OLD succeeded",
                )
            )
    if action.name == "buzzer-enable":
        config = json.loads((fake.path.parent / "state.json").read_text())
        config["controller"]["HwCfg"]["Alarm"] = "Off"
        fake.configure(controller=config["controller"])
    if action.family == "replace":
        return {"serial_number": "WCA275678000", "dry_run": False}
    if action.family == "foreign":
        config = json.loads((fake.path.parent / "state.json").read_text())
        digest = parse_foreign_config(
            {
                "Controllers": [
                    {
                        "Command Status": {"Status": "Success"},
                        "Response Data": config["foreign"],
                    }
                ]
            }
        ).digest
        return {
            "confirmation": digest if action.name == "foreign-import" else "CLEAR FOREIGN CONFIG",
            "dry_run": False,
        }
    if action.name.endswith("-mode"):
        return {"mode": "concurrent" if action.family == "consistency-check" else "auto"}
    if action.name == "hotspare":
        return {"dg_id": 0, "serial_number": "WCA275678000"}
    return {"serial_number": "WCA275678000"} if action.family == "advanced" else {}
