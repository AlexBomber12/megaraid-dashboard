from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

JSON = dict[str, Any] | list[Any] | str | int | float | bool | None

IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SAS_KEYS = {"sas address", "wwn", "scsi naa id"}
HOSTNAME_KEYS = {"host name", "hostname", "host"}


class RedactionPlan:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.drive_serials = _drive_serial_mapping(payloads)
        self.sas_addresses = _sas_address_mapping(payloads)


def redact_directory(source_dir: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = sorted(source_dir.glob("*.json"))
    payloads = [_load_json(path) for path in paths]
    plan = RedactionPlan(payloads)

    written_paths: list[Path] = []
    for path, payload in zip(paths, payloads, strict=True):
        redacted = _redact(payload, plan, key=None)
        output_path = output_dir / path.name
        output_path.write_text(json.dumps(redacted, indent=2) + "\n", encoding="utf-8")
        written_paths.append(output_path)
    return written_paths


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not 1 <= len(args) <= 2:
        sys.stderr.write(
            "usage: python tests/fixtures/storcli/redact.py <source-dir> [output-dir]\n"
        )
        return 2

    source_dir = Path(args[0])
    output_dir = Path(args[1]) if len(args) == 2 else source_dir.parent / "redacted"
    redact_directory(source_dir, output_dir)
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"{path} does not contain a JSON object"
        raise TypeError(msg)
    return payload


def _redact(value: JSON, plan: RedactionPlan, *, key: str | None) -> JSON:
    if isinstance(value, dict):
        return {
            item_key: _redact_mapping_value(item_key, item_value, plan)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, plan, key=key) for item in value]
    if isinstance(value, str):
        return _redact_string(value, key=key)
    return value


def _redact_mapping_value(key: str, value: JSON, plan: RedactionPlan) -> JSON:
    normalized_key = key.casefold()
    if key == "Serial Number" and isinstance(value, str):
        return "SV00000001"
    if key == "SN" and isinstance(value, str):
        return plan.drive_serials.get(value.strip(), value.strip())
    if normalized_key in SAS_KEYS and isinstance(value, str):
        return plan.sas_addresses.get(_normalize_sas_address(value), value)
    if normalized_key == "inquiry data" and isinstance(value, str):
        return "redacted"
    if normalized_key in HOSTNAME_KEYS and isinstance(value, str):
        return "redacted-host"
    return _redact(value, plan, key=key)


def _redact_string(value: str, *, key: str | None) -> str:
    if key is not None and "version" in key.casefold():
        return value

    def replace(match: re.Match[str]) -> str:
        octets = match.group(0).split(".")
        if all(0 <= int(octet) <= 255 for octet in octets):
            return "0.0.0.0"
        return match.group(0)

    return IPV4_RE.sub(replace, value)


def _drive_serial_mapping(payloads: list[dict[str, Any]]) -> dict[str, str]:
    discovered: set[tuple[int, int, str]] = set()
    for payload in payloads:
        for response in _responses(payload):
            for drive_key, drive_rows in _drive_rows(response):
                if not drive_rows:
                    continue
                summary = drive_rows[0]
                if not isinstance(summary, dict):
                    continue
                eid_slot = summary.get("EID:Slt")
                if not isinstance(eid_slot, str):
                    continue
                enclosure_id, slot_id = _parse_eid_slot(eid_slot)
                details = response.get(f"{drive_key} - Detailed Information")
                if not isinstance(details, dict):
                    continue
                attributes = details.get(f"{drive_key} Device attributes")
                if not isinstance(attributes, dict):
                    continue
                serial = attributes.get("SN")
                if isinstance(serial, str) and serial.strip():
                    discovered.add((enclosure_id, slot_id, serial.strip()))

    redacted_serials: dict[str, str] = {}
    for index, (_enclosure_id, _slot_id, serial) in enumerate(sorted(discovered), start=1):
        redacted_serials.setdefault(serial, f"WD-WM{index:08d}")
    return redacted_serials


def _sas_address_mapping(payloads: list[dict[str, Any]]) -> dict[str, str]:
    addresses: set[str] = set()
    for payload in payloads:
        _collect_sas_addresses(payload, addresses)
    return {
        address: f"{0x5000000000000000 + index:016x}"
        for index, address in enumerate(sorted(addresses))
    }


def _collect_sas_addresses(value: Any, addresses: set[str], *, key: str | None = None) -> None:
    if isinstance(value, dict):
        for item_key, item_value in value.items():
            _collect_sas_addresses(item_value, addresses, key=item_key)
    elif isinstance(value, list):
        for item in value:
            _collect_sas_addresses(item, addresses, key=key)
    elif isinstance(value, str) and key is not None and key.casefold() in SAS_KEYS:
        addresses.add(_normalize_sas_address(value))


def _responses(payload: dict[str, Any]) -> list[dict[str, Any]]:
    controllers = payload.get("Controllers")
    if not isinstance(controllers, list):
        return []
    responses: list[dict[str, Any]] = []
    for controller in controllers:
        if not isinstance(controller, dict):
            continue
        response = controller.get("Response Data")
        if isinstance(response, dict):
            responses.append(response)
    return responses


def _drive_rows(response: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    return [
        (key, value)
        for key, value in response.items()
        if key.startswith("Drive ")
        and not key.endswith(" - Detailed Information")
        and isinstance(value, list)
    ]


def _parse_eid_slot(value: str) -> tuple[int, int]:
    enclosure_id, separator, slot_id = value.partition(":")
    if separator != ":":
        msg = f"invalid EID:Slt value: {value}"
        raise ValueError(msg)
    return int(enclosure_id), int(slot_id)


def _normalize_sas_address(value: str) -> str:
    return value.strip().removeprefix("0x")


if __name__ == "__main__":
    raise SystemExit(main())
