from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_UNIT = REPO_ROOT / "deploy" / "megaraid-dashboard.service"
NGINX_SAMPLE = REPO_ROOT / "deploy" / "nginx" / "megaraid.conf.sample"


def test_systemd_default_database_url_is_writable_under_hardening() -> None:
    unit = SERVICE_UNIT.read_text(encoding="utf-8")

    assert "ProtectSystem=strict" in unit
    assert "WorkingDirectory=/opt/megaraid-dashboard" in unit
    assert "ReadWritePaths=/var/lib/megaraid-dashboard" in unit
    assert "Environment=DATABASE_URL=sqlite:////var/lib/megaraid-dashboard/megaraid.db" in unit


def test_systemd_environment_file_can_override_default_database_url() -> None:
    lines = SERVICE_UNIT.read_text(encoding="utf-8").splitlines()

    default_index = lines.index(
        "Environment=DATABASE_URL=sqlite:////var/lib/megaraid-dashboard/megaraid.db"
    )
    environment_file_index = lines.index("EnvironmentFile=/etc/megaraid-dashboard/env")

    assert default_index < environment_file_index


def test_nginx_sample_limits_only_exact_dashboard_entrypoint() -> None:
    config = NGINX_SAMPLE.read_text(encoding="utf-8")

    exact_entrypoint = _nginx_location_block(config, "location = /raid/ {")
    catch_all = _nginx_location_block(config, "location /raid/ {")

    assert "limit_req zone=raid_login burst=2 nodelay;" in exact_entrypoint
    assert "limit_req_status 429;" in exact_entrypoint
    assert "limit_req zone=raid_login" not in catch_all
    assert "limit_req_status" not in catch_all


def _nginx_location_block(config: str, header: str) -> str:
    start = config.index(header)
    end = config.index("\n    }", start)
    return config[start:end]
