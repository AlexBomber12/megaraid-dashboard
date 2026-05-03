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


def test_phase_venv_reuses_existing_venv_and_upgrades_pip(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    pip = prefix / ".venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    log = tmp_path / "commands.log"
    pip.write_text(f"#!/bin/sh\nprintf 'pip %s\\n' \"$*\" >> {log}\n")
    pip.chmod(0o755)
    bin_dir = _stub_bin(tmp_path)
    _write_executable(
        bin_dir / "sudo",
        '#!/bin/sh\nif [ "$1" = "-u" ]; then\n  shift 2\nfi\nexec "$@"\n',
    )

    result = _run_phase(
        "phase_venv",
        env={
            "INSTALL_PREFIX": str(prefix),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "venv exists, skip" in result.stdout
    assert log.read_text() == "pip install --upgrade pip>=24\n"


def test_phase_pip_fails_fast_when_pypi_unreachable(tmp_path: Path) -> None:
    bin_dir = _stub_bin(tmp_path)
    _write_executable(bin_dir / "curl", "#!/bin/sh\nexit 7\n")
    _write_executable(bin_dir / "rsync", "#!/bin/sh\nexit 0\n")

    result = _run_phase(
        "phase_pip",
        env={
            "INSTALL_PREFIX": str(tmp_path / "prefix"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode == 1
    assert "pypi.org unreachable; cannot pip install" in result.stderr


def test_phase_pip_copies_source_and_installs_editable_package(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    pip = prefix / ".venv" / "bin" / "pip"
    pip.parent.mkdir(parents=True)
    log = tmp_path / "commands.log"
    pip.write_text(f"#!/bin/sh\nprintf 'pip %s\\n' \"$*\" >> {log}\n")
    pip.chmod(0o755)

    bin_dir = _stub_bin(tmp_path)
    _write_executable(bin_dir / "curl", "#!/bin/sh\nexit 0\n")
    _write_executable(
        bin_dir / "install",
        '#!/bin/sh\nwhile [ $# -gt 1 ]; do\n  shift\ndone\nmkdir -p "$1"\n',
    )
    _write_executable(
        bin_dir / "rsync",
        f"#!/bin/sh\nprintf 'rsync %s\\n' \"$*\" >> {log}\n",
    )
    _write_executable(
        bin_dir / "chown",
        f"#!/bin/sh\nprintf 'chown %s\\n' \"$*\" >> {log}\n",
    )
    _write_executable(
        bin_dir / "sudo",
        '#!/bin/sh\nif [ "$1" = "-u" ]; then\n  shift 2\nfi\nexec "$@"\n',
    )

    result = _run_phase(
        "phase_pip",
        env={
            "INSTALL_PREFIX": str(prefix),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stderr
    logged = log.read_text()
    assert "--exclude=.git/" in logged
    assert f"{REPO_ROOT}/ {prefix}/src/" in logged
    assert f"chown -R raid-monitor:raid-monitor {prefix}/src" in logged
    assert f"pip install -e {prefix}/src" in logged


def test_phase_smoke_logs_imported_version(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    python = prefix / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nprintf '9.8.7-test\\n'\n")
    python.chmod(0o755)

    bin_dir = _stub_bin(tmp_path)
    _write_executable(
        bin_dir / "sudo",
        '#!/bin/sh\nif [ "$1" = "-u" ]; then\n  shift 2\nfi\nexec "$@"\n',
    )

    result = _run_phase(
        "phase_smoke",
        env={
            "INSTALL_PREFIX": str(prefix),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "imported megaraid_dashboard 9.8.7-test" in result.stdout


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


def _run_phase(phase: str, *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; {phase}"],
        check=False,
        env={**os.environ, **env},
        text=True,
        capture_output=True,
    )


def _stub_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    return bin_dir


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)
