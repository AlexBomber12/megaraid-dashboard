"""Action-specific live evidence for approved writes; no topology is inferred."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from megaraid_dashboard.storcli.exceptions import StorcliCommandFailed, StorcliError
from megaraid_dashboard.storcli.parser import parse_controller_show_all, parse_foreign_config
from megaraid_dashboard.storcli.policy import CommandPolicy

ReadCommand = Callable[[list[str]], Awaitable[dict[str, Any]]]


class _UnprovenSafetyError(ValueError):
    """Bounded, application-owned refusal detail; never validation input."""


class _Status(BaseModel):
    model_config = ConfigDict(strict=True)
    controller: int = Field(alias="Controller", ge=0, le=0)
    status: Literal["Success"] = Field(alias="Status")


class _Controller(BaseModel):
    status: _Status = Field(alias="Command Status")


class _ReadResponse(BaseModel):
    controllers: list[_Controller] = Field(alias="Controllers", min_length=1, max_length=1)


class _VD(BaseModel):
    model_config = ConfigDict(strict=True)
    identity: str = Field(alias="DG/VD", pattern=r"^\d+/\d+$")


class _VDInventory(BaseModel):
    model_config = ConfigDict(strict=True)
    count: int = Field(alias="Virtual Drives", ge=0)
    drives: list[_VD] = Field(alias="VD LIST")


class _GroupRow(BaseModel):
    model_config = ConfigDict(strict=True)
    group: int | Literal["-"] = Field(alias="DG")
    array: int | Literal["-"] = Field(alias="Arr")
    row: int | Literal["-"] = Field(alias="Row")


class _GroupInventory(BaseModel):
    model_config = ConfigDict(strict=True)
    count: int = Field(alias="Drive Groups", ge=0)
    rows: list[_GroupRow] = Field(alias="TOPOLOGY")


class _Drive(BaseModel):
    model_config = ConfigDict(strict=True)
    location: str = Field(alias="EID:Slt", pattern=r"^252:[0-7]$")
    device_id: int = Field(alias="DID", ge=0)
    state: Literal[
        "Onln", "UGood", "UBad", "Offln", "Failed", "Rbld", "DHS", "GHS", "Msng", "JBOD"
    ] = Field(alias="State")


class _PhysicalInventory(BaseModel):
    model_config = ConfigDict(strict=True)
    count: int = Field(alias="Physical Drives", ge=1)
    drives: list[_Drive] = Field(alias="PD LIST", min_length=1)


class _IdleRebuildProof(BaseModel):
    model_config = ConfigDict(strict=True)
    status: Literal["not in progress", "idle", "none", "complete", "completed"] = Field(
        validation_alias=AliasChoices("Status", "State")
    )

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, value: Any) -> Any:
        return value.strip().casefold() if isinstance(value, str) else value


def check_action_topology(args: list[str], identity: dict[str, Any], policy: CommandPolicy) -> None:
    """CC/configured replacement needs the VD/DG; hot spare needs only its DG."""
    data = identity["Controllers"][0]["Response Data"]
    try:
        if (
            args[0].startswith("/c0/v")
            or args[2].startswith("cc=")
            or args[1] == "insert"
            or args[1:3] in (["set", "offline"], ["set", "missing"])
        ):
            inventory = _VDInventory.model_validate(data)
            identities = [drive.identity for drive in inventory.drives]
            per_vd_start = args[0] == f"/c0/v{policy.virtual_drive}" and args[1:3] == [
                "start",
                "cc",
            ]
            if (
                inventory.count != len(identities)
                or len(set(identities)) != len(identities)
                or f"{policy.disk_group}/{policy.virtual_drive}" not in identities
                or (not per_vd_start and len(identities) != 1)
            ):
                raise ValueError("approved local VD/DG missing or changed")
        elif args[1:3] == ["add", "hotsparedrive"]:
            groups = _GroupInventory.model_validate(data)
            headers = [
                row.group
                for row in groups.rows
                if row.array == "-" and row.row == "-" and type(row.group) is int
            ]
            if (
                groups.count != len(headers)
                or len(set(headers)) != len(headers)
                or policy.disk_group not in headers
            ):
                raise ValueError("approved disk group missing or changed")
    except (ValidationError, ValueError) as exc:
        raise StorcliCommandFailed(
            "Approved local VD/DG topology cannot be verified for this action. "
            "Refresh current controller topology and have the administrator review the policy."
        ) from exc


def _physical_inventory(identity: dict[str, Any]) -> _PhysicalInventory:
    _ReadResponse.model_validate(identity)
    inventory = _PhysicalInventory.model_validate(identity["Controllers"][0]["Response Data"])
    if (
        inventory.count != len(inventory.drives)
        or len({row.location for row in inventory.drives}) != inventory.count
        or len({row.device_id for row in inventory.drives}) != inventory.count
    ):
        raise ValueError("physical inventory incomplete or ambiguous")
    return inventory


def _foreign_evidence(payload: dict[str, Any], expected_digest: str) -> str:
    _ReadResponse.model_validate(payload)
    config = parse_foreign_config(payload)
    if not config.present or config.digest != expected_digest:
        raise _UnprovenSafetyError(
            "foreign configuration changed; reload and confirm its current digest"
        )
    return json.dumps(payload["Controllers"][0]["Response Data"], sort_keys=True, allow_nan=False)


async def check_foreign_safety(
    identity: dict[str, Any],
    policy: CommandPolicy,
    read: ReadCommand,
    expected_digest: str | None,
) -> None:
    """Require fresh complete membership, explicit idle rebuilds and unchanged confirmation."""
    # Import lazily because the action parsers also import the public wrapper API.
    from megaraid_dashboard.services.drive_actions import parse_rebuild_status

    if not expected_digest:
        raise StorcliCommandFailed(
            "Foreign confirmation is missing. Reload Foreign Configuration "
            "and confirm its current digest."
        )
    try:
        inventory = _physical_inventory(identity)
        foreign_before = _foreign_evidence(
            await read(["/c0/fall", "show", "all", "J"]), expected_digest
        )
        for drive in inventory.drives:
            if drive.state == "Rbld":
                raise _UnprovenSafetyError(f"rebuild active at {drive.location}")
            path = f"/c0/e252/s{drive.location.split(':')[1]}"
            payload = await read([path, "show", "rebuild", "J"])
            _ReadResponse.model_validate(payload)
            status = parse_rebuild_status(payload, requested_drive=path)
            if status.state not in {"Not in progress", "Complete"}:
                raise _UnprovenSafetyError(f"rebuild {status.state} at {drive.location}")
            data = payload["Controllers"][0]["Response Data"]
            rows = data if isinstance(data, list) else next(iter(data.values()))
            _IdleRebuildProof.model_validate(rows[0])
        current = await read(["/c0", "show", "all", "J"])
        controller = parse_controller_show_all(current)
        if (
            controller.serial_number != policy.controller_serial
            or controller.model_name != "LSI MegaRAID SAS 9270CV-8i"
        ):
            raise _UnprovenSafetyError("controller identity changed during safety reads")
        latest = _physical_inventory(current)
        before = {(row.location, row.device_id, row.state) for row in inventory.drives}
        after = {(row.location, row.device_id, row.state) for row in latest.drives}
        if before != after:
            raise _UnprovenSafetyError("physical inventory changed during safety reads")
        foreign = await read(["/c0/fall", "show", "all", "J"])
        if _foreign_evidence(foreign, expected_digest) != foreign_before:
            raise _UnprovenSafetyError("foreign configuration changed during safety reads")
    except _UnprovenSafetyError as exc:
        raise StorcliCommandFailed(
            f"Foreign action refused: {exc}. Refresh current controller and per-drive rebuild "
            "reads; wait for rebuilds to finish, then reload Foreign Configuration "
            "and confirm again."
        ) from exc
    except (ValidationError, ValueError, StorcliError) as exc:
        raise StorcliCommandFailed(
            "Foreign action refused: live controller, inventory, rebuild safety or confirmation "
            "cannot be established. Refresh current controller and per-drive rebuild reads; "
            "wait for any rebuild to finish, then reload Foreign Configuration and confirm again."
        ) from exc
