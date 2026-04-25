from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class StorcliModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


def size_string_to_bytes(value: str) -> int:
    parts = value.replace(",", "").strip().split()
    if not parts:
        msg = "size string is empty"
        raise ValueError(msg)

    number_text = parts[0]
    unit = parts[1].upper() if len(parts) > 1 else "B"
    if unit == "BYTES":
        unit = "B"

    multipliers = {
        "B": 1,
        "KB": 10**3,
        "MB": 10**6,
        "GB": 10**9,
        "TB": 10**12,
        "PB": 10**15,
    }
    if unit not in multipliers:
        msg = f"unsupported size unit: {unit}"
        raise ValueError(msg)

    try:
        number = Decimal(number_text)
    except InvalidOperation as exc:
        msg = f"invalid size number: {number_text}"
        raise ValueError(msg) from exc

    return int(number * multipliers[unit])


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        msg = f"expected datetime string, got {type(value).__name__}"
        raise TypeError(msg)

    normalized = value.split("(", maxsplit=1)[0].strip()
    for format_string in ("%m/%d/%Y, %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(" ".join(normalized.split()), format_string)
        except ValueError:
            continue

    msg = f"unsupported datetime format: {value}"
    raise ValueError(msg)


def _parse_optional_datetime(value: Any) -> datetime | None:
    if value in (None, "", "N/A", "-"):
        return None
    return _parse_datetime(value)


def _parse_temperature(value: Any) -> int | None:
    if value in (None, "", "N/A", "-"):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        msg = f"expected temperature string, got {type(value).__name__}"
        raise TypeError(msg)

    celsius_text = value.partition("C")[0].strip()
    return int(float(celsius_text))


def _parse_percent(value: Any) -> int | None:
    if value in (None, "", "N/A", "-"):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        msg = f"expected percent string, got {type(value).__name__}"
        raise TypeError(msg)

    percent_text = value.partition("%")[0].strip()
    return int(float(percent_text))


def _yes_no_to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if not isinstance(value, str):
        msg = f"expected bool-like string, got {type(value).__name__}"
        raise TypeError(msg)
    return value.strip().lower() in {"yes", "y", "true", "on", "1", "present"}


class ControllerInfo(StorcliModel):
    model_name: str = Field(alias="Model")
    serial_number: str = Field(alias="Serial Number")
    firmware_version: str = Field(alias="Firmware Version")
    bios_version: str = Field(alias="Bios Version")
    driver_name: str = Field(alias="Driver Name")
    driver_version: str = Field(alias="Driver Version")
    pci_address: str = Field(alias="PCI Address")
    system_time: datetime = Field(alias="Current System Date/time")
    alarm_state: str = Field(alias="Alarm")
    cv_present: bool = Field(alias="Cachevault_Info")
    bbu_present: bool = Field(alias="BBU")

    @field_validator("system_time", mode="before")
    @classmethod
    def parse_system_time(cls, value: Any) -> datetime:
        return _parse_datetime(value)

    @field_validator("cv_present", mode="before")
    @classmethod
    def parse_cv_present(cls, value: Any) -> bool:
        if isinstance(value, list):
            return bool(value)
        return _yes_no_to_bool(value)

    @field_validator("bbu_present", mode="before")
    @classmethod
    def parse_bbu_present(cls, value: Any) -> bool:
        return _yes_no_to_bool(value)


class VirtualDrive(StorcliModel):
    vd_id: int = Field(alias="DG/VD")
    name: str = Field(alias="Name")
    raid_level: str = Field(alias="TYPE")
    size_bytes: int = Field(alias="Size")
    state: str = Field(alias="State")
    access: str = Field(alias="Access")
    cache: str = Field(alias="Cache")

    @field_validator("vd_id", mode="before")
    @classmethod
    def parse_vd_id(cls, value: Any) -> int:
        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            msg = f"expected DG/VD string, got {type(value).__name__}"
            raise TypeError(msg)
        _, _, vd_id = value.partition("/")
        return int(vd_id or value)

    @field_validator("size_bytes", mode="before")
    @classmethod
    def parse_size(cls, value: Any) -> int:
        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            msg = f"expected size string, got {type(value).__name__}"
            raise TypeError(msg)
        return size_string_to_bytes(value)


class PhysicalDrive(StorcliModel):
    enclosure_id: int = Field(alias="EID:Slt")
    slot_id: int = Field(alias="EID:Slt")
    device_id: int = Field(alias="DID")
    model: str = Field(alias="Model")
    serial_number: str = Field(alias="SN")
    firmware_version: str = Field(alias="Firmware Revision")
    size_bytes: int = Field(alias="Size")
    interface: str = Field(alias="Intf")
    media_type: str = Field(alias="Med")
    state: str = Field(alias="State")
    temperature_celsius: int | None = Field(alias="Drive Temperature")
    media_errors: int = Field(alias="Media Error Count")
    other_errors: int = Field(alias="Other Error Count")
    predictive_failures: int = Field(alias="Predictive Failure Count")
    smart_alert: bool = Field(alias="S.M.A.R.T alert flagged by drive")
    sas_address: str = Field(alias="SAS address")

    @field_validator("enclosure_id", "slot_id", mode="before")
    @classmethod
    def parse_eid_slot(cls, value: Any, info: ValidationInfo) -> int:
        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            msg = f"expected EID:Slt string, got {type(value).__name__}"
            raise TypeError(msg)
        enclosure_id, separator, slot_id = value.partition(":")
        if separator != ":":
            msg = f"invalid EID:Slt value: {value}"
            raise ValueError(msg)
        if info.field_name == "enclosure_id":
            return int(enclosure_id)
        return int(slot_id)

    @field_validator("serial_number", mode="before")
    @classmethod
    def strip_serial_number(cls, value: Any) -> str:
        if not isinstance(value, str):
            msg = f"expected serial string, got {type(value).__name__}"
            raise TypeError(msg)
        return value.strip()

    @field_validator("size_bytes", mode="before")
    @classmethod
    def parse_size(cls, value: Any) -> int:
        if isinstance(value, int):
            return value
        if not isinstance(value, str):
            msg = f"expected size string, got {type(value).__name__}"
            raise TypeError(msg)
        return size_string_to_bytes(value)

    @field_validator("temperature_celsius", mode="before")
    @classmethod
    def parse_temperature(cls, value: Any) -> int | None:
        return _parse_temperature(value)

    @field_validator("smart_alert", mode="before")
    @classmethod
    def parse_smart_alert(cls, value: Any) -> bool:
        return _yes_no_to_bool(value)

    @field_validator("sas_address", mode="before")
    @classmethod
    def normalize_sas_address(cls, value: Any) -> str:
        if not isinstance(value, str):
            msg = f"expected SAS address string, got {type(value).__name__}"
            raise TypeError(msg)
        return value.removeprefix("0x")


class CacheVault(StorcliModel):
    type: str = Field(alias="Type")
    state: str = Field(alias="State")
    temperature_celsius: int | None = Field(alias="Temperature")
    pack_energy: str | None = Field(default=None, alias="Pack Energy")
    capacitance_percent: int | None = Field(default=None, alias="Capacitance")
    replacement_required: bool = Field(alias="Replacement required")
    next_learn_cycle: datetime | None = Field(default=None, alias="Next Learn time")

    @field_validator("temperature_celsius", mode="before")
    @classmethod
    def parse_temperature(cls, value: Any) -> int | None:
        return _parse_temperature(value)

    @field_validator("capacitance_percent", mode="before")
    @classmethod
    def parse_capacitance_percent(cls, value: Any) -> int | None:
        return _parse_percent(value)

    @field_validator("replacement_required", mode="before")
    @classmethod
    def parse_replacement_required(cls, value: Any) -> bool:
        return _yes_no_to_bool(value)

    @field_validator("next_learn_cycle", mode="before")
    @classmethod
    def parse_next_learn_cycle(cls, value: Any) -> datetime | None:
        return _parse_optional_datetime(value)


class BbuInfo(StorcliModel):
    response_data: dict[str, Any] = Field(alias="Response Data")


class StorcliSnapshot(StorcliModel):
    controller: ControllerInfo
    virtual_drives: list[VirtualDrive]
    physical_drives: list[PhysicalDrive]
    cachevault: CacheVault | None
    bbu: Any | None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
