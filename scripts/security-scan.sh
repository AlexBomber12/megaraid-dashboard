#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

run_scanner() {
  local label="$1"
  local findings_exit="$2"
  shift 2

  set +e
  "$@"
  local status="$?"
  set -e

  if [[ "$status" -eq 0 ]]; then
    return 0
  fi

  if [[ "$status" -eq "$findings_exit" ]]; then
    echo "==> ${label} reported findings; continuing"
    return 0
  fi

  echo "ERROR: ${label} failed with exit status ${status}" >&2
  return "$status"
}

echo "==> ruff (security rules)"
run_scanner "ruff security rules" 1 .venv/bin/ruff check --select S --output-format concise src/

echo "==> pip-audit"
run_scanner "pip-audit" 1 .venv/bin/pip-audit --skip-editable

echo "==> bandit (deeper SAST)"
run_scanner "bandit" 1 .venv/bin/bandit -r src/ -ll

echo "==> file permissions on /etc/megaraid-dashboard/env"
stat -c "%a %U:%G %n" /etc/megaraid-dashboard/env 2>/dev/null || echo "env file not present (dev environment)"

echo "==> done"
