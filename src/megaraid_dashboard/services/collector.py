from __future__ import annotations

from typing import Any

from megaraid_dashboard.config import Settings
from megaraid_dashboard.storcli import (
    ForeignConfig,
    StorcliCommandFailed,
    StorcliError,
    StorcliSnapshot,
    parse_bbu,
    parse_cachevault,
    parse_controller_show_all,
    parse_foreign_config,
    parse_physical_drives,
    parse_virtual_drives,
    run_storcli,
)


async def collect_storcli_snapshot(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
    controller_payload = await _run(settings, "/c0 show all")
    virtual_drives_payload = await _run(settings, "/c0/vall show all")
    physical_drives_payload = await _run(settings, "/c0/eall/sall show all")

    cachevault_payload: dict[str, Any] | None = None
    cachevault = None
    try:
        cachevault_payload = await _run(settings, "/c0/cv show all")
        cachevault = parse_cachevault(cachevault_payload)
    except StorcliCommandFailed:
        cachevault = None

    bbu_payload: dict[str, Any] | None = None
    bbu = None
    try:
        bbu_payload = await _run(settings, "/c0/bbu show all")
        bbu = parse_bbu(bbu_payload)
    except StorcliError:
        bbu = None

    foreign_config_payload: dict[str, Any] | None = None
    foreign_config: ForeignConfig | None = None
    try:
        foreign_config_payload = await _run(settings, "/c0/fall show all")
        foreign_config = parse_foreign_config(foreign_config_payload)
    except StorcliError:
        # The "no foreign configuration" case is handled inside the parser as
        # ForeignConfig(present=False); this catch only swallows other storcli
        # failures (probe unsupported, transient errors) so a flaky probe does
        # not stall snapshot collection. The detector treats None as unknown
        # and preserves the last observed presence to avoid false transitions.
        foreign_config = None

    snapshot = StorcliSnapshot(
        controller=parse_controller_show_all(controller_payload),
        virtual_drives=parse_virtual_drives(virtual_drives_payload),
        physical_drives=parse_physical_drives(physical_drives_payload),
        cachevault=cachevault,
        bbu=bbu,
        foreign_config=foreign_config,
    )
    raw_payload = {
        "controller": controller_payload,
        "virtual_drives": virtual_drives_payload,
        "physical_drives": physical_drives_payload,
        "cachevault": cachevault_payload,
        "bbu": bbu_payload,
        "foreign_config": foreign_config_payload,
    }
    return snapshot, raw_payload


async def _run(settings: Settings, command: str) -> dict[str, Any]:
    return await run_storcli(
        command.split(),
        use_sudo=settings.storcli_use_sudo,
        binary_path=settings.storcli_path,
    )
