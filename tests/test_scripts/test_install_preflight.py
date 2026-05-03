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


def test_install_accepts_quoted_ubuntu_os_release(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID="ubuntu"\nVERSION_ID="24.04"\n')
    storcli = tmp_path / "storcli64"
    storcli.write_text("#!/bin/sh\nexit 0\n")
    storcli.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ss = bin_dir / "ss"
    ss.write_text(
        "#!/bin/sh\nprintf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n'\n"
    )
    ss.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_preflight"],
        check=False,
        env={
            **os.environ,
            "OS_RELEASE_FILE": str(os_release),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STORCLI_PATH": str(storcli),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr


def test_install_fails_when_ss_probe_errors(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID="ubuntu"\nVERSION_ID="24.04"\n')
    storcli = tmp_path / "storcli64"
    storcli.write_text("#!/bin/sh\nexit 0\n")
    storcli.chmod(0o755)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ss = bin_dir / "ss"
    ss.write_text("#!/bin/sh\necho 'ss exploded' >&2\nexit 42\n")
    ss.chmod(0o755)

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_preflight"],
        check=False,
        env={
            **os.environ,
            "OS_RELEASE_FILE": str(os_release),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STORCLI_PATH": str(storcli),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "ss port probe failed: ss exploded" in result.stderr


def test_install_rejects_existing_login_user(tmp_path: Path) -> None:
    bin_dir = _existing_user_stub_bin(
        tmp_path,
        passwd_entry="raid-monitor:x:1001:1001::/home/raid-monitor:/bin/bash",
        group_entry="raid-monitor:x:1001:",
    )

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_user"],
        check=False,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "user raid-monitor exists but is not a system user" in result.stderr


def test_install_accepts_existing_service_user(tmp_path: Path) -> None:
    bin_dir = _existing_user_stub_bin(
        tmp_path,
        passwd_entry="raid-monitor:x:999:999::/var/lib/megaraid-dashboard:/usr/sbin/nologin",
        group_entry="raid-monitor:x:999:",
    )

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_user"],
        check=False,
        env={
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "matches service account policy" in result.stdout


def _existing_user_stub_bin(tmp_path: Path, *, passwd_entry: str, group_entry: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    id_command = bin_dir / "id"
    id_command.write_text(
        '#!/bin/sh\nif [ "$1" = "-u" ] && [ "$2" = "raid-monitor" ]; then\n  exit 0\nfi\nexit 1\n'
    )
    id_command.chmod(0o755)

    getent = bin_dir / "getent"
    getent.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "passwd" ] && [ "$2" = "raid-monitor" ]; then\n'
        f"  printf '%s\\n' {passwd_entry!r}\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "group" ] && [ "$2" = "999" ]; then\n'
        f"  printf '%s\\n' {group_entry!r}\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "group" ] && [ "$2" = "1001" ]; then\n'
        f"  printf '%s\\n' {group_entry!r}\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    getent.chmod(0o755)

    return bin_dir
