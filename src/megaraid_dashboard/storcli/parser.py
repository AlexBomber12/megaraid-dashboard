from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from megaraid_dashboard.storcli.exceptions import StorcliCommandFailed, StorcliParseError
from megaraid_dashboard.storcli.models import (
    BbuInfo,
    CacheVault,
    ControllerInfo,
    PhysicalDrive,
    VirtualDrive,
)


def parse_controller_show_all(payload: dict[str, Any]) -> ControllerInfo:
    controller = _ensure_success(payload)

    try:
        response = _response_data(controller)
        basics = _mapping(response["Basics"])
        version = _mapping(response["Version"])
        hwcfg = _mapping(response["HwCfg"])
        data = {
            **basics,
            **version,
            "Alarm": hwcfg["Alarm"],
            "Cachevault_Info": response.get("Cachevault_Info", []),
            "BBU": hwcfg["BBU"],
        }
        return ControllerInfo.model_validate(data)
    except (KeyError, TypeError, ValidationError) as exc:
        raise StorcliParseError(
            "controller show all payload does not match expected schema"
        ) from exc


def parse_virtual_drives(payload: dict[str, Any]) -> list[VirtualDrive]:
    controller = _ensure_success(payload)

    try:
        response = _response_data(controller)
        items = response.get("VD LIST")
        if not isinstance(items, list):
            items = [
                item
                for value in response.values()
                if isinstance(value, list)
                for item in value
                if isinstance(item, dict) and {"DG/VD", "TYPE", "Size"} <= item.keys()
            ]
        virtual_drives = [VirtualDrive.model_validate(item) for item in items]
        return sorted(virtual_drives, key=lambda virtual_drive: virtual_drive.vd_id)
    except (TypeError, ValidationError) as exc:
        raise StorcliParseError("virtual drive payload does not match expected schema") from exc


def parse_physical_drives(payload: dict[str, Any]) -> list[PhysicalDrive]:
    controller = _ensure_success(payload)

    try:
        response = _response_data(controller)
        physical_drives = [
            _parse_physical_drive(response, drive_key, drive_rows[0])
            for drive_key, drive_rows in _drive_rows(response)
        ]
        return sorted(
            physical_drives,
            key=lambda drive: (drive.enclosure_id, drive.slot_id),
        )
    except (KeyError, TypeError, IndexError, ValidationError) as exc:
        raise StorcliParseError("physical drive payload does not match expected schema") from exc


def parse_cachevault(payload: dict[str, Any]) -> CacheVault | None:
    controller = _first_controller(payload)
    if not _optional_command_succeeded(controller):
        return None

    try:
        response = _response_data(controller)
        data = _property_lists_to_mapping(response)
        if not data and isinstance(response.get("Cachevault_Info"), list):
            data = dict(_mapping(response["Cachevault_Info"][0]))
            if "Model" in data:
                data["Type"] = data["Model"]
            if "Temp" in data:
                data["Temperature"] = data["Temp"]
            data.setdefault("Replacement required", "No")
        return CacheVault.model_validate(data)
    except (KeyError, TypeError, IndexError, ValidationError) as exc:
        raise StorcliParseError("cachevault payload does not match expected schema") from exc


def parse_bbu(payload: dict[str, Any]) -> BbuInfo | None:
    controller = _first_controller(payload)
    if not _optional_command_succeeded(controller):
        return None

    try:
        response = _response_data(controller)
        return BbuInfo.model_validate({"Response Data": response})
    except (TypeError, ValidationError) as exc:
        raise StorcliParseError("bbu payload does not match expected schema") from exc


def _parse_physical_drive(
    response: Mapping[str, Any],
    drive_key: str,
    summary: Mapping[str, Any],
) -> PhysicalDrive:
    detail = _mapping(response[f"{drive_key} - Detailed Information"])
    state = _mapping(detail[f"{drive_key} State"])
    attributes = _mapping(detail[f"{drive_key} Device attributes"])
    policies = _mapping(detail[f"{drive_key} Policies/Settings"])
    port_information = policies["Port Information"]
    if not isinstance(port_information, list) or not port_information:
        msg = "missing Port Information"
        raise TypeError(msg)

    data = {
        **summary,
        **state,
        "Model": attributes.get("Model Number", summary.get("Model")),
        "SN": attributes["SN"],
        "Firmware Revision": attributes["Firmware Revision"],
        "SAS address": _mapping(port_information[0])["SAS address"],
    }
    return PhysicalDrive.model_validate(data)


def _drive_rows(response: Mapping[str, Any]) -> list[tuple[str, list[Any]]]:
    return [
        (key, value)
        for key, value in response.items()
        if key.startswith("Drive ")
        and not key.endswith(" - Detailed Information")
        and isinstance(value, list)
        and value
    ]


def _property_lists_to_mapping(response: Mapping[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for value in response.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, dict) and set(item) >= {"Property", "Value"}:
                data[str(item["Property"])] = item["Value"]
    return data


def _ensure_success(payload: dict[str, Any]) -> Mapping[str, Any]:
    controller = _first_controller(payload)
    if _command_failed(controller):
        err_msg = _command_err_msg(controller)
        raise StorcliCommandFailed(f"storcli command failed: {err_msg}", err_msg=err_msg)
    return controller


def _optional_command_succeeded(controller: Mapping[str, Any]) -> bool:
    if not _command_failed(controller):
        return True

    err_msg = _command_err_msg(controller)
    if _is_unsupported_hardware_error(err_msg):
        return False

    raise StorcliCommandFailed(f"storcli command failed: {err_msg}", err_msg=err_msg)


def _is_unsupported_hardware_error(err_msg: str) -> bool:
    normalized = err_msg.casefold()
    return any(
        marker in normalized
        for marker in (
            "use /cx/cv",
            "use /cx/bbu",
        )
    )


def _first_controller(payload: dict[str, Any]) -> Mapping[str, Any]:
    try:
        controllers = payload["Controllers"]
        if not isinstance(controllers, list) or not controllers:
            msg = "Controllers must be a non-empty list"
            raise TypeError(msg)
        return _mapping(controllers[0])
    except (KeyError, TypeError) as exc:
        raise StorcliParseError("payload does not contain a controller") from exc


def _response_data(controller: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(controller.get("Response Data", {}))


def _command_failed(controller: Mapping[str, Any]) -> bool:
    status = _mapping(controller.get("Command Status", {}))
    return str(status.get("Status", "")).lower() == "failure"


def _command_err_msg(controller: Mapping[str, Any]) -> str:
    status = _mapping(controller.get("Command Status", {}))
    detailed_status = status.get("Detailed Status", [])
    if isinstance(detailed_status, list):
        for item in detailed_status:
            if isinstance(item, dict) and item.get("ErrMsg"):
                return str(item["ErrMsg"])
    return str(status.get("Description") or status.get("Status") or "unknown error")


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        msg = f"expected mapping, got {type(value).__name__}"
        raise TypeError(msg)
    return value
