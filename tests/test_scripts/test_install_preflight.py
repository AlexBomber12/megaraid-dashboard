from __future__ import annotations

import os
import pwd
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"


def run_install(
    *,
    env: dict[str, str] | None = None,
    preexec_fn: object | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)

    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        check=False,
        env=merged_env,
        preexec_fn=preexec_fn,
        text=True,
        capture_output=True,
    )


def test_install_shellcheck_passes() -> None:
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck not installed")

    result = subprocess.run(
        ["shellcheck", str(INSTALL_SCRIPT)],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_install_requires_root() -> None:
    preexec_fn = None
    if os.geteuid() == 0:
        nobody = pwd.getpwnam("nobody")

        def demote_to_nobody() -> None:
            os.setgid(nobody.pw_gid)
            os.setuid(nobody.pw_uid)

        preexec_fn = demote_to_nobody

    result = run_install(preexec_fn=preexec_fn)

    assert result.returncode == 1
    assert "must run as root" in result.stderr


def test_install_fails_on_non_ubuntu_os_release(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=fedora\nVERSION_ID="40"\n')
    storcli = tmp_path / "storcli64"
    storcli.write_text("#!/bin/sh\nexit 0\n")
    storcli.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_preflight"],
        check=False,
        env={
            **os.environ,
            "OS_RELEASE_FILE": str(os_release),
            "STORCLI_PATH": str(storcli),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "expected Ubuntu, got ID=fedora" in result.stderr
