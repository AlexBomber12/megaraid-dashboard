"""Fail closed on a new source control or mutation route without reviewed coverage."""

import ast
import json
import re
from pathlib import Path

from megaraid_dashboard.web.routes import router
from tests.safety.inventory import ROOT, source_controls
from tests.safety.scenarios import ACTIONS

BROWSER_TESTS = {
    "locate": "test_locate_flow.py::test_locate_browser_result",
    "buzzer": "test_buzzer_control_flow.py::test_buzzer_control_flow",
    "advanced": "test_drive_detail_page.py::test_advanced_action_browser_matrix",
    "patrol-read": "test_controller_operations.py::test_controller_operation_browser_matrix",
    "consistency-check": "test_controller_operations.py::test_controller_operation_browser_matrix",
    "replace": "test_replace_wizard_flow.py::test_replace_offline_missing_browser",
    "foreign": "test_foreign_configuration.py::test_foreign_action_browser",
}


def test_all_source_controls_have_reviewed_inventory() -> None:
    baseline = json.loads((ROOT / "docs/operator-controls.json").read_text())
    assert source_controls() == baseline, (
        "UI control inventory changed. Review docs/OPERATOR_SAFETY.md, add browser/contract "
        "scenarios, then update docs/operator-controls.json. Do not regenerate without review."
    )


def test_every_mutation_route_has_browser_and_failure_matrix() -> None:
    routes = {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}
    }
    known = {re.sub(r"252:0", "{enclosure}:{slot}", action.path) for action in ACTIONS}
    assert routes == {
        ("POST", path) for path in known | {"/maintenance/start", "/maintenance/stop"}
    }
    assert len(ACTIONS) == len({action.path for action in ACTIONS})
    for action in ACTIONS:
        reference = BROWSER_TESTS[action.family]
        if action.name == "replace-insert":
            reference = "test_replace_wizard_flow.py::test_replacement_insert_browser"
        path, function = reference.split("::")
        assert function in {
            node.name
            for node in ast.walk(ast.parse((ROOT / "tests/e2e" / path).read_text()))
            if isinstance(node, ast.FunctionDef)
        }
    # Maintenance is a database mutation with its own atomicity and browser scenarios.
    path = ROOT / "tests/e2e/test_maintenance_flow.py"
    assert "test_maintenance_start_stop_persists_and_refreshes" in path.read_text()


def test_browser_suite_cannot_reintroduce_csrf_workaround() -> None:
    for path in (ROOT / "tests/e2e").glob("*.py"):
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"add_cookies", "set_extra_http_headers"}, path
            if isinstance(node, ast.Dict):
                for key in node.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        assert key.value.lower() not in {"cookie", "x-csrf-token"}, path


def test_documented_browser_references_exist() -> None:
    text = (ROOT / "docs/OPERATOR_SAFETY.md").read_text()
    for reference in re.findall(r"`(tests/[\w/]+\.py)`", text):
        assert Path(ROOT / reference).is_file(), reference
