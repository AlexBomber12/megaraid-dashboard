from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = REPO_ROOT / "scripts" / "preflight.sh"


def test_preflight_fails_clearly_without_venv(tmp_path: Path) -> None:
    project = _copy_preflight_project(tmp_path)

    result = _run_preflight(project, database_url="sqlite:///./tmp_preflight.db")

    assert result.returncode == 127
    assert ".venv/bin/alembic not found or not executable" in result.stderr


def test_preflight_succeeds_with_stubbed_alembic(tmp_path: Path) -> None:
    project = _copy_preflight_project(tmp_path)
    _install_stub_venv(project)

    result = _run_preflight(project, database_url="sqlite:///./tmp_preflight.db")

    assert result.returncode == 0
    assert "stub alembic upgrade head" in result.stdout
    assert "DB writable" in result.stdout
    assert "preflight OK" in result.stdout
    assert _table_names(project / "tmp_preflight.db") == []


def test_preflight_preserves_existing_preflight_table(tmp_path: Path) -> None:
    project = _copy_preflight_project(tmp_path)
    _install_stub_venv(project)
    db_path = project / "tmp_preflight.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE _preflight (n INT)")
        conn.execute("INSERT INTO _preflight VALUES (42)")
        conn.commit()
    finally:
        conn.close()

    result = _run_preflight(project, database_url="sqlite:///./tmp_preflight.db")

    assert result.returncode == 0
    assert _table_names(db_path) == ["_preflight"]
    assert _table_rows(db_path, "_preflight") == [(42,)]


def test_preflight_fails_for_read_only_sqlite_db(tmp_path: Path) -> None:
    project = _copy_preflight_project(tmp_path)
    _install_stub_venv(project)
    db_path = project / "tmp_preflight.db"
    sqlite3.connect(db_path).close()
    db_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    try:
        result = _run_preflight(project, database_url="sqlite:///./tmp_preflight.db")
    finally:
        db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert result.returncode == 1
    assert "SQLite database is not writable" in result.stderr


def test_preflight_fails_for_read_only_sqlite_db_with_driver_suffix(tmp_path: Path) -> None:
    project = _copy_preflight_project(tmp_path)
    _install_stub_venv(project)
    db_path = project / "tmp_preflight.db"
    sqlite3.connect(db_path).close()
    db_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    try:
        result = _run_preflight(project, database_url="sqlite+pysqlite:///./tmp_preflight.db")
    finally:
        db_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    assert result.returncode == 1
    assert "SQLite database is not writable" in result.stderr


def _copy_preflight_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    scripts_dir = project / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SOURCE, scripts_dir / "preflight.sh")
    return project


def _install_stub_venv(project: Path) -> None:
    bin_dir = project / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    python_link = bin_dir / "python"
    python_link.symlink_to(sys.executable)
    alembic = bin_dir / "alembic"
    alembic.write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\necho "stub alembic $*"\n',
        encoding="utf-8",
    )
    alembic.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)


def _run_preflight(project: Path, *, database_url: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        ["bash", "scripts/preflight.sh"],
        cwd=project,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _table_names(db_path: Path) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def _table_rows(db_path: Path, table_name: str) -> list[tuple[int]]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(f'SELECT n FROM "{table_name}"').fetchall()
    finally:
        conn.close()
    return [(int(row[0]),) for row in rows]
