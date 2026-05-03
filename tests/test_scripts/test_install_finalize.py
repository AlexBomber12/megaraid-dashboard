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
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    storcli = tmp_path / "usr" / "local" / "sbin" / "storcli64"
    storcli.parent.mkdir(parents=True)
    _write_executable(storcli, "#!/bin/sh\nexit 0\n")
    sudoers = tmp_path / "sudoers.d" / "megaraid-dashboard"
    _write_executable(
        bin_dir / "stat",
        "#!/bin/sh\n"
        "last=\n"
        'for arg in "$@"; do last="$arg"; done\n'
        'case "$last" in\n'
        "  *) printf '0 -rwxr-xr-x\\n' ;;\n"
        "esac\n",
    )
    _write_executable(bin_dir / "visudo", "#!/bin/sh\nexit 0\n")

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_sudoers"],
        check=False,
        env={
            **os.environ,
            "INSTALL_USER": "raid-monitor",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STORCLI_PATH": str(storcli),
            "SUDOERS_FILE": str(sudoers),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert sudoers.read_text() == (
        f"Cmnd_Alias MEGARAID_DASHBOARD_STORCLI = {storcli} /c0 show all J, "
        f"  {storcli} /c0/vall show all J, "
        f"  {storcli} /c0/eall/sall show all J, "
        f"  {storcli} /c0/cv show all J, "
        f"  {storcli} /c0/bbu show all J\n"
        "raid-monitor ALL=(root) NOPASSWD: MEGARAID_DASHBOARD_STORCLI\n"
    )
    assert stat.S_IMODE(sudoers.stat().st_mode) == 0o440


def test_phase_sudoers_rejects_non_root_owned_storcli(tmp_path: Path) -> None:
    result, sudoers = _run_phase_sudoers_with_stat(
        tmp_path,
        storcli_stat_output="1001 -rwxr-xr-x",
    )

    assert result.returncode == 1
    assert "must be owned by root before sudoers grant" in result.stderr
    assert not sudoers.exists()


def test_phase_sudoers_rejects_group_writable_storcli(tmp_path: Path) -> None:
    result, sudoers = _run_phase_sudoers_with_stat(
        tmp_path,
        storcli_stat_output="0 -rwxrwxr-x",
    )

    assert result.returncode == 1
    assert "must not be writable by group or other before sudoers grant" in result.stderr
    assert not sudoers.exists()


def test_systemd_unit_file_referenced_by_installer_exists() -> None:
    assert (REPO_ROOT / "deploy" / "megaraid-dashboard.service").is_file()
    assert "deploy/megaraid-dashboard.service" in INSTALL_SCRIPT.read_text()


def test_journald_drop_in_file_referenced_by_installer_exists() -> None:
    assert (REPO_ROOT / "deploy" / "journald-megaraid.conf").is_file()
    assert "deploy/journald-megaraid.conf" in INSTALL_SCRIPT.read_text()


def test_phase_finalize_retries_healthz_until_ready(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_attempts = tmp_path / "curl-attempts"
    sleeps = tmp_path / "sleeps"

    _write_executable(
        bin_dir / "systemctl",
        "#!/bin/sh\n"
        'if [ "$1" = "is-active" ] && [ "$2" != "--quiet" ]; then\n'
        "  printf 'active\\n'\n"
        "fi\n",
    )
    _write_executable(
        bin_dir / "curl",
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"--connect-timeout 2"*"--max-time 5"*"/healthz"*) ;;\n'
        '  *) printf "unexpected curl args: %s\\n" "$*" >&2; exit 2 ;;\n'
        "esac\n"
        f"attempts={curl_attempts}\n"
        "count=0\n"
        '[ -f "$attempts" ] && count="$(cat "$attempts")"\n'
        "count=$((count + 1))\n"
        'printf "%s\\n" "$count" > "$attempts"\n'
        'if [ "$count" -lt 3 ]; then\n'
        "  exit 7\n"
        "fi\n"
        'printf \'{"status":"ok"}\\n\'\n',
    )
    _write_executable(
        bin_dir / "sleep",
        f"#!/bin/sh\nprintf '%s\\n' \"$1\" >> {sleeps}\n",
    )
    _write_executable(
        bin_dir / "hostname",
        "#!/bin/sh\nprintf 'raid-host.example\\n'\n",
    )

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_finalize"],
        check=False,
        env={
            **os.environ,
            "APP_PORT": "18123",
            "ENV_FILE": str(tmp_path / "env"),
            "INSTALL_PREFIX": str(tmp_path / "prefix"),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert curl_attempts.read_text() == "3\n"
    assert sleeps.read_text() == "1\n1\n"
    assert 'healthz: {"status":"ok"}' in result.stdout


def test_phase_systemd_renders_unit_with_installed_paths_and_app_port(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    unit_template = prefix / "src" / "deploy" / "megaraid-dashboard.service"
    unit_template.parent.mkdir(parents=True)
    unit_template.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
    preflight_source = prefix / "src" / "scripts" / "preflight.sh"
    preflight_source.parent.mkdir(parents=True)
    preflight_source.write_text("#!/bin/sh\necho tampered preflight\n", encoding="utf-8")
    uninstall_source = prefix / "src" / "scripts" / "uninstall.sh"
    uninstall_source.write_text("#!/bin/sh\necho tampered uninstall\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands.log"
    installed_unit = tmp_path / "megaraid-dashboard.service"
    _write_executable(
        bin_dir / "install",
        "#!/bin/sh\n"
        f"printf 'install %s\\n' \"$*\" >> {log}\n"
        "make_dir=false\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  case "$1" in\n'
        "    -d) make_dir=true; shift ;;\n"
        "    -m|-o|-g) shift 2 ;;\n"
        "    *) break ;;\n"
        "  esac\n"
        "done\n"
        'if [ "$make_dir" = true ]; then\n'
        '  mkdir -p "$1"\n'
        'elif [ "$2" = /etc/systemd/system/megaraid-dashboard.service ]; then\n'
        f'  cp "$1" {installed_unit}\n'
        "else\n"
        '  cp "$1" "$2"\n'
        "fi\n",
    )
    _write_executable(
        bin_dir / "systemctl",
        f"#!/bin/sh\nprintf 'systemctl %s\\n' \"$*\" >> {log}\n",
    )

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_systemd"],
        check=False,
        env={
            **os.environ,
            "APP_PORT": "18123",
            "DATA_DIR": str(tmp_path / "data"),
            "ENV_FILE": str(tmp_path / "etc" / "env"),
            "INSTALL_PREFIX": str(prefix),
            "INSTALL_USER": "raid-special",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr

    unit = installed_unit.read_text()
    assert "User=raid-special" in unit
    assert "Group=raid-special" in unit
    assert f"EnvironmentFile={tmp_path / 'etc' / 'env'}" in unit
    assert f"ExecStartPre={prefix}/scripts/preflight.sh" in unit
    assert f"ReadWritePaths={tmp_path / 'data'}" in unit
    assert "--port 18123" in unit
    assert "scripts/preflight.sh" in unit
    assert (prefix / "scripts" / "preflight.sh").read_text() == (
        REPO_ROOT / "scripts" / "preflight.sh"
    ).read_text()
    assert (prefix / "scripts" / "uninstall.sh").read_text() == (
        REPO_ROOT / "scripts" / "uninstall.sh"
    ).read_text()
    assert "tampered" not in unit
    assert "systemctl daemon-reload" in log.read_text()


def test_phase_finalize_points_uninstall_to_root_owned_script_copy(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "systemctl",
        "#!/bin/sh\n"
        'if [ "$1" = "is-active" ] && [ "$2" != "--quiet" ]; then\n'
        "  printf 'active\\n'\n"
        "fi\n",
    )
    _write_executable(bin_dir / "curl", "#!/bin/sh\nprintf 'ok\\n'\n")
    _write_executable(bin_dir / "hostname", "#!/bin/sh\nprintf 'raid-host.example\\n'\n")

    prefix = tmp_path / "prefix"
    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_finalize"],
        check=False,
        env={
            **os.environ,
            "APP_PORT": "18123",
            "ENV_FILE": str(tmp_path / "env"),
            "INSTALL_PREFIX": str(prefix),
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert f"Uninstall: sudo bash {prefix}/scripts/uninstall.sh" in result.stdout
    assert f"{prefix}/src/scripts/uninstall.sh" not in result.stdout


def test_uninstall_removes_configured_sudoers_file() -> None:
    script = UNINSTALL_SCRIPT.read_text()

    assert 'SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/megaraid-dashboard}"' in script
    assert 'rm -f "${SUDOERS_FILE}"' in script
    assert "rm -f /etc/sudoers.d/megaraid-dashboard" not in script


def _run_phase_sudoers_with_stat(
    tmp_path: Path,
    *,
    storcli_stat_output: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    storcli = tmp_path / "usr" / "local" / "sbin" / "storcli64"
    storcli.parent.mkdir(parents=True)
    _write_executable(storcli, "#!/bin/sh\nexit 0\n")
    sudoers = tmp_path / "sudoers.d" / "megaraid-dashboard"
    _write_executable(
        bin_dir / "stat",
        "#!/bin/sh\n"
        "last=\n"
        'for arg in "$@"; do last="$arg"; done\n'
        f'if [ "$last" = "{storcli}" ]; then\n'
        f"  printf '{storcli_stat_output}\\n'\n"
        "else\n"
        "  printf '0 -rwxr-xr-x\\n'\n"
        "fi\n",
    )
    _write_executable(bin_dir / "visudo", "#!/bin/sh\nexit 0\n")

    result = subprocess.run(
        ["bash", "-c", f"source {INSTALL_SCRIPT}; phase_sudoers"],
        check=False,
        env={
            **os.environ,
            "INSTALL_USER": "raid-monitor",
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STORCLI_PATH": str(storcli),
            "SUDOERS_FILE": str(sudoers),
        },
        text=True,
        capture_output=True,
    )

    return result, sudoers


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)
