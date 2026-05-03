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
import uuid

from sqlalchemy import make_url
from sqlalchemy.dialects.sqlite import pysqlite


def sqlite_connect_args_from_url(url: str) -> tuple[str, dict[str, object]]:
    args, kwargs = pysqlite.dialect().create_connect_args(make_url(url))
    if len(args) != 1:
        print("ERROR: unexpected SQLite connection arguments", file=sys.stderr)
        sys.exit(1)
    return str(args[0]), dict(kwargs)


def is_sqlite_url(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


url = os.environ.get("DATABASE_URL", "sqlite:///./megaraid.db")
if not is_sqlite_url(url):
    sys.exit(0)

path, connect_kwargs = sqlite_connect_args_from_url(url)
connect_kwargs.setdefault("timeout", 2)
if path and path != ":memory:" and not connect_kwargs.get("uri") and os.path.exists(path):
    mode = os.stat(path).st_mode
    if not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        print(f"ERROR: SQLite database is not writable: {path}", file=sys.stderr)
        sys.exit(1)

conn = sqlite3.connect(path, **connect_kwargs)
table_name = f"_megaraid_preflight_{os.getpid()}_{uuid.uuid4().hex}"
created = False
try:
    conn.execute(f'CREATE TABLE "{table_name}" (n INT)')
    created = True
    conn.execute(f'INSERT INTO "{table_name}" VALUES (1)')
    conn.execute(f'DROP TABLE "{table_name}"')
    created = False
    conn.commit()
finally:
    if created:
        try:
            conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            conn.commit()
        except sqlite3.Error:
            pass
    conn.close()

print("DB writable")
PY

echo "==> preflight OK"
