from __future__ import annotations

from megaraid_dashboard.services.drive_actions import (
    build_foreign_config_clear_command,
    build_foreign_config_import_command,
    build_foreign_config_show_command,
)


def test_build_foreign_config_show_command() -> None:
    assert build_foreign_config_show_command() == ["/c0/fall", "show", "all", "J"]


def test_build_foreign_config_import_command() -> None:
    assert build_foreign_config_import_command() == ["/c0/fall", "import", "J"]


def test_build_foreign_config_clear_command() -> None:
    assert build_foreign_config_clear_command() == ["/c0/fall", "delete", "J"]


def test_foreign_config_builders_return_distinct_arg_lists() -> None:
    show = build_foreign_config_show_command()
    show.append("MUTATED")
    assert build_foreign_config_show_command() == ["/c0/fall", "show", "all", "J"]
