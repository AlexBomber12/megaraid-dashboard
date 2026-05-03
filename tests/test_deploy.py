from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_UNIT = REPO_ROOT / "deploy" / "megaraid-dashboard.service"


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
