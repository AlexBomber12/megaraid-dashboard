#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> ruff (security rules)"
.venv/bin/ruff check --select S --output-format concise src/ || true

echo "==> pip-audit"
.venv/bin/pip-audit --skip-editable || true

echo "==> bandit (deeper SAST)"
.venv/bin/bandit -r src/ -ll || true

echo "==> file permissions on /etc/megaraid-dashboard/env"
stat -c "%a %U:%G %n" /etc/megaraid-dashboard/env 2>/dev/null || echo "env file not present (dev environment)"

echo "==> done"
