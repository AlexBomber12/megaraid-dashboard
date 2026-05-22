from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from megaraid_dashboard import __version__
from megaraid_dashboard.web.templates import create_templates

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = REPO_ROOT / "src" / "megaraid_dashboard" / "templates"


class StaticRequest:
    def url_for(self, name: str, *, path: str | None = None) -> SimpleNamespace:
        if name == "static":
            return SimpleNamespace(path=f"/static/{path}")
        return SimpleNamespace(path=f"/{name}")


def test_package_version_is_semver_string() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(\+\w+)?", __version__)


def test_base_layout_no_longer_renders_legacy_build_footer() -> None:
    rendered = _render_base_footer()

    assert 'class="site-footer"' not in rendered
    assert "Build " not in rendered


def test_wheel_build_uses_project_version(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--no-isolation",
            "--wheel",
            "--outdir",
            str(tmp_path),
            str(REPO_ROOT),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    wheel_names = sorted(path.name for path in tmp_path.glob("*.whl"))

    assert wheel_names == [f"megaraid_dashboard-{__version__}-py3-none-any.whl"]


def _render_base_footer() -> str:
    template = create_templates(TEMPLATE_DIR).env.get_template("layouts/base.html")
    return template.render(
        {
            "request": StaticRequest(),
            "active_nav": "overview",
            "current_utc_label": "2026-05-04T00:00:00Z",
            "maintenance_state": SimpleNamespace(active=False),
            "static_asset_version": "asset123",
        }
    )
