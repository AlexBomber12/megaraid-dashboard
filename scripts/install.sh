#!/usr/bin/env bash
# MegaRAID Dashboard installer.
# Usage: sudo bash scripts/install.sh [--non-interactive]
set -euo pipefail

INSTALL_USER="${INSTALL_USER:-raid-monitor}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/megaraid-dashboard}"
DATA_DIR="${DATA_DIR:-/var/lib/megaraid-dashboard}"
ETC_DIR="${ETC_DIR:-/etc/megaraid-dashboard}"
ENV_FILE="${ENV_FILE:-${ETC_DIR}/env}"
STORCLI_PATH="${STORCLI_PATH:-/usr/local/sbin/storcli64}"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
APP_PORT="${APP_PORT:-8090}"

log_info() { printf "\033[1;34m[info]\033[0m %s\n" "$*"; }
log_warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
log_fail() {
  printf "\033[1;31m[fail]\033[0m %s\n" "$*" >&2
  exit 1
}

require_root() {
  [[ ${EUID} -eq 0 ]] || log_fail "must run as root"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

phase_preflight() {
  log_info "Phase 1: pre-flight"

  if ! grep -q '^ID=ubuntu' "${OS_RELEASE_FILE}"; then
    log_fail "expected Ubuntu, got $(grep '^ID=' "${OS_RELEASE_FILE}" || echo unknown)"
  fi

  local os_version
  os_version="$(grep '^VERSION_ID=' "${OS_RELEASE_FILE}" | cut -d'"' -f2)"
  [[ "${os_version}" == "24.04" ]] || log_warn "expected Ubuntu 24.04, got ${os_version}"

  command_exists python3 || log_fail "python3 not found"

  local python_version
  python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  [[ "${python_version}" == "3.12" ]] || log_warn "expected Python 3.12, got ${python_version}"

  [[ -x "${STORCLI_PATH}" ]] || log_fail "storcli64 not found at ${STORCLI_PATH}"

  if ss -lnt | awk '{print $4}' | grep -q ":${APP_PORT}\$"; then
    log_fail "port ${APP_PORT} already in use"
  fi

  log_info "pre-flight OK"
}

phase_user() {
  log_info "Phase 2: system user"

  if id -u "${INSTALL_USER}" >/dev/null 2>&1; then
    log_info "user ${INSTALL_USER} already exists, skip"
  else
    useradd --system --home-dir "${DATA_DIR}" --no-create-home --shell /usr/sbin/nologin \
      "${INSTALL_USER}"
    log_info "created user ${INSTALL_USER}"
  fi
}

phase_dirs() {
  log_info "Phase 3: directories"

  install -d -m 0750 -o root -g "${INSTALL_USER}" "${INSTALL_PREFIX}"
  install -d -m 0750 -o "${INSTALL_USER}" -g "${INSTALL_USER}" "${DATA_DIR}"
  install -d -m 0750 -o root -g "${INSTALL_USER}" "${ETC_DIR}"

  if [[ ! -f "${ENV_FILE}" ]]; then
    install -m 0600 -o root -g "${INSTALL_USER}" /dev/null "${ENV_FILE}"
    log_info "created ${ENV_FILE} (empty placeholder)"
  else
    log_info "${ENV_FILE} exists, skip"
  fi
}

main() {
  require_root
  phase_preflight
  phase_user
  phase_dirs
  log_info "PR-028 phases complete; continue with venv / pip / config / systemd via PR-029..PR-031"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
