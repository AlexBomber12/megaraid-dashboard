from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from megaraid_dashboard.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"


def test_phase_config_non_interactive_writes_complete_env_file(tmp_path: Path) -> None:
    result = _run_phase_config(
        tmp_path,
        args=["--non-interactive"],
        install_env=_install_env(tmp_path),
    )

    env_file = tmp_path / "etc" / "env"
    values = _read_env_file(env_file)

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert values == {
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": "$2b$secret-admin-password",
        "ALERT_SMTP_HOST": "smtp.example.test",
        "ALERT_SMTP_PORT": "587",
        "ALERT_SMTP_USER": "alert-user",
        "ALERT_SMTP_PASSWORD": "smtp-token",
        "ALERT_SMTP_USE_STARTTLS": "true",
        "ALERT_FROM": "megaraid@example.test",
        "ALERT_TO": "ops@example.test",
        "STORCLI_PATH": "/usr/local/sbin/storcli64",
        "STORCLI_USE_SUDO": "true",
        "LOG_LEVEL": "info",
        "METRICS_INTERVAL_SECONDS": "300",
        "DATABASE_URL": f"sqlite:///{tmp_path}/data/megaraid.db",
    }
    assert values["ADMIN_PASSWORD_HASH"].startswith("$2b$")
    Settings(_env_file=env_file)


def test_phase_config_non_interactive_lists_missing_required_values(tmp_path: Path) -> None:
    result = _run_phase_config(
        tmp_path,
        args=["--non-interactive"],
        install_env={},
    )

    assert result.returncode == 1
    assert "required config missing in non-interactive mode" in result.stderr
    for var in (
        "MEGARAID_INSTALL_ADMIN_PASSWORD",
        "MEGARAID_INSTALL_ALERT_SMTP_HOST",
        "MEGARAID_INSTALL_ALERT_SMTP_USER",
        "MEGARAID_INSTALL_ALERT_SMTP_PASSWORD",
        "MEGARAID_INSTALL_ALERT_FROM",
        "MEGARAID_INSTALL_ALERT_TO",
    ):
        assert var in result.stderr


def test_phase_config_preserves_existing_values_without_force(tmp_path: Path) -> None:
    first = _run_phase_config(
        tmp_path,
        args=["--non-interactive"],
        install_env=_install_env(tmp_path),
    )
    second = _run_phase_config(
        tmp_path,
        args=["--non-interactive"],
        install_env=_install_env(
            tmp_path,
            admin_username="changed-admin",
            admin_password="changed-password",
            smtp_host="changed-smtp.example.test",
        ),
    )

    values = _read_env_file(tmp_path / "etc" / "env")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert values["ADMIN_USERNAME"] == "admin"
    assert values["ADMIN_PASSWORD_HASH"] == "$2b$secret-admin-password"
    assert values["ALERT_SMTP_HOST"] == "smtp.example.test"


def test_phase_config_force_reconfigure_overwrites_existing_values(tmp_path: Path) -> None:
    first = _run_phase_config(
        tmp_path,
        args=["--non-interactive"],
        install_env=_install_env(tmp_path),
    )
    second = _run_phase_config(
        tmp_path,
        args=["--non-interactive", "--force-reconfigure"],
        install_env=_install_env(
            tmp_path,
            admin_username="changed-admin",
            admin_password="changed-password",
            smtp_host="changed-smtp.example.test",
        ),
    )

    values = _read_env_file(tmp_path / "etc" / "env")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert values["ADMIN_USERNAME"] == "changed-admin"
    assert values["ADMIN_PASSWORD_HASH"] == "$2b$changed-password"
    assert values["ALERT_SMTP_HOST"] == "changed-smtp.example.test"


def _run_phase_config(
    tmp_path: Path,
    *,
    args: list[str],
    install_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    env_file = tmp_path / "etc" / "env"
    env_file.parent.mkdir(exist_ok=True)
    env_file.touch(mode=0o600, exist_ok=True)
    bin_dir = _stub_bin(tmp_path)
    prefix = tmp_path / "prefix"
    data_dir = tmp_path / "data"
    prefix.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    python = prefix / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nprintf '$2b$%s\\n' \"$3\"\n")
    python.chmod(0o755)

    command_parts = ["source", str(INSTALL_SCRIPT) + ";", "parse_args", *args, ";", "phase_config"]
    command = " ".join(command_parts)
    return subprocess.run(
        ["bash", "-c", command],
        check=False,
        env={
            **os.environ,
            **install_env,
            "DATA_DIR": str(data_dir),
            "ENV_FILE": str(env_file),
            "INSTALL_PREFIX": str(prefix),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )


def _install_env(
    tmp_path: Path,
    *,
    admin_username: str = "admin",
    admin_password: str = "secret-admin-password",
    smtp_host: str = "smtp.example.test",
) -> dict[str, str]:
    return {
        "MEGARAID_INSTALL_ADMIN_USERNAME": admin_username,
        "MEGARAID_INSTALL_ADMIN_PASSWORD": admin_password,
        "MEGARAID_INSTALL_ALERT_SMTP_HOST": smtp_host,
        "MEGARAID_INSTALL_ALERT_SMTP_USER": "alert-user",
        "MEGARAID_INSTALL_ALERT_SMTP_PASSWORD": "smtp-token",
        "MEGARAID_INSTALL_ALERT_FROM": "megaraid@example.test",
        "MEGARAID_INSTALL_ALERT_TO": "ops@example.test",
        "MEGARAID_INSTALL_ALERT_SMTP_PORT": "587",
        "MEGARAID_INSTALL_STORCLI_PATH": "/usr/local/sbin/storcli64",
        "MEGARAID_INSTALL_LOG_LEVEL": "info",
        "MEGARAID_INSTALL_METRICS_INTERVAL_SECONDS": "300",
    }


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        key, value = line.split("=", maxsplit=1)
        values[key] = value
    return values


def _stub_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    _write_executable(
        bin_dir / "install",
        "#!/bin/sh\n"
        "mode=''\n"
        "target=''\n"
        "while [ $# -gt 0 ]; do\n"
        '  case "$1" in\n'
        '    -m) mode="$2"; shift 2 ;;\n'
        "    -o|-g) shift 2 ;;\n"
        '    *) target="$1"; shift ;;\n'
        "  esac\n"
        "done\n"
        ': > "$target"\n'
        '[ -z "$mode" ] || chmod "$mode" "$target"\n',
    )
    _write_executable(bin_dir / "chown", "#!/bin/sh\nexit 0\n")
    return bin_dir


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)
