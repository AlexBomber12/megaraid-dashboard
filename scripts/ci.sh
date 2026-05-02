#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> ruff check ."
python -m ruff check .

echo "==> ruff format --check ."
python -m ruff format --check .

echo "==> mypy src"
python -m mypy src

echo "==> pytest"
python -m pytest

echo "==> ci.sh OK"
