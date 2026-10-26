from __future__ import annotations

from dataclasses import asdict
from typing import Any

from megaraid_dashboard.config import Settings
from megaraid_dashboard.services.drive_actions import (
    parse_consistency_check_status,
    parse_patrol_read_status,
)
from megaraid_dashboard.storcli import (
    ForeignConfig,
    StorcliError,
    StorcliParseError,
    StorcliSnapshot,
    ensure_command_succeeded,
    parse_bbu,
    parse_cachevault,
    parse_controller_show_all,
    parse_foreign_config,
    parse_physical_drives,
    parse_virtual_drives,
    run_storcli,
)
from megaraid_dashboard.storcli.parser import validate_complete_inventory


async def collect_storcli_snapshot(*, settings: Settings) -> tuple[StorcliSnapshot, dict[str, Any]]:
    controller_payload = await _run(settings, "/c0 show all")
    virtual_drives_payload = await _run(settings, "/c0/vall show all")
    physical_drives_payload = await _run(settings, "/c0/eall/sall show all")

    controller = parse_controller_show_all(controller_payload)
    virtual_drives = parse_virtual_drives(virtual_drives_payload)
    physical_drives = parse_physical_drives(physical_drives_payload)
    validate_complete_inventory(
        controller_payload,
        virtual_drives_payload,
        physical_drives_payload,
        virtual_drives,
        physical_drives,
    )

    # Unknown optional CV results must not become an absent hardware snapshot. Reject
    # the whole poll, retaining the last reliable baseline and using confirmed outage
    # handling. This also prevents a probe error from clearing an existing incident.
    cachevault_payload = await _run(settings, "/c0/cv show all")
    cachevault = parse_cachevault(cachevault_payload)
    if controller.cv_present != (cachevault is not None):
        raise StorcliParseError("CacheVault presence is unproven: controller/probe disagree")

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
        controller=controller,
        virtual_drives=virtual_drives,
        physical_drives=physical_drives,
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
    # Optional read-only operation probes. Unknown/unsupported state must not be
    # presented as an idle operation or prevent inventory collection.
    try:
        patrol = await _run(settings, "/c0 show patrolread")
        ensure_command_succeeded(patrol)
        raw_payload["patrol_read"] = asdict(parse_patrol_read_status(patrol))
    except StorcliError:
        raw_payload["patrol_read"] = None
    try:
        cc = await _run(settings, "/c0 show cc")
        ensure_command_succeeded(cc)
        progress = await _run(settings, "/c0/vall show cc")
        ensure_command_succeeded(progress)
        raw_payload["consistency_check"] = asdict(parse_consistency_check_status(cc, progress))
    except StorcliError:
        raw_payload["consistency_check"] = None
    return snapshot, raw_payload


async def _run(settings: Settings, command: str) -> dict[str, Any]:
    return await run_storcli(
        command.split(),
        use_sudo=settings.storcli_use_sudo,
        binary_path=settings.storcli_path,
    )
