#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check ."
python -m ruff check .

echo "==> ruff format --check ."
python -m ruff format --check .

echo "==> mypy src"
python -m mypy src

echo "==> bash syntax"
bash -n scripts/install.sh

if command -v shellcheck >/dev/null 2>&1; then
  echo "==> shellcheck scripts/install.sh"
  shellcheck scripts/install.sh
else
  echo "==> shellcheck not installed; skipping"
fi

echo "==> pytest"
python -m pytest

echo "==> ci.sh OK"
