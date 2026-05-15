from __future__ import annotations

from typing import Any

import pytest

from megaraid_dashboard.storcli import (
    StorcliCommandFailed,
    StorcliParseError,
    parse_bbu,
    parse_cachevault,
    parse_foreign_config,
    parse_physical_drives,
    parse_virtual_drives,
)
from megaraid_dashboard.storcli.parser import (
    _coerce_count,
    _coerce_dg_id,
    _command_err_msg,
    _command_failed,
    _first_controller,
    _foreign_config_digest,
    _foreign_summary_counts,
    _foreign_total_size_bytes,
)


def success_payload(response_data: Any) -> dict[str, Any]:
    return {
        "Controllers": [
            {
                "Command Status": {"Status": "Success", "Description": "None"},
                "Response Data": response_data,
            }
        ]
    }


# ---------------------------------------------------------------------------
# parse_virtual_drives branches (lines 51->59, 62)
# ---------------------------------------------------------------------------


def test_parse_virtual_drives_uses_fallback_when_vd_list_missing() -> None:
    """Line 51->59: VD LIST not a list, fall back to scanning values."""
    payload = success_payload(
        {
            "Some Other Block": [
                {
                    "DG/VD": "0/0",
                    "Name": "VD0",
                    "TYPE": "RAID5",
                    "Size": "1.000 TB",
                    "State": "Optl",
                    "Access": "RW",
                    "Cache": "NRWBD",
                }
            ]
        }
    )
    drives = parse_virtual_drives(payload)
    assert len(drives) == 1
    assert drives[0].raid_level == "RAID5"


def test_parse_virtual_drives_raises_on_malformed_vd_entry() -> None:
    """Line 62: TypeError/ValidationError path on malformed VD entry."""
    payload = success_payload({"VD LIST": [{"DG/VD": "x/y", "TYPE": "RAID5"}]})
    with pytest.raises(StorcliParseError, match="virtual drive payload"):
        parse_virtual_drives(payload)


# ---------------------------------------------------------------------------
# _extract_drive_show branches (lines 78, 80, 83)
# ---------------------------------------------------------------------------


def test_parse_drive_show_skips_non_drive_keys_and_detailed_keys() -> None:
    """Line 78: keys not starting with "Drive " or ending in Detailed Information are skipped."""
    from megaraid_dashboard.storcli import parse_drive_show

    payload = success_payload(
        {
            "Other Section": [{"State": "Onln"}],
            "Drive /c0/e2/s0 - Detailed Information": {
                "Drive /c0/e2/s0 Device attributes": {"SN": "X"},
            },
            "Drive /c0/e2/s0": [{"EID:Slt": "2:0", "State": "Onln"}],
            "Drive /c0/e2/s0 - Detailed Information ": {},
        }
    )
    # Skip Other Section (no "Drive " prefix); skip detailed info; pick "Drive /c0/e2/s0".
    payload["Controllers"][0]["Response Data"]["Drive /c0/e2/s0 - Detailed Information"] = {
        "Drive /c0/e2/s0 Device attributes": {"SN": "WD-SN-0001"},
    }
    result = parse_drive_show(payload)
    assert result.state == "Onln"
    assert result.serial_number == "WD-SN-0001"


def test_parse_drive_show_skips_when_value_not_list_or_empty() -> None:
    """Line 80: when value is not a list, or empty list, skip."""
    from megaraid_dashboard.storcli import parse_drive_show

    payload = success_payload(
        {
            "Drive /c0/e2/s0": {"not": "a list"},
            "Drive /c0/e2/s1": [],
            "Drive /c0/e2/s2": [{"State": "Onln"}],
            "Drive /c0/e2/s2 - Detailed Information": {
                "Drive /c0/e2/s2 Device attributes": {"SN": "SN-X"},
            },
        }
    )
    result = parse_drive_show(payload)
    assert result.serial_number == "SN-X"


def test_parse_drive_show_skips_when_first_item_not_mapping() -> None:
    """Line 83: first element is not a Mapping → continue."""
    from megaraid_dashboard.storcli import parse_drive_show

    payload = success_payload(
        {
            "Drive /c0/e2/s0": ["string item, not a mapping"],
            "Drive /c0/e2/s1": [{"State": "Onln"}],
            "Drive /c0/e2/s1 - Detailed Information": {
                "Drive /c0/e2/s1 Device attributes": {"SN": "OK"},
            },
        }
    )
    result = parse_drive_show(payload)
    assert result.serial_number == "OK"


# ---------------------------------------------------------------------------
# parse_physical_drives malformed payload (line 107)
# ---------------------------------------------------------------------------


def test_parse_physical_drives_raises_on_malformed_payload() -> None:
    """Line 107: KeyError/TypeError/IndexError/ValidationError path."""
    payload = success_payload(
        {
            "Drive /c0/e2/s0": [{"EID:Slt": "2:0", "DID": 14, "State": "Onln"}],
            # Missing "Drive /c0/e2/s0 - Detailed Information"
        }
    )
    with pytest.raises(StorcliParseError, match="physical drive payload"):
        parse_physical_drives(payload)


# ---------------------------------------------------------------------------
# parse_cachevault branches (lines 119-124, 127)
# ---------------------------------------------------------------------------


def test_parse_cachevault_uses_cachevault_info_fallback() -> None:
    """Lines 119-124: Response with Cachevault_Info list fallback (Model→Type, Temp→Temperature)."""
    payload = success_payload(
        {
            "Cachevault_Info": [
                {
                    "Model": "CVPM02",
                    "State": "Optimal",
                    "Temp": "30C",
                    "Capacitance": "100%",
                }
            ]
        }
    )
    cachevault = parse_cachevault(payload)
    assert cachevault is not None
    assert cachevault.type == "CVPM02"
    assert cachevault.state == "Optimal"
    assert cachevault.temperature_celsius == 30
    assert cachevault.replacement_required is False


def test_parse_cachevault_uses_cachevault_info_preserves_replacement() -> None:
    """Line 124 setdefault: if Replacement required already present, keep it."""
    payload = success_payload(
        {
            "Cachevault_Info": [
                {
                    "Model": "CVPM02",
                    "State": "Optimal",
                    "Temp": "30C",
                    "Replacement required": "Yes",
                }
            ]
        }
    )
    cachevault = parse_cachevault(payload)
    assert cachevault is not None
    assert cachevault.replacement_required is True


def test_parse_cachevault_cachevault_info_without_model_or_temp() -> None:
    """Lines 120->122, 122->124: Cachevault_Info dict missing Model and Temp."""
    payload = success_payload(
        {
            "Cachevault_Info": [
                {
                    "Type": "CVPM02",
                    "State": "Optimal",
                    "Temperature": "30C",
                    "Replacement required": "No",
                }
            ]
        }
    )
    cv = parse_cachevault(payload)
    assert cv is not None
    assert cv.type == "CVPM02"
    assert cv.state == "Optimal"
    assert cv.temperature_celsius == 30


def test_parse_cachevault_raises_on_validation_error() -> None:
    """Line 127: ValidationError path. Provide a CV info dict that cannot validate."""
    payload = success_payload(
        {
            "Cachevault_Info": [
                {
                    "Model": "CVPM02",
                    "State": "Optimal",
                    "Temp": ["not", "a", "string"],
                }
            ]
        }
    )
    with pytest.raises(StorcliParseError, match="cachevault payload"):
        parse_cachevault(payload)


# ---------------------------------------------------------------------------
# _coerce_dg_id branches (lines 267-283)
# ---------------------------------------------------------------------------


def test_coerce_dg_id_none() -> None:
    assert _coerce_dg_id(None) is None


def test_coerce_dg_id_bool() -> None:
    assert _coerce_dg_id(True) is None
    assert _coerce_dg_id(False) is None


def test_coerce_dg_id_int() -> None:
    assert _coerce_dg_id(5) == 5


def test_coerce_dg_id_empty_string() -> None:
    assert _coerce_dg_id("") is None
    assert _coerce_dg_id("   ") is None


def test_coerce_dg_id_dash_and_na() -> None:
    assert _coerce_dg_id("-") is None
    assert _coerce_dg_id("N/A") is None


def test_coerce_dg_id_with_slash() -> None:
    assert _coerce_dg_id("5/2") == 5


def test_coerce_dg_id_invalid_string() -> None:
    assert _coerce_dg_id("abc") is None


def test_coerce_dg_id_other_type() -> None:
    """Fallback `return None` for non-str/int/None types (e.g. float, list)."""
    assert _coerce_dg_id(1.5) is None
    assert _coerce_dg_id([1, 2]) is None


# ---------------------------------------------------------------------------
# _coerce_count branches (lines 315-330)
# ---------------------------------------------------------------------------


def test_coerce_count_bool() -> None:
    assert _coerce_count(True) is None
    assert _coerce_count(False) is None


def test_coerce_count_int() -> None:
    assert _coerce_count(3) == 3


def test_coerce_count_float() -> None:
    assert _coerce_count(3.7) == 3


def test_coerce_count_empty_string() -> None:
    assert _coerce_count("") is None
    assert _coerce_count("   ") is None


def test_coerce_count_numeric_string() -> None:
    assert _coerce_count("5") == 5
    assert _coerce_count("2.0") == 2


def test_coerce_count_bad_string() -> None:
    assert _coerce_count("abc") is None


def test_coerce_count_other_type() -> None:
    assert _coerce_count([1, 2]) is None
    assert _coerce_count({"key": "value"}) is None


# ---------------------------------------------------------------------------
# _foreign_summary_counts branches (lines 299, 302, 304, 310->297)
# ---------------------------------------------------------------------------


def test_foreign_summary_counts_non_string_key_skipped() -> None:
    """Line 299: key is not str → skip."""
    response: dict[Any, Any] = {
        42: "skip me",
        "Total Foreign DG Count": 1,
        "Total Foreign Drive Count": 2,
    }
    assert _foreign_summary_counts(response) == (1, 2)


def test_foreign_summary_counts_missing_total_or_foreign() -> None:
    """Line 302: key missing total or foreign → skip."""
    response = {
        "Other Stat Count": 9,
        "Total DG Count": 9,  # missing 'foreign'
        "Foreign Drive Count": 9,  # missing 'total'
    }
    assert _foreign_summary_counts(response) == (0, 0)


def test_foreign_summary_counts_missing_count_keyword() -> None:
    """Line 304: key missing 'count' → skip."""
    response = {"Total Foreign DG Size": 9}
    assert _foreign_summary_counts(response) == (0, 0)


def test_foreign_summary_counts_negative_or_zero_skipped() -> None:
    """Line 306-307: coerced is None or <= 0 → skip."""
    response = {
        "Total Foreign DG Count": -1,
        "Total Foreign Drive Count": 0,
    }
    assert _foreign_summary_counts(response) == (0, 0)


def test_foreign_summary_counts_uncoercible_value_skipped() -> None:
    """Line 306: coerced is None (value not coercible) → skip."""
    response = {
        "Total Foreign DG Count": "abc",
        "Total Foreign Drive Count": "def",
    }
    assert _foreign_summary_counts(response) == (0, 0)


def test_foreign_summary_counts_unrelated_total_count_keyword() -> None:
    """Line 310->297: key contains total+foreign+count but neither dg nor drive → return loop."""
    response = {"Total Foreign Block Count": 7}
    assert _foreign_summary_counts(response) == (0, 0)


def test_foreign_summary_counts_only_keeps_first_occurrence() -> None:
    """`dg_count == 0` / `drive_count == 0` guard keeps the first match (lines 308-311)."""
    response: dict[str, Any] = {
        "Total Foreign DG Count": 1,
        "Total Other Foreign DG Count": 99,
        "Total Foreign Drive Count": 2,
        "Total Other Foreign Drive Count": 99,
    }
    assert _foreign_summary_counts(response) == (1, 2)


# ---------------------------------------------------------------------------
# _foreign_total_size_bytes branches (lines 342-345)
# ---------------------------------------------------------------------------


def test_foreign_total_size_bytes_returns_none_for_empty_inputs() -> None:
    """Line 349: no matching key + no disk groups → return None."""
    assert _foreign_total_size_bytes({}, []) is None


def test_foreign_total_size_bytes_skips_non_string_values() -> None:
    """Line 338-339: value is not a string → skip."""
    response = {"Total Size": 12345}  # not a string
    assert _foreign_total_size_bytes(response, []) is None


def test_foreign_total_size_bytes_uses_total_size_key() -> None:
    """Line 342-343: key contains total + size and value is a valid size string."""
    response = {"Total Foreign Size": "1.000 TB"}
    assert _foreign_total_size_bytes(response, []) == 10**12


def test_foreign_total_size_bytes_skips_bad_size_string() -> None:
    """Line 344-345: ValueError from size_string_to_bytes → continue."""
    response = {"Total Foreign Size": "not a valid size"}
    assert _foreign_total_size_bytes(response, []) is None


def test_foreign_total_size_bytes_falls_back_to_dg_sum() -> None:
    """Lines 346-348: no key match, fall back to sum of dg.size_bytes."""
    from megaraid_dashboard.storcli.models import ForeignConfigDiskGroup

    disk_groups = [
        ForeignConfigDiskGroup(dg_id=0, drive_count=2, size_bytes=10**9),
        ForeignConfigDiskGroup(dg_id=1, drive_count=2, size_bytes=2 * 10**9),
        ForeignConfigDiskGroup(dg_id=2, drive_count=2, size_bytes=None),
    ]
    assert _foreign_total_size_bytes({}, disk_groups) == 3 * 10**9


# ---------------------------------------------------------------------------
# _foreign_config_digest empty dg_tokens path (line 388 wrapper — actually 374-375)
# ---------------------------------------------------------------------------


def test_foreign_config_digest_no_disk_groups() -> None:
    """Line 374-375 (digest with empty disk_groups returns base)."""
    digest = _foreign_config_digest(
        dg_count=1, drive_count=2, total_size_bytes=None, disk_groups=[]
    )
    assert digest == "FC-DG1-PD2-UNKNOWN"


def test_foreign_config_digest_with_size() -> None:
    digest = _foreign_config_digest(
        dg_count=1, drive_count=2, total_size_bytes=5 * 10**9, disk_groups=[]
    )
    assert digest == "FC-DG1-PD2-5GB"


# ---------------------------------------------------------------------------
# parse_bbu validation error path (line 388)
# ---------------------------------------------------------------------------


def test_parse_bbu_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 388: TypeError/ValidationError path on parse_bbu.

    Force ``BbuInfo.model_validate`` to raise ``ValidationError`` to exercise
    the parser's catch block. Patching the parser module's binding ensures the
    parser picks up the patched callable.
    """
    from pydantic import ValidationError

    from megaraid_dashboard.storcli import parser as parser_module

    def _raise(*_args: Any, **_kwargs: Any) -> Any:
        raise ValidationError.from_exception_data("BbuInfo", [])

    monkeypatch.setattr(parser_module.BbuInfo, "model_validate", _raise)
    with pytest.raises(StorcliParseError, match="bbu payload"):
        parse_bbu(success_payload({"BBU_Info": [{"State": "Optimal"}]}))


# ---------------------------------------------------------------------------
# _parse_physical_drive missing port information (lines 402-403)
# ---------------------------------------------------------------------------


def test_parse_physical_drive_missing_port_information() -> None:
    """Lines 402-403: missing/empty Port Information → TypeError."""
    payload = success_payload(
        {
            "Drive /c0/e2/s0": [
                {
                    "EID:Slt": "2:0",
                    "DID": 14,
                    "State": "Onln",
                    "DG": 0,
                    "Size": "3.000 TB",
                    "Intf": "SATA",
                    "Med": "HDD",
                    "Model": "WDC",
                }
            ],
            "Drive /c0/e2/s0 - Detailed Information": {
                "Drive /c0/e2/s0 State": {
                    "Media Error Count": 0,
                    "Other Error Count": 0,
                    "Predictive Failure Count": 0,
                    "S.M.A.R.T alert flagged by drive": "No",
                    "Drive Temperature": "35C",
                },
                "Drive /c0/e2/s0 Device attributes": {
                    "SN": "WD-SN-0001",
                    "Model Number": "WDC WD30EFRX",
                    "Firmware Revision": "82.00A82",
                },
                "Drive /c0/e2/s0 Policies/Settings": {
                    "Port Information": [],  # Empty list → triggers TypeError
                },
            },
        }
    )
    with pytest.raises(StorcliParseError, match="physical drive payload"):
        parse_physical_drives(payload)


def test_parse_physical_drive_port_information_not_a_list() -> None:
    """Lines 401-403: Port Information not a list → TypeError."""
    payload = success_payload(
        {
            "Drive /c0/e2/s0": [
                {
                    "EID:Slt": "2:0",
                    "DID": 14,
                    "State": "Onln",
                    "DG": 0,
                    "Size": "3.000 TB",
                    "Intf": "SATA",
                    "Med": "HDD",
                    "Model": "WDC",
                }
            ],
            "Drive /c0/e2/s0 - Detailed Information": {
                "Drive /c0/e2/s0 State": {
                    "Media Error Count": 0,
                    "Other Error Count": 0,
                    "Predictive Failure Count": 0,
                    "S.M.A.R.T alert flagged by drive": "No",
                    "Drive Temperature": "35C",
                },
                "Drive /c0/e2/s0 Device attributes": {
                    "SN": "WD-SN-0001",
                    "Model Number": "WDC WD30EFRX",
                    "Firmware Revision": "82.00A82",
                },
                "Drive /c0/e2/s0 Policies/Settings": {
                    "Port Information": "not a list",
                },
            },
        }
    )
    with pytest.raises(StorcliParseError, match="physical drive payload"):
        parse_physical_drives(payload)


# ---------------------------------------------------------------------------
# _property_lists_to_mapping branches (lines 431, 433)
# ---------------------------------------------------------------------------


def test_parse_cachevault_property_list_skips_non_list_and_non_property_items() -> None:
    """Lines 431, 433: non-list values skipped; non-Property dicts skipped."""
    payload = success_payload(
        {
            "Cachevault_Info": [
                {"Property": "Type", "Value": "CVPM02"},
                {"Property": "State", "Value": "Optimal"},
                {"Property": "Temperature", "Value": "30C"},
                {"Property": "Replacement required", "Value": "No"},
                {"NotProperty": "ignored", "Value": "ignored"},
                "string item, not dict",
            ],
            "Some Scalar": "ignored",  # value not a list → skip (line 431)
            "Some Other List": [
                {"unrelated": "dict"},
                42,  # non-dict item inside list (line 433 condition false)
            ],
        }
    )
    cv = parse_cachevault(payload)
    assert cv is not None
    assert cv.type == "CVPM02"
    assert cv.state == "Optimal"


# ---------------------------------------------------------------------------
# _first_controller branches (lines 483-484, 486-487)
# ---------------------------------------------------------------------------


def test_first_controller_empty_list_raises() -> None:
    """Lines 482-484: Controllers is an empty list → StorcliParseError."""
    with pytest.raises(StorcliParseError, match="payload does not contain a controller"):
        _first_controller({"Controllers": []})


def test_first_controller_not_a_list_raises() -> None:
    """Lines 482-484: Controllers is not a list → StorcliParseError."""
    with pytest.raises(StorcliParseError, match="payload does not contain a controller"):
        _first_controller({"Controllers": "nope"})


def test_first_controller_missing_key_raises() -> None:
    """Lines 486-487: KeyError when 'Controllers' missing."""
    with pytest.raises(StorcliParseError, match="payload does not contain a controller"):
        _first_controller({})


def test_first_controller_non_mapping_first_item_raises() -> None:
    """Lines 485-487: TypeError when controllers[0] is not a mapping."""
    with pytest.raises(StorcliParseError, match="payload does not contain a controller"):
        _first_controller({"Controllers": ["not a mapping"]})


# ---------------------------------------------------------------------------
# _command_err_msg branches (lines 505-509)
# ---------------------------------------------------------------------------


def test_command_err_msg_detailed_status_not_a_list() -> None:
    """Line 505: Detailed Status not a list → fall through."""
    controller: dict[str, Any] = {
        "Command Status": {
            "Status": "Failure",
            "Description": "fallback desc",
            "Detailed Status": "not a list",
        }
    }
    assert _command_err_msg(controller) == "fallback desc"


def test_command_err_msg_item_without_errmsg() -> None:
    """Line 507->506: item is a dict but missing ErrMsg → skip."""
    controller: dict[str, Any] = {
        "Command Status": {
            "Status": "Failure",
            "Description": "use desc",
            "Detailed Status": [
                "not a dict",
                {"Status": "Failure", "ErrCd": 7},  # no ErrMsg
            ],
        }
    }
    assert _command_err_msg(controller) == "use desc"


def test_command_err_msg_falls_back_to_status_field() -> None:
    """Line 509: no Description → falls back to Status field."""
    controller: dict[str, Any] = {"Command Status": {"Status": "Failure"}}
    assert _command_err_msg(controller) == "Failure"


def test_command_err_msg_falls_back_to_unknown_error() -> None:
    """Line 509: nothing available → 'unknown error'."""
    controller: dict[str, Any] = {"Command Status": {}}
    assert _command_err_msg(controller) == "unknown error"


def test_command_err_msg_picks_first_errmsg() -> None:
    """Lines 506-508: pick the first ErrMsg-bearing dict in detailed_status."""
    controller: dict[str, Any] = {
        "Command Status": {
            "Status": "Failure",
            "Detailed Status": [
                {"Status": "Failure"},  # no ErrMsg
                {"Status": "Failure", "ErrMsg": "real error"},
                {"Status": "Failure", "ErrMsg": "later one"},
            ],
        }
    }
    assert _command_err_msg(controller) == "real error"


# ---------------------------------------------------------------------------
# parse_foreign_config StorcliParseError catch in _response_data (lines 154-155)
# ---------------------------------------------------------------------------


def test_parse_foreign_config_response_data_not_mapping_returns_absent() -> None:
    """Lines 153-155: _response_data raises → treat as absent."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": "this is a string",
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.present is False
    assert fc.digest == ""


# ---------------------------------------------------------------------------
# parse_foreign_config DG list branches (lines 242, 246, 249-253, 259)
# ---------------------------------------------------------------------------


def test_parse_foreign_config_skips_non_mapping_dg_entries() -> None:
    """Line 241-242: items in DG list that are not Mappings get skipped."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [
                        "string not a dict",
                        42,
                        {"DG": 0, "Size": "1.000 TB"},
                    ],
                    "FOREIGN PD LIST": [
                        {"EID:Slt": "2:0", "DG": 0},
                    ],
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.present is True
    assert fc.dg_count == 1


def test_parse_foreign_config_dg_id_unrecognized_skipped() -> None:
    """Lines 244-246: DG id None → skip the DG entry."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [
                        {"DG": "-", "Size": "1.000 TB"},  # dg_id None
                        {"DG": 0, "Size": "1.000 TB"},
                    ],
                    "FOREIGN PD LIST": [
                        {"EID:Slt": "2:0", "DG": 0},
                    ],
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.dg_count == 1
    assert fc.disk_groups[0].dg_id == 0


def test_parse_foreign_config_size_text_not_string_skipped() -> None:
    """Lines 249->240: size_text not a string → entry size remains None."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [
                        {"DG": 0, "Size": 12345},  # size is int, not str
                    ],
                    "FOREIGN PD LIST": [
                        {"EID:Slt": "2:0", "DG": 0},
                    ],
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.dg_count == 1
    assert fc.disk_groups[0].size_bytes is None


def test_parse_foreign_config_size_string_invalid_handled() -> None:
    """Lines 252-253: size_string_to_bytes raises ValueError → entry stays None."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [
                        {"DG": 0, "Size": "garbage"},
                    ],
                    "FOREIGN PD LIST": [
                        {"EID:Slt": "2:0", "DG": 0},
                    ],
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.dg_count == 1
    assert fc.disk_groups[0].size_bytes is None


def test_parse_foreign_config_drive_with_unrecognized_dg_id_skipped() -> None:
    """Lines 258-259: drives with dg_id None skip the size update loop."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [
                        {"DG": 0, "Size": "1.000 TB"},
                    ],
                    "FOREIGN PD LIST": [
                        {"EID:Slt": "2:0", "DG": "-"},  # dg_id None
                        {"EID:Slt": "2:1", "DG": 0},
                    ],
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.drive_count == 2
    assert fc.dg_count == 1
    assert fc.disk_groups[0].drive_count == 1


# ---------------------------------------------------------------------------
# _foreign_drives branch 224->223 (item without EID:Slt/DID skipped)
# ---------------------------------------------------------------------------


def test_parse_foreign_config_skips_drive_entries_without_id_keys() -> None:
    """Line 224->223: list items not containing EID:Slt or DID are skipped."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [
                        {"DG": 0, "Size": "1.000 TB"},
                    ],
                    "FOREIGN PD LIST": [
                        "not a mapping",
                        {"unrelated": "fields"},  # no EID:Slt/DID
                        {"EID:Slt": "2:0", "DG": 0},
                    ],
                    # extra "drive" keyword key with non-list value (line 222 check)
                    "drive summary": "scalar",
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.drive_count == 1


# ---------------------------------------------------------------------------
# _foreign_disk_groups: non-list value for a "dg" key (line 238-239)
# ---------------------------------------------------------------------------


def test_parse_foreign_config_dg_key_non_list_value_skipped() -> None:
    """When a 'dg' keyword key holds a non-list value, skip it."""
    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {"Status": "Success"},
                "Response Data": {
                    "FOREIGN DG LIST": [{"DG": 0, "Size": "1.000 TB"}],
                    "FOREIGN PD LIST": [{"EID:Slt": "2:0", "DG": 0}],
                    "DG Summary": "non-list scalar",
                },
            }
        ]
    }
    fc = parse_foreign_config(payload)
    assert fc.dg_count == 1


# ---------------------------------------------------------------------------
# _command_failed sanity (covers normal usage)
# ---------------------------------------------------------------------------


def test_command_failed_true_on_failure_status() -> None:
    controller: dict[str, Any] = {"Command Status": {"Status": "Failure"}}
    assert _command_failed(controller) is True


def test_command_failed_false_on_success_status() -> None:
    controller: dict[str, Any] = {"Command Status": {"Status": "Success"}}
    assert _command_failed(controller) is False


# ---------------------------------------------------------------------------
# Coverage for ensure_command_succeeded line 454 (helper alias)
# ---------------------------------------------------------------------------


def test_ensure_command_succeeded_raises_on_failure() -> None:
    from megaraid_dashboard.storcli import ensure_command_succeeded

    payload: dict[str, Any] = {
        "Controllers": [
            {
                "Command Status": {
                    "Status": "Failure",
                    "Detailed Status": [{"ErrMsg": "boom"}],
                }
            }
        ]
    }
    with pytest.raises(StorcliCommandFailed, match="boom"):
        ensure_command_succeeded(payload)


def test_ensure_command_succeeded_no_raise_on_success() -> None:
    from megaraid_dashboard.storcli import ensure_command_succeeded

    ensure_command_succeeded(success_payload({}))
