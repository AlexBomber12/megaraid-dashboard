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

validate_install_user() {
  local passwd_entry
  passwd_entry="$(getent passwd "${INSTALL_USER}")" || log_fail "user ${INSTALL_USER} not found"

  local uid gid home shell
  IFS=: read -r _ _ uid gid _ home shell <<<"${passwd_entry}"

  local group_entry
  group_entry="$(getent group "${gid}")" || log_fail "primary group ${gid} for ${INSTALL_USER} not found"

  local group_name
  group_name="${group_entry%%:*}"

  [[ "${uid}" -lt 1000 ]] || log_fail "user ${INSTALL_USER} exists but is not a system user"
  [[ "${home}" == "${DATA_DIR}" ]] || \
    log_fail "user ${INSTALL_USER} home is ${home}, expected ${DATA_DIR}"
  [[ "${shell}" == "/usr/sbin/nologin" ]] || \
    log_fail "user ${INSTALL_USER} shell is ${shell}, expected /usr/sbin/nologin"
  [[ "${group_name}" == "${INSTALL_USER}" ]] || \
    log_fail "user ${INSTALL_USER} primary group is ${group_name}, expected ${INSTALL_USER}"
}

os_release_value() {
  local key="$1"
  local value

  value="$(awk -F= -v key="${key}" '$1 == key { print substr($0, index($0, "=") + 1); exit }' \
    "${OS_RELEASE_FILE}")"

  case "${value}" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac

  printf "%s\n" "${value}"
}

phase_preflight() {
  log_info "Phase 1: pre-flight"

  local os_id
  os_id="$(os_release_value ID)"
  if [[ "${os_id}" != "ubuntu" ]]; then
    log_fail "expected Ubuntu, got $(grep '^ID=' "${OS_RELEASE_FILE}" || echo unknown)"
  fi

  local os_version
  os_version="$(os_release_value VERSION_ID)"
  [[ "${os_version}" == "24.04" ]] || log_warn "expected Ubuntu 24.04, got ${os_version}"

  command_exists python3 || log_fail "python3 not found"

  local python_version
  python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  [[ "${python_version}" == "3.12" ]] || log_warn "expected Python 3.12, got ${python_version}"

  [[ -x "${STORCLI_PATH}" ]] || log_fail "storcli64 not found at ${STORCLI_PATH}"

  local sockets
  if ! sockets="$(ss -lnt 2>&1)"; then
    log_fail "ss port probe failed: ${sockets}"
  fi

  if printf "%s\n" "${sockets}" | awk '{print $4}' | grep -q ":${APP_PORT}\$"; then
    log_fail "port ${APP_PORT} already in use"
  fi

  log_info "pre-flight OK"
}

phase_user() {
  log_info "Phase 2: system user"

  if id -u "${INSTALL_USER}" >/dev/null 2>&1; then
    validate_install_user
    log_info "user ${INSTALL_USER} already exists and matches service account policy, skip"
  else
    useradd --system --user-group --home-dir "${DATA_DIR}" --no-create-home --shell /usr/sbin/nologin \
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

phase_venv() {
  log_info "Phase 4: venv"

  command_exists python3 || log_fail "python3 not found"

  local venv="${INSTALL_PREFIX}/.venv"
  if [[ ! -d "${venv}" ]]; then
    python3 -m venv "${venv}"
    chown -R "${INSTALL_USER}:${INSTALL_USER}" "${venv}"
    log_info "created venv at ${venv}"
  else
    log_info "venv exists, skip"
  fi

  sudo -u "${INSTALL_USER}" "${venv}/bin/pip" install --upgrade "pip>=24" >/dev/null
}

phase_pip() {
  log_info "Phase 5: pip install"

  command_exists curl || log_fail "curl not found"

  local src_dir="${INSTALL_PREFIX}/src"
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local repo_root
  repo_root="$(dirname "${script_dir}")"

  if ! curl -fsSI -o /dev/null https://pypi.org/simple/; then
    log_fail "pypi.org unreachable; cannot pip install"
  fi

  command_exists git || log_fail "git not found"
  command_exists tar || log_fail "tar not found"
  if [[ "$(git -C "${repo_root}" rev-parse --is-inside-work-tree)" != "true" ]]; then
    log_fail "${repo_root} is not a git work tree"
  fi

  install -d -m 0750 -o root -g "${INSTALL_USER}" "${INSTALL_PREFIX}"

  local staging_dir
  staging_dir="$(mktemp -d "${INSTALL_PREFIX}/src.staged.XXXXXXXX")"
  local backup_dir
  backup_dir=""

  if ! git -C "${repo_root}" archive --format=tar HEAD | tar -x -C "${staging_dir}"; then
    rm -rf -- "${staging_dir}"
    log_fail "failed to export source tree"
  fi
  if ! chown -R "${INSTALL_USER}:${INSTALL_USER}" "${staging_dir}"; then
    rm -rf -- "${staging_dir}"
    log_fail "failed to set source tree ownership"
  fi
  if ! chmod 0750 "${staging_dir}"; then
    rm -rf -- "${staging_dir}"
    log_fail "failed to set source tree permissions"
  fi

  if [[ -e "${src_dir}" ]]; then
    backup_dir="$(mktemp -d "${INSTALL_PREFIX}/src.previous.XXXXXXXX")"
    rmdir "${backup_dir}"
    mv "${src_dir}" "${backup_dir}"
  fi

  if ! mv "${staging_dir}" "${src_dir}"; then
    if [[ -n "${backup_dir}" ]]; then
      mv "${backup_dir}" "${src_dir}"
    fi
    rm -rf -- "${staging_dir}"
    log_fail "failed to promote staged source tree"
  fi

  if [[ -n "${backup_dir}" ]]; then
    rm -rf -- "${backup_dir}"
  fi

  sudo -u "${INSTALL_USER}" "${INSTALL_PREFIX}/.venv/bin/pip" install -e "${src_dir}"
  log_info "package installed"
}

phase_smoke() {
  log_info "Phase 6: smoke"

  local out
  out="$(sudo -u "${INSTALL_USER}" "${INSTALL_PREFIX}/.venv/bin/python" -c \
    "import megaraid_dashboard; print(megaraid_dashboard.__version__)")"
  log_info "imported megaraid_dashboard ${out}"
}

main() {
  require_root
  phase_preflight
  phase_user
  phase_dirs
  phase_venv
  phase_pip
  phase_smoke
  log_info "PR-029 phases complete; continue with config wizard via PR-030"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
