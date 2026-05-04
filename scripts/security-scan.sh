#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python}"

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

require_python_module() {
  local label="$1"
  local module="$2"

  if "$PYTHON_BIN" -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('${module}') is None)"; then
    return 0
  fi

  echo "ERROR: ${label} module '${module}' is not installed; scanner did not run" >&2
  return 127
}

run_python_module_scanner() {
  local label="$1"
  local findings_exit="$2"
  local module="$3"
  shift 3

  require_python_module "$label" "$module"
  run_scanner "$label" "$findings_exit" "$PYTHON_BIN" -m "$module" "$@"
}

echo "==> ruff (security rules)"
run_python_module_scanner "ruff security rules" 1 ruff check --select S --output-format concise src/

echo "==> pip-audit"
run_python_module_scanner "pip-audit" 1 pip_audit --skip-editable

echo "==> bandit (deeper SAST)"
run_python_module_scanner "bandit" 1 bandit -r src/ -ll

echo "==> file permissions on /etc/megaraid-dashboard/env"
stat -c "%a %U:%G %n" /etc/megaraid-dashboard/env 2>/dev/null || echo "env file not present (dev environment)"

echo "==> done"
