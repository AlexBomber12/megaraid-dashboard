from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from megaraid_dashboard.config import Settings
from megaraid_dashboard.services.collector import collect_storcli_snapshot
from megaraid_dashboard.storcli import StorcliNotAvailable, StorcliSnapshot

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "storcli" / "redacted"


async def test_collect_storcli_snapshot_assembles_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool, str]] = []
    payloads = {
        "/c0 show all": _load_fixture("c0_show_all.json"),
        "/c0/vall show all": _load_fixture("vall_show_all.json"),
        "/c0/eall/sall show all": _load_fixture("eall_sall_show_all.json"),
        "/c0/cv show all": _load_fixture("cv_show_all.json"),
        "/c0/bbu show all": _load_fixture("bbu_show_all.json"),
    }

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        del timeout_seconds
        calls.append((args, use_sudo, binary_path))
        return payloads[" ".join(args)]

    monkeypatch.setattr(
        "megaraid_dashboard.services.collector.run_storcli",
        fake_run_storcli,
    )

    snapshot, raw_payload = await collect_storcli_snapshot(settings=_settings())

    assert isinstance(snapshot, StorcliSnapshot)
    assert len(snapshot.virtual_drives) == 1
    assert len(snapshot.physical_drives) == 8
    assert snapshot.cachevault is not None
    assert snapshot.bbu is None
    assert raw_payload["controller"] == payloads["/c0 show all"]
    assert raw_payload["bbu"] == payloads["/c0/bbu show all"]
    assert [call[0] for call in calls] == [
        ["/c0", "show", "all"],
        ["/c0/vall", "show", "all"],
        ["/c0/eall/sall", "show", "all"],
        ["/c0/cv", "show", "all"],
        ["/c0/bbu", "show", "all"],
    ]
    assert all(use_sudo is True for _, use_sudo, _ in calls)
    assert all(binary_path == "/custom/storcli64" for _, _, binary_path in calls)


async def test_collect_storcli_snapshot_treats_bbu_storcli_errors_as_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payloads = {
        "/c0 show all": _load_fixture("c0_show_all.json"),
        "/c0/vall show all": _load_fixture("vall_show_all.json"),
        "/c0/eall/sall show all": _load_fixture("eall_sall_show_all.json"),
        "/c0/cv show all": _load_fixture("cv_show_all.json"),
    }

    async def fake_run_storcli(
        args: list[str],
        *,
        use_sudo: bool,
        binary_path: str,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        del use_sudo, binary_path, timeout_seconds
        command = " ".join(args)
        if command == "/c0/bbu show all":
            raise StorcliNotAvailable("sudoers does not allow bbu probe")
        return payloads[command]

    monkeypatch.setattr(
        "megaraid_dashboard.services.collector.run_storcli",
        fake_run_storcli,
    )

    snapshot, raw_payload = await collect_storcli_snapshot(settings=_settings())

    assert len(snapshot.physical_drives) == 8
    assert snapshot.bbu is None
    assert raw_payload["bbu"] is None


def _settings() -> Settings:
    return Settings(
        alert_smtp_host="smtp.example.test",
        alert_smtp_port=587,
        alert_smtp_user="alert@example.test",
        alert_smtp_password="test-token",
        alert_from="alert@example.test",
        alert_to="ops@example.test",
        admin_username="admin",
        admin_password_hash="test-bcrypt-hash",
        storcli_path="/custom/storcli64",
        storcli_use_sudo=True,
        metrics_interval_seconds=300,
        database_url="sqlite:///:memory:",
        log_level="INFO",
    )


def _load_fixture(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload
