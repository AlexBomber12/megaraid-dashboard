#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check ."
python -m ruff check .

echo "==> ruff format --check ."
python -m ruff format --check .

echo "==> mypy src and operator tools"
python -m mypy src scripts/deploy_release.py scripts/locate_activity.py

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

echo "==> isolated operator safety integration"
python -m pytest -o "addopts=" tests/integration/ --no-cov

echo "==> ci.sh OK"
