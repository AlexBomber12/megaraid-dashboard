#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/alembic ]]; then
  echo "ERROR: .venv/bin/alembic not found or not executable" >&2
  exit 127
fi

if [[ ! -x .venv/bin/python ]]; then
  echo "ERROR: .venv/bin/python not found or not executable" >&2
  exit 127
fi

echo "==> alembic upgrade head"
.venv/bin/alembic upgrade head

echo "==> DB writability probe"
.venv/bin/python - <<'PY'
from __future__ import annotations

import os
import sqlite3
import stat
import sys
from urllib.parse import unquote, urlparse


def sqlite_path_from_url(url: str) -> str:
    parsed = urlparse(url)
    raw_path = unquote(parsed.path)
    if raw_path in ("", "/"):
        return parsed.netloc
    if raw_path.startswith("/./") or raw_path.startswith("/../"):
        return raw_path[1:]
    if raw_path.startswith("//"):
        return raw_path[1:]
    return raw_path.lstrip("/")


url = os.environ.get("DATABASE_URL", "sqlite:///./megaraid.db")
if not url.startswith("sqlite:"):
    sys.exit(0)

path = sqlite_path_from_url(url)
if path and path != ":memory:" and os.path.exists(path):
    mode = os.stat(path).st_mode
    if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        print(f"ERROR: SQLite database is not writable: {path}", file=sys.stderr)
        sys.exit(1)

conn = sqlite3.connect(path, timeout=2)
try:
    conn.execute("CREATE TABLE IF NOT EXISTS _preflight (n INT)")
    conn.execute("INSERT INTO _preflight VALUES (1)")
    conn.execute("DROP TABLE _preflight")
    conn.commit()
finally:
    conn.close()

print("DB writable")
PY

echo "==> preflight OK"
