from __future__ import annotations

from collections.abc import Callable

import pytest

from megaraid_dashboard.services.drive_actions import (
    build_make_hot_spare_command,
    build_mark_unconfigured_bad_command,
    build_mark_unconfigured_good_command,
    build_spin_down_command,
    can_make_hot_spare,
    can_mark_ubad,
    can_mark_ugood,
    can_spin_down,
)

DRIVE_STATES = ("UGood", "UBad", "Onln", "Offln", "Missing", "Rbld", "Rebld", "Failed")


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (build_mark_unconfigured_bad_command, ["/c0/e2/s0", "set", "bad"]),
        (build_mark_unconfigured_good_command, ["/c0/e2/s0", "set", "good"]),
        (build_spin_down_command, ["/c0/e2/s0", "spindown"]),
    ],
)
def test_advanced_drive_command_builders(
    builder: Callable[[int, int], list[str]],
    expected: list[str],
) -> None:
    assert builder(2, 0) == expected


def test_build_make_hot_spare_command() -> None:
    assert build_make_hot_spare_command(2, 0, 3) == [
        "/c0/e2/s0",
        "add",
        "hotsparedrive",
        "dgs=3",
    ]


@pytest.mark.parametrize("dg_id", [-1, 64, True, "0"])
def test_build_make_hot_spare_command_rejects_invalid_dg_id(dg_id: object) -> None:
    with pytest.raises(ValueError, match="dg_id must be int"):
        build_make_hot_spare_command(2, 0, dg_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("state", DRIVE_STATES)
def test_can_mark_ubad_only_allows_ugood(state: str) -> None:
    assert can_mark_ubad(state) is (state == "UGood")


@pytest.mark.parametrize("state", DRIVE_STATES)
def test_can_mark_ugood_only_allows_ubad(state: str) -> None:
    assert can_mark_ugood(state) is (state == "UBad")


@pytest.mark.parametrize("state", DRIVE_STATES)
def test_can_spin_down_allows_online_ugood_or_ubad(state: str) -> None:
    assert can_spin_down(state) is (state in {"Onln", "UGood", "UBad"})


@pytest.mark.parametrize("state", DRIVE_STATES)
@pytest.mark.parametrize("has_existing_dgs", [False, True])
def test_can_make_hot_spare_requires_ugood_and_existing_dg(
    state: str,
    has_existing_dgs: bool,
) -> None:
    assert can_make_hot_spare(state, has_existing_dgs) is (state == "UGood" and has_existing_dgs)
