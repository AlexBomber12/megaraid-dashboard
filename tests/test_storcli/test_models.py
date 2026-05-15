from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationInfo

from megaraid_dashboard.storcli.models import (
    CacheVault,
    ControllerInfo,
    DriveShow,
    ForeignConfig,
    PhysicalDrive,
    VirtualDrive,
    _parse_datetime,
    _parse_optional_datetime,
    _parse_optional_int,
    _parse_percent,
    _parse_temperature,
    _yes_no_to_bool,
    size_string_to_bytes,
)

# ---------------------------------------------------------------------------
# size_string_to_bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "   "])
def test_size_string_to_bytes_empty(value: str) -> None:
    with pytest.raises(ValueError, match="size string is empty"):
        size_string_to_bytes(value)


def test_size_string_to_bytes_unsupported_unit() -> None:
    with pytest.raises(ValueError, match="unsupported size unit: ZB"):
        size_string_to_bytes("100 ZB")


def test_size_string_to_bytes_invalid_number() -> None:
    with pytest.raises(ValueError, match="invalid size number: abc"):
        size_string_to_bytes("abc MB")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1024", 1024),
        ("1 KB", 1000),
        ("2 MB", 2_000_000),
        ("3 GB", 3_000_000_000),
        ("1 TB", 10**12),
        ("1 PB", 10**15),
        ("1,024 bytes", 1024),
    ],
)
def test_size_string_to_bytes_valid(value: str, expected: int) -> None:
    assert size_string_to_bytes(value) == expected


# ---------------------------------------------------------------------------
# _parse_datetime / _parse_optional_datetime
# ---------------------------------------------------------------------------


def test_parse_datetime_passthrough_for_datetime() -> None:
    now = datetime(2024, 1, 1, 12, 0, 0)
    assert _parse_datetime(now) is now


@pytest.mark.parametrize("value", [42, 3.14, [], {}, object()])
def test_parse_datetime_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected datetime string"):
        _parse_datetime(value)


def test_parse_datetime_invalid_format() -> None:
    with pytest.raises(ValueError, match="unsupported datetime format"):
        _parse_datetime("not-a-date")


@pytest.mark.parametrize(
    "value",
    [
        "01/02/2024, 03:04:05",
        "2024/01/02 03:04:05",
        "01/02/2024, 03:04:05 (UTC)",
    ],
)
def test_parse_datetime_accepts_known_formats(value: str) -> None:
    assert isinstance(_parse_datetime(value), datetime)


@pytest.mark.parametrize("value", [None, "", "N/A", "-"])
def test_parse_optional_datetime_none_like(value: Any) -> None:
    assert _parse_optional_datetime(value) is None


def test_parse_optional_datetime_delegates_to_parse_datetime() -> None:
    parsed = _parse_optional_datetime("2024/01/02 03:04:05")
    assert isinstance(parsed, datetime)


# ---------------------------------------------------------------------------
# _parse_temperature
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "N/A", "-"])
def test_parse_temperature_none_like(value: Any) -> None:
    assert _parse_temperature(value) is None


def test_parse_temperature_int() -> None:
    assert _parse_temperature(42) == 42


def test_parse_temperature_float() -> None:
    assert _parse_temperature(42.7) == 42


@pytest.mark.parametrize("value", [{}, [], object(), b"42"])
def test_parse_temperature_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected temperature string"):
        _parse_temperature(value)


def test_parse_temperature_string() -> None:
    assert _parse_temperature("42 C") == 42


# ---------------------------------------------------------------------------
# _parse_optional_int
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "N/A", "-"])
def test_parse_optional_int_none_like(value: Any) -> None:
    assert _parse_optional_int(value) is None


def test_parse_optional_int_int() -> None:
    assert _parse_optional_int(7) == 7


def test_parse_optional_int_float() -> None:
    assert _parse_optional_int(7.9) == 7


@pytest.mark.parametrize("value", [{}, [], object(), b"5"])
def test_parse_optional_int_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected integer string"):
        _parse_optional_int(value)


def test_parse_optional_int_invalid_string_returns_none() -> None:
    assert _parse_optional_int("abc") is None


def test_parse_optional_int_valid_string() -> None:
    assert _parse_optional_int(" 12 ") == 12


# ---------------------------------------------------------------------------
# _parse_percent
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "N/A", "-"])
def test_parse_percent_none_like(value: Any) -> None:
    assert _parse_percent(value) is None


def test_parse_percent_int() -> None:
    assert _parse_percent(80) == 80


def test_parse_percent_float() -> None:
    assert _parse_percent(80.5) == 80


@pytest.mark.parametrize("value", [{}, [], object(), b"80"])
def test_parse_percent_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected percent string"):
        _parse_percent(value)


def test_parse_percent_string() -> None:
    assert _parse_percent("95 %") == 95


# ---------------------------------------------------------------------------
# _yes_no_to_bool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("value", "expected"), [(True, True), (False, False)])
def test_yes_no_to_bool_bool(value: bool, expected: bool) -> None:
    assert _yes_no_to_bool(value) is expected


@pytest.mark.parametrize(("value", "expected"), [(0, False), (1, True), (5, True)])
def test_yes_no_to_bool_int(value: int, expected: bool) -> None:
    assert _yes_no_to_bool(value) is expected


@pytest.mark.parametrize("value", [{}, [], object(), b"yes"])
def test_yes_no_to_bool_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected bool-like string"):
        _yes_no_to_bool(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Yes", True),
        ("yes", True),
        ("Y", True),
        ("true", True),
        ("On", True),
        ("1", True),
        ("present", True),
        ("no", False),
        ("N", False),
        ("0", False),
        ("absent", False),
    ],
)
def test_yes_no_to_bool_string(value: str, expected: bool) -> None:
    assert _yes_no_to_bool(value) is expected


# ---------------------------------------------------------------------------
# ControllerInfo.parse_cv_present
# ---------------------------------------------------------------------------


def test_controller_info_parse_cv_present_list_truthy() -> None:
    assert ControllerInfo.parse_cv_present([{"x": 1}]) is True


def test_controller_info_parse_cv_present_list_empty() -> None:
    assert ControllerInfo.parse_cv_present([]) is False


def test_controller_info_parse_cv_present_string() -> None:
    assert ControllerInfo.parse_cv_present("Yes") is True


# ---------------------------------------------------------------------------
# VirtualDrive validators
# ---------------------------------------------------------------------------


def test_virtual_drive_parse_vd_id_int() -> None:
    assert VirtualDrive.parse_vd_id(5) == 5


@pytest.mark.parametrize("value", [{}, [], object(), 3.14])
def test_virtual_drive_parse_vd_id_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected DG/VD string"):
        VirtualDrive.parse_vd_id(value)


def test_virtual_drive_parse_vd_id_with_slash() -> None:
    assert VirtualDrive.parse_vd_id("5/2") == 2


def test_virtual_drive_parse_vd_id_without_slash() -> None:
    assert VirtualDrive.parse_vd_id("7") == 7


def test_virtual_drive_parse_size_int() -> None:
    assert VirtualDrive.parse_size(1024) == 1024


@pytest.mark.parametrize("value", [{}, [], object(), 3.14])
def test_virtual_drive_parse_size_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected size string"):
        VirtualDrive.parse_size(value)


def test_virtual_drive_parse_size_string() -> None:
    assert VirtualDrive.parse_size("2 GB") == 2_000_000_000


# ---------------------------------------------------------------------------
# PhysicalDrive validators
# ---------------------------------------------------------------------------


class _FakeFieldInfo:
    def __init__(self, name: str) -> None:
        self.field_name = name


def _info(name: str) -> ValidationInfo:
    # ValidationInfo isn't directly constructible; PhysicalDrive.parse_eid_slot
    # only reads `info.field_name`, so a simple stand-in is sufficient.
    return _FakeFieldInfo(name)  # type: ignore[return-value]


def test_physical_drive_parse_eid_slot_int() -> None:
    assert PhysicalDrive.parse_eid_slot(3, _info("enclosure_id")) == 3


@pytest.mark.parametrize("value", [{}, [], object(), 3.14])
def test_physical_drive_parse_eid_slot_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected EID:Slt string"):
        PhysicalDrive.parse_eid_slot(value, _info("enclosure_id"))


def test_physical_drive_parse_eid_slot_invalid() -> None:
    with pytest.raises(ValueError, match="invalid EID:Slt value"):
        PhysicalDrive.parse_eid_slot("1-2", _info("enclosure_id"))


def test_physical_drive_parse_eid_slot_enclosure() -> None:
    assert PhysicalDrive.parse_eid_slot("1:2", _info("enclosure_id")) == 1


def test_physical_drive_parse_eid_slot_slot() -> None:
    assert PhysicalDrive.parse_eid_slot("1:2", _info("slot_id")) == 2


@pytest.mark.parametrize("value", [{}, [], object(), 5, 3.14])
def test_physical_drive_strip_serial_number_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected serial string"):
        PhysicalDrive.strip_serial_number(value)


def test_physical_drive_strip_serial_number_strips() -> None:
    assert PhysicalDrive.strip_serial_number("  SN0001  ") == "SN0001"


def test_physical_drive_parse_size_int() -> None:
    assert PhysicalDrive.parse_size(1024) == 1024


@pytest.mark.parametrize("value", [{}, [], object(), 3.14])
def test_physical_drive_parse_size_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected size string"):
        PhysicalDrive.parse_size(value)


def test_physical_drive_parse_size_string() -> None:
    assert PhysicalDrive.parse_size("1 GB") == 1_000_000_000


def test_physical_drive_parse_disk_group_id_none() -> None:
    assert PhysicalDrive.parse_disk_group_id(None) is None


@pytest.mark.parametrize("value", [True, False])
def test_physical_drive_parse_disk_group_id_bool(value: bool) -> None:
    with pytest.raises(TypeError, match="expected DG int, got bool"):
        PhysicalDrive.parse_disk_group_id(value)


def test_physical_drive_parse_disk_group_id_int() -> None:
    assert PhysicalDrive.parse_disk_group_id(4) == 4


@pytest.mark.parametrize("value", ["", "-", "N/A"])
def test_physical_drive_parse_disk_group_id_empty_string(value: str) -> None:
    assert PhysicalDrive.parse_disk_group_id(value) is None


def test_physical_drive_parse_disk_group_id_invalid_string() -> None:
    assert PhysicalDrive.parse_disk_group_id("abc") is None


def test_physical_drive_parse_disk_group_id_valid_string() -> None:
    assert PhysicalDrive.parse_disk_group_id("5") == 5


@pytest.mark.parametrize("value", [3.14, [], {}, object()])
def test_physical_drive_parse_disk_group_id_other_returns_none(value: Any) -> None:
    assert PhysicalDrive.parse_disk_group_id(value) is None


@pytest.mark.parametrize("value", [{}, [], object(), 5, 3.14])
def test_physical_drive_normalize_sas_address_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected SAS address string"):
        PhysicalDrive.normalize_sas_address(value)


def test_physical_drive_normalize_sas_address_strips_prefix() -> None:
    assert PhysicalDrive.normalize_sas_address("0xABCD") == "ABCD"


def test_physical_drive_normalize_sas_address_without_prefix() -> None:
    assert PhysicalDrive.normalize_sas_address("ABCD") == "ABCD"


# ---------------------------------------------------------------------------
# DriveShow validators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [{}, [], object(), 5, 3.14])
def test_drive_show_validate_state_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected state string"):
        DriveShow.validate_state(value)


def test_drive_show_validate_state_empty() -> None:
    with pytest.raises(ValueError, match="state must not be empty"):
        DriveShow.validate_state("   ")


def test_drive_show_validate_state_returns_value() -> None:
    assert DriveShow.validate_state("Onln") == "Onln"


@pytest.mark.parametrize("value", [{}, [], object(), 5, 3.14])
def test_drive_show_validate_serial_number_rejects_non_string(value: Any) -> None:
    with pytest.raises(TypeError, match="expected serial number string"):
        DriveShow.validate_serial_number(value)


def test_drive_show_validate_serial_number_empty() -> None:
    with pytest.raises(ValueError, match="serial_number must not be empty"):
        DriveShow.validate_serial_number("   ")


def test_drive_show_validate_serial_number_strips() -> None:
    assert DriveShow.validate_serial_number("  SN0001  ") == "SN0001"


# ---------------------------------------------------------------------------
# ForeignConfig.parse_int_count
# ---------------------------------------------------------------------------


def test_foreign_config_parse_int_count_none() -> None:
    assert ForeignConfig.parse_int_count(None) == 0


@pytest.mark.parametrize("value", [True, False])
def test_foreign_config_parse_int_count_bool(value: bool) -> None:
    with pytest.raises(TypeError, match="expected int count, got bool"):
        ForeignConfig.parse_int_count(value)


def test_foreign_config_parse_int_count_int() -> None:
    assert ForeignConfig.parse_int_count(7) == 7


def test_foreign_config_parse_int_count_empty_string() -> None:
    assert ForeignConfig.parse_int_count("   ") == 0


def test_foreign_config_parse_int_count_valid_string() -> None:
    assert ForeignConfig.parse_int_count("5") == 5


@pytest.mark.parametrize("value", [3.14, [], {}, object()])
def test_foreign_config_parse_int_count_other_raises(value: Any) -> None:
    with pytest.raises(TypeError, match="expected int count"):
        ForeignConfig.parse_int_count(value)


# ---------------------------------------------------------------------------
# Thin validator wrappers — invoke each classmethod directly so coverage hits
# every `return _parse_*(value)` line.
# ---------------------------------------------------------------------------


def test_controller_info_parse_system_time_wrapper() -> None:
    assert isinstance(
        ControllerInfo.parse_system_time("2024/01/02 03:04:05"),
        datetime,
    )


def test_controller_info_parse_bbu_present_wrapper() -> None:
    assert ControllerInfo.parse_bbu_present("Yes") is True


def test_controller_info_parse_roc_temperature_wrapper() -> None:
    assert ControllerInfo.parse_roc_temperature_celsius("N/A") is None


def test_physical_drive_parse_temperature_wrapper() -> None:
    assert PhysicalDrive.parse_temperature("42 C") == 42


def test_physical_drive_parse_smart_alert_wrapper() -> None:
    assert PhysicalDrive.parse_smart_alert("Yes") is True


def test_cache_vault_parse_temperature_wrapper() -> None:
    assert CacheVault.parse_temperature("30 C") == 30


def test_cache_vault_parse_capacitance_percent_wrapper() -> None:
    assert CacheVault.parse_capacitance_percent("95%") == 95


def test_cache_vault_parse_replacement_required_wrapper() -> None:
    assert CacheVault.parse_replacement_required("No") is False


def test_cache_vault_parse_next_learn_cycle_wrapper() -> None:
    assert CacheVault.parse_next_learn_cycle("N/A") is None
