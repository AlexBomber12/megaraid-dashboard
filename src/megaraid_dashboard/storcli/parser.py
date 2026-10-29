from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from megaraid_dashboard.storcli.exceptions import StorcliCommandFailed, StorcliParseError
from megaraid_dashboard.storcli.models import (
    BbuInfo,
    CacheVault,
    ControllerInfo,
    DriveShow,
    ForeignConfig,
    ForeignConfigDiskGroup,
    PhysicalDrive,
    VirtualDrive,
    size_string_to_bytes,
)
from megaraid_dashboard.storcli.states import normalized_state


def parse_controller_show_all(payload: dict[str, Any]) -> ControllerInfo:
    controller = _ensure_inventory_success(payload)

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
            "ROC temperature(Degree Celsius)": hwcfg.get("ROC temperature(Degree Celsius)"),
        }
        return ControllerInfo.model_validate(data)
    except (KeyError, TypeError, ValidationError) as exc:
        raise StorcliParseError(
            "controller show all payload does not match expected schema"
        ) from exc


def parse_virtual_drives(payload: dict[str, Any]) -> list[VirtualDrive]:
    controller = _ensure_inventory_success(payload)

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


def parse_drive_show(payload: dict[str, Any], *, requested_drive: str | None = None) -> DriveShow:
    """Extract live state and serial number from a `/c0/eX/sY show all J` payload."""
    controller = _ensure_success(payload)
    try:
        response = _response_data(controller)
        if requested_drive is not None:
            status = _mapping(controller["Command Status"])
            rows = _drive_rows(response)
            if (
                len(payload["Controllers"]) != 1
                or type(status.get("Controller")) is not int
                or status["Controller"] != 0
                or len(rows) != 1
                or rows[0][0] != f"Drive {requested_drive}"
                or _mapping(rows[0][1][0]).get("EID:Slt")
                != requested_drive.removeprefix("/c0/e").replace("/s", ":")
            ):
                raise StorcliParseError("live drive response does not identify the requested slot")
        return _extract_drive_show(response)
    except (KeyError, TypeError, ValidationError) as exc:
        raise StorcliParseError("drive show payload does not match expected schema") from exc


def _extract_drive_show(response: Mapping[str, Any]) -> DriveShow:
    for key, value in response.items():
        if not key.startswith("Drive ") or key.endswith(" - Detailed Information"):
            continue
        if not isinstance(value, list) or not value:
            continue
        first = value[0]
        if not isinstance(first, Mapping):
            continue
        state = first.get("State")
        if not isinstance(state, str):
            continue
        detail = _mapping(response[f"{key} - Detailed Information"])
        attributes = _mapping(detail[f"{key} Device attributes"])
        return DriveShow.model_validate({"state": state, "serial_number": attributes["SN"]})
    raise StorcliParseError("drive show payload does not contain a Drive State and SN")


def parse_physical_drives(payload: dict[str, Any]) -> list[PhysicalDrive]:
    controller = _ensure_inventory_success(payload)

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
    controller = _ensure_inventory_success(payload)

    try:
        response = _response_data(controller)
        if response.get("Cachevault_Info") == []:
            return None
        data = _property_lists_to_mapping(response)
        if not data and isinstance(response.get("Cachevault_Info"), list):
            data = dict(_mapping(response["Cachevault_Info"][0]))
            if "Model" in data:
                data["Type"] = data["Model"]
            if "Temp" in data:
                data["Temperature"] = data["Temp"]
        return CacheVault.model_validate(data)
    except (KeyError, TypeError, IndexError, ValidationError) as exc:
        raise StorcliParseError("cachevault payload does not match expected schema") from exc


def parse_foreign_config(payload: dict[str, Any]) -> ForeignConfig:
    """Parse a ``/c0/fall show all J`` payload into a ForeignConfig summary.

    A failure status with the storcli "no foreign configuration" marker is the
    NORMAL absent case — the controller emits Status=Failure with a marker
    error message rather than an empty success payload. That case maps to
    ``ForeignConfig(present=False)``.

    A success status with no extractable DG/drive counts is also treated as
    absent: defensively, because firmware revisions differ in how they report
    "no foreign config", we never raise from this parser unless the payload is
    fundamentally not a controller response. Operationally, the presence flag
    is what gates events and routes; structural changes that drop counts are
    safer to render as "absent" than to raise and stall the collector.
    """
    controller = _first_controller(payload)
    if _command_failed(controller):
        err_msg = _command_err_msg(controller)
        if _is_no_foreign_config_error(err_msg):
            return ForeignConfig(present=False, digest="")
        raise StorcliCommandFailed(f"storcli command failed: {err_msg}", err_msg=err_msg)

    ensure_command_succeeded(payload)

    try:
        response = _response_data(controller)
    except StorcliParseError:
        return ForeignConfig(present=False, digest="")

    drives = _foreign_drives(response)
    disk_groups = _foreign_disk_groups(response, drives)
    drive_count = len(drives)
    dg_count = len(disk_groups)
    total_size_bytes = _foreign_total_size_bytes(response, disk_groups)

    if dg_count == 0 and drive_count == 0:
        # Some firmware revisions report foreign config via summary count fields
        # (e.g. "Total foreign DG Count" / "Total foreign drive Count") without
        # emitting the detailed DG/PD list blocks. Fall back to those counts
        # before declaring the config absent so the import/clear routes do not
        # incorrectly reject a real foreign configuration.
        summary_dg, summary_drive = _foreign_summary_counts(response)
        if summary_dg > 0 or summary_drive > 0:
            digest = _foreign_config_digest(
                dg_count=summary_dg,
                drive_count=summary_drive,
                total_size_bytes=total_size_bytes,
                disk_groups=disk_groups,
            )
            return ForeignConfig(
                present=True,
                dg_count=summary_dg,
                drive_count=summary_drive,
                total_size_bytes=total_size_bytes,
                disk_groups=disk_groups,
                digest=_foreign_metadata_digest(response, digest),
            )
        return ForeignConfig(present=False, digest="")

    digest = _foreign_config_digest(
        dg_count=dg_count,
        drive_count=drive_count,
        total_size_bytes=total_size_bytes,
        disk_groups=disk_groups,
    )
    return ForeignConfig(
        present=True,
        dg_count=dg_count,
        drive_count=drive_count,
        total_size_bytes=total_size_bytes,
        disk_groups=disk_groups,
        digest=_foreign_metadata_digest(response, digest),
    )


def _foreign_metadata_digest(response: Mapping[str, Any], summary: str) -> str:
    """Bind confirmation to the reviewed metadata, including equal-sized drive swaps."""
    try:
        encoded = json.dumps(
            dict(response), sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    except (TypeError, ValueError) as exc:
        raise StorcliParseError("foreign metadata cannot be fingerprinted") from exc
    return f"{summary}-{hashlib.sha256(encoded).hexdigest()}"


_NO_FOREIGN_CONFIG_MARKERS = (
    "no foreign configuration",
    "no foreign config",
    "foreign configuration not found",
)


def _is_no_foreign_config_error(err_msg: str) -> bool:
    normalized = err_msg.casefold()
    return any(marker in normalized for marker in _NO_FOREIGN_CONFIG_MARKERS)


def _foreign_drives(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    drives: list[Mapping[str, Any]] = []
    for key, value in response.items():
        normalized = key.casefold()
        if "pd list" not in normalized and "drive" not in normalized:
            continue
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, Mapping) and ("EID:Slt" in item or "DID" in item):
                drives.append(item)
    return drives


def _foreign_disk_groups(
    response: Mapping[str, Any],
    drives: list[Mapping[str, Any]],
) -> list[ForeignConfigDiskGroup]:
    by_id: dict[int, dict[str, Any]] = {}
    for key, value in response.items():
        normalized = key.casefold()
        if "dg" not in normalized:
            continue
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, Mapping):
                continue
            dg_value = item.get("DG") if "DG" in item else item.get("DG/VD")
            dg_id = _coerce_dg_id(dg_value)
            if dg_id is None:
                continue
            entry = by_id.setdefault(dg_id, {"dg_id": dg_id, "drive_count": 0, "size_bytes": None})
            size_text = item.get("Size") if "Size" in item else None
            if isinstance(size_text, str) and entry["size_bytes"] is None:
                try:
                    entry["size_bytes"] = size_string_to_bytes(size_text)
                except ValueError:
                    entry["size_bytes"] = None

    for drive in drives:
        dg_value = drive.get("DG")
        dg_id = _coerce_dg_id(dg_value)
        if dg_id is None:
            continue
        entry = by_id.setdefault(dg_id, {"dg_id": dg_id, "drive_count": 0, "size_bytes": None})
        entry["drive_count"] = int(entry["drive_count"]) + 1

    sorted_entries = sorted(by_id.values(), key=lambda entry: entry["dg_id"])
    return [ForeignConfigDiskGroup.model_validate(entry) for entry in sorted_entries]


def _coerce_dg_id(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"-", "N/A"}:
            return None
        if "/" in text:
            head, _, _ = text.partition("/")
            text = head.strip()
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _foreign_summary_counts(response: Mapping[str, Any]) -> tuple[int, int]:
    """Extract ``(dg_count, drive_count)`` from foreign-config summary keys.

    Firmware revisions that omit the detailed DG/PD list blocks still tend to
    report counts via top-level summary fields like ``Total foreign DG Count``
    and ``Total foreign drive Count``. Match keys case-insensitively on the
    ``total ... foreign ... (dg|drive) ... count`` shape so cosmetic spelling
    differences across revisions still resolve.
    """
    dg_count = 0
    drive_count = 0
    for key, value in response.items():
        if not isinstance(key, str):
            continue
        normalized = key.casefold()
        if "total" not in normalized or "foreign" not in normalized:
            continue
        if "count" not in normalized:
            continue
        coerced = _coerce_count(value)
        if coerced is None or coerced <= 0:
            continue
        if "dg" in normalized and dg_count == 0:
            dg_count = coerced
        elif "drive" in normalized and drive_count == 0:
            drive_count = coerced
    return dg_count, drive_count


def _coerce_count(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(float(stripped))
        except ValueError:
            return None
    return None


def _foreign_total_size_bytes(
    response: Mapping[str, Any],
    disk_groups: list[ForeignConfigDiskGroup],
) -> int | None:
    for key, value in response.items():
        if not isinstance(value, str):
            continue
        normalized_key = key.casefold()
        if "total" in normalized_key and "size" in normalized_key:
            try:
                return size_string_to_bytes(value)
            except ValueError:
                continue
    sizes = [dg.size_bytes for dg in disk_groups if dg.size_bytes is not None]
    if sizes:
        return sum(sizes)
    return None


def _foreign_config_digest(
    *,
    dg_count: int,
    drive_count: int,
    total_size_bytes: int | None,
    disk_groups: list[ForeignConfigDiskGroup],
) -> str:
    """Stable summary the operator types back to confirm import.

    Format: ``FC-DG{n}-PD{m}-{size}`` where size is total_size_bytes rounded to
    the nearest GB or ``UNKNOWN`` when the firmware did not report it. The
    digest is intentionally short, human-readable, and changes whenever the
    foreign-config shape changes — making blind copy/paste recognizable while
    preventing typo replay against a different foreign config.
    """
    if total_size_bytes is None:
        size_token = "UNKNOWN"
    else:
        size_gb = max(0, round(total_size_bytes / 10**9))
        size_token = f"{size_gb}GB"
    base = f"FC-DG{dg_count}-PD{drive_count}-{size_token}"
    dg_tokens = [f"dg{dg.dg_id}:{dg.drive_count}" for dg in disk_groups]
    if not dg_tokens:
        return base
    return f"{base}-[{','.join(dg_tokens)}]"


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
    rows: list[tuple[str, list[Any]]] = []
    for key, value in response.items():
        if not key.startswith("Drive ") or key.endswith(" - Detailed Information"):
            continue
        if not isinstance(value, list) or len(value) != 1:
            raise TypeError("Drive summary must contain exactly one row")
        rows.append((key, value))
    return rows


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


def _ensure_inventory_success(payload: dict[str, Any]) -> Mapping[str, Any]:
    try:
        controller = _ensure_success(payload)
        status = _mapping(controller.get("Command Status", {}))
        if (
            str(status.get("Status", "")).strip().casefold() != "success"
            or len(payload["Controllers"]) != 1
            or status.get("Controller", 0) != 0
        ):
            raise StorcliParseError("storcli command success is unproven")
    except TypeError as exc:
        raise StorcliParseError("storcli command status is malformed") from exc
    return controller


def ensure_command_succeeded(payload: dict[str, Any]) -> None:
    """Require explicit controller acceptance for a mutating command.

    Process exit zero only confirms transport success. Read parsers retain their
    existing compatibility behavior; mutations fail closed on an unknown status.
    """
    controller = _first_controller(payload)
    status = controller.get("Command Status")
    if not isinstance(status, Mapping):
        raise StorcliParseError("storcli did not report a valid Command Status")
    value = status.get("Status")
    if not isinstance(value, str) or value.strip().casefold() not in {"success", "failure"}:
        raise StorcliParseError("storcli did not report an explicit Success or Failure status")
    if value.strip().casefold() == "failure":
        # Only expose bounded, printable controller detail, never a raw payload.
        detail = _command_err_msg(controller)
        detail = " ".join("".join(c for c in detail if c.isprintable() or c.isspace()).split())
        detail = detail[:300]
        raise StorcliCommandFailed(f"storcli command failed: {detail}", err_msg=detail)


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
    try:
        return _mapping(controller.get("Response Data", {}))
    except TypeError as exc:
        raise StorcliParseError("Response Data is not a mapping") from exc


def _command_failed(controller: Mapping[str, Any]) -> bool:
    status = _mapping(controller.get("Command Status", {}))
    return str(status.get("Status", "")).strip().casefold() == "failure"


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


def validate_complete_inventory(
    controller_payload: dict[str, Any],
    virtual_payload: dict[str, Any],
    physical_payload: dict[str, Any],
    virtual_drives: list[VirtualDrive],
    physical_drives: list[PhysicalDrive],
) -> None:
    """Accept only mutually consistent complete inventories from successful commands.

    The controller's explicit counts and identity lists corroborate both detailed
    commands. Zero counts prove empty inventories, even if firmware omits empty tables.
    A race between commands is uncertainty: retry rather than invent a hardware loss.
    """
    try:
        for payload in (controller_payload, virtual_payload, physical_payload):
            _ensure_inventory_success(payload)
        response = _response_data(_first_controller(controller_payload))
        vd_count = response["Virtual Drives"]
        pd_count = response["Physical Drives"]
        if (
            type(vd_count) is not int
            or vd_count != len(virtual_drives)
            or type(pd_count) is not int
            or pd_count != len(physical_drives)
        ):
            raise ValueError("inventory counts are missing or inconsistent")
        summary_vds = parse_virtual_drives(controller_payload)
        summary_pds = response.get("PD LIST", [])
        if not isinstance(summary_pds, list):
            raise ValueError("PD LIST is not a list")
        vd_keys = [(d.vd_id, normalized_state(d.state)) for d in virtual_drives]
        summary_vd_keys = [(d.vd_id, normalized_state(d.state)) for d in summary_vds]
        pd_keys = [
            (d.enclosure_id, d.slot_id, d.device_id, normalized_state(d.state))
            for d in physical_drives
        ]
        summary_pd_keys = []
        for row in summary_pds:
            item = _mapping(row)
            enclosure, slot = str(item["EID:Slt"]).split(":")
            if not isinstance(item["State"], str):
                raise TypeError("PD state is not a string")
            summary_pd_keys.append(
                (int(enclosure), int(slot), int(item["DID"]), normalized_state(item["State"]))
            )
        if (
            len({d.vd_id for d in virtual_drives}) != vd_count
            or len({(d.enclosure_id, d.slot_id) for d in physical_drives}) != pd_count
            or sorted(vd_keys) != sorted(summary_vd_keys)
            or sorted(pd_keys) != sorted(summary_pd_keys)
            or any(not d.serial_number for d in physical_drives)
        ):
            raise ValueError("inventory identities or states are inconsistent")
        cv_info = response["Cachevault_Info"]
        if not isinstance(cv_info, list):
            raise ValueError("Cachevault_Info must explicitly describe presence or absence")
    except (KeyError, TypeError, ValueError) as exc:
        raise StorcliParseError("inventory completeness is unproven") from exc
