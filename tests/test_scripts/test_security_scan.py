from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = REPO_ROOT / "scripts" / "security-scan.sh"


def test_security_scan_fails_when_scanner_module_is_missing(tmp_path: Path) -> None:
    project = _copy_security_scan_project(tmp_path)
    python_bin = _write_fake_python(
        project,
        missing_modules={"pip_audit"},
        module_statuses={"ruff": 0, "pip_audit": 1, "bandit": 0},
    )

    result = _run_security_scan(project, python_bin)

    assert result.returncode == 127
    assert "pip-audit module 'pip_audit' is not installed" in result.stderr
    assert "pip-audit reported findings; continuing" not in result.stdout
    assert not (project / "bandit.ran").exists()


def test_security_scan_allows_known_findings_exit_after_module_preflight(tmp_path: Path) -> None:
    project = _copy_security_scan_project(tmp_path)
    python_bin = _write_fake_python(
        project,
        missing_modules=set(),
        module_statuses={"ruff": 0, "pip_audit": 1, "bandit": 1},
    )

    result = _run_security_scan(project, python_bin)

    assert result.returncode == 0
    assert "pip-audit reported findings; continuing" in result.stdout
    assert "bandit reported findings; continuing" in result.stdout
    assert (project / "pip_audit.ran").exists()
    assert (project / "bandit.ran").exists()


def _copy_security_scan_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    scripts_dir.mkdir(parents=True)
    (project / "src").mkdir()
    shutil.copy2(SCRIPT_SOURCE, scripts_dir / "security-scan.sh")
    return project


def _write_fake_python(
    project: Path,
    *,
    missing_modules: set[str],
    module_statuses: dict[str, int],
) -> Path:
    python_bin = project / "fake-python"
    missing_modules_literal = " ".join(sorted(missing_modules))
    statuses = "\n".join(
        f'  "{module}") touch "{module}.ran"; exit {status} ;;'
        for module, status in sorted(module_statuses.items())
    )
    python_bin.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail

if [[ "${{1:-}}" == "-c" ]]; then
  code="${{2:-}}"
  for module in {missing_modules_literal}; do
    if [[ "$code" == *"find_spec('${{module}}')"* ]]; then
      exit 1
    fi
  done
  exit 0
fi

if [[ "${{1:-}}" == "-m" ]]; then
  case "${{2:-}}" in
{statuses}
    *) exit 2 ;;
  esac
fi

exit 2
""",
        encoding="utf-8",
    )
    python_bin.chmod(python_bin.stat().st_mode | stat.S_IXUSR)
    return python_bin


def _run_security_scan(project: Path, python_bin: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(project / "scripts" / "security-scan.sh")],
        cwd=project,
        env={**os.environ, "PYTHON_BIN": str(python_bin)},
        text=True,
        capture_output=True,
    )
