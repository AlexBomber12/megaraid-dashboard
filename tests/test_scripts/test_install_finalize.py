from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"
UNINSTALL_SCRIPT = REPO_ROOT / "scripts" / "uninstall.sh"


def test_bash_syntax_passes_on_install_and_uninstall_scripts() -> None:
    for script in (INSTALL_SCRIPT, UNINSTALL_SCRIPT):
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            text=True,
            capture_output=True,
        )

        assert result.returncode == 0, result.stderr


def test_shellcheck_passes_on_install_and_uninstall_scripts() -> None:
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")

    result = subprocess.run(
        ["shellcheck", str(INSTALL_SCRIPT), str(UNINSTALL_SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_phase_sudoers_writes_valid_fragment(tmp_path: Path) -> None:
    if shutil.which("visudo") is None:
        pytest.skip("visudo not installed")

    sudoers = tmp_path / "sudoers.d" / "megaraid-dashboard"

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_sudoers"],
        check=False,
        env={
            **os.environ,
            "INSTALL_USER": "raid-monitor",
            "STORCLI_PATH": "/usr/local/sbin/storcli64",
            "SUDOERS_FILE": str(sudoers),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert sudoers.read_text() == "raid-monitor ALL=(root) NOPASSWD: /usr/local/sbin/storcli64\n"
    assert stat.S_IMODE(sudoers.stat().st_mode) == 0o440

    visudo = subprocess.run(
        ["visudo", "-c", "-f", str(sudoers)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert visudo.returncode == 0, visudo.stderr


def test_systemd_unit_file_referenced_by_installer_exists() -> None:
    assert (REPO_ROOT / "deploy" / "megaraid-dashboard.service").is_file()
    assert "deploy/megaraid-dashboard.service" in INSTALL_SCRIPT.read_text()


def test_journald_drop_in_file_referenced_by_installer_exists() -> None:
    assert (REPO_ROOT / "deploy" / "journald-megaraid.conf").is_file()
    assert "deploy/journald-megaraid.conf" in INSTALL_SCRIPT.read_text()
