from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from megaraid_dashboard.app import create_app
from megaraid_dashboard.config import get_settings
from megaraid_dashboard.web.templates import create_templates
from tests.conftest import TEST_ADMIN_PASSWORD_HASH, TEST_AUTH_HEADER

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "src" / "megaraid_dashboard" / "templates"


@pytest.fixture(autouse=True)
def app_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("ALERT_SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("ALERT_SMTP_PORT", "587")
    monkeypatch.setenv("ALERT_SMTP_USER", "alert@example.test")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "test-token")
    monkeypatch.setenv("ALERT_FROM", "alert@example.test")
    monkeypatch.setenv("ALERT_TO", "ops@example.test")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_ADMIN_PASSWORD_HASH)
    monkeypatch.setenv("STORCLI_PATH", "/usr/local/sbin/storcli64")
    monkeypatch.setenv("METRICS_INTERVAL_SECONDS", "300")
    monkeypatch.setenv("COLLECTOR_ENABLED", "false")
    monkeypatch.setenv("COLLECTOR_LOCK_PATH", str(tmp_path / "collector.lock"))
    monkeypatch.setenv("METRICS_LOCK_PATH", str(tmp_path / "metrics.lock"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_layout_shell_renders_landmarks_and_version_label() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert '<header class="site-header">' in response.text
    assert '<main class="page-content">' in response.text
    assert '<footer class="site-footer">' in response.text
    assert "Version " in response.text
    assert "Build " in response.text
    assert "hero" not in response.text.lower()


def test_layout_shell_content_uses_page_content_container() -> None:
    test_app = create_app()
    with TestClient(test_app, headers=TEST_AUTH_HEADER) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert '<main class="page-content">' in response.text
    assert "SERVER RAID Status" in response.text


def test_status_bar_block_renders_only_when_populated() -> None:
    rendered_empty = _render_child_template("{% block content %}Body{% endblock %}")
    rendered_populated = _render_child_template(
        "{% block content %}Body{% endblock %}"
        "{% block status_bar %}<span>Collector online</span>{% endblock %}"
    )

    assert 'class="status-bar"' not in rendered_empty
    assert '<div class="status-bar" role="status">' in rendered_populated
    assert "<span>Collector online</span>" in rendered_populated


def test_maintenance_banner_still_renders_when_active() -> None:
    rendered = _render_child_template(
        "{% block content %}Body{% endblock %}",
        maintenance_state=SimpleNamespace(active=True, started_by="admin", expires_at=None),
    )

    assert 'class="maintenance-banner"' in rendered
    assert "Maintenance mode active" in rendered


def _render_child_template(
    source: str,
    *,
    maintenance_state: SimpleNamespace | None = None,
) -> str:
    template = create_templates(TEMPLATE_DIR).env.from_string(
        '{% extends "layouts/base.html" %}' + source
    )
    return template.render(
        {
            "active_nav": "overview",
            "request": _StaticRequest(),
            "current_utc_label": "2026-05-04T00:00:00Z",
            "maintenance_state": maintenance_state or SimpleNamespace(active=False),
            "static_asset_version": "asset123",
        }
    )


class _StaticRequest:
    def url_for(self, name: str, *, path: str | None = None) -> SimpleNamespace:
        if name == "static":
            return SimpleNamespace(path=f"/static/{path}")
        return SimpleNamespace(path=f"/{name}")
