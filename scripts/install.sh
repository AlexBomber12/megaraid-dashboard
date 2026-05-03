#!/usr/bin/env bash
# MegaRAID Dashboard installer.
# Usage: sudo bash scripts/install.sh [--non-interactive] [--force-reconfigure]
set -euo pipefail

INSTALL_USER="${INSTALL_USER:-raid-monitor}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/megaraid-dashboard}"
DATA_DIR="${DATA_DIR:-/var/lib/megaraid-dashboard}"
ETC_DIR="${ETC_DIR:-/etc/megaraid-dashboard}"
ENV_FILE="${ENV_FILE:-${ETC_DIR}/env}"
STORCLI_PATH="${STORCLI_PATH:-/usr/local/sbin/storcli64}"
OS_RELEASE_FILE="${OS_RELEASE_FILE:-/etc/os-release}"
APP_PORT="${APP_PORT:-8090}"
SUDOERS_FILE="${SUDOERS_FILE:-/etc/sudoers.d/megaraid-dashboard}"
NON_INTERACTIVE="false"
FORCE_RECONFIG="false"
MISSING_CONFIG_VARS=()

log_info() { printf "\033[1;34m[info]\033[0m %s\n" "$*"; }
log_warn() { printf "\033[1;33m[warn]\033[0m %s\n" "$*" >&2; }
log_fail() {
  printf "\033[1;31m[fail]\033[0m %s\n" "$*" >&2
  exit 1
}

sed_replacement_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//&/\\&}"
  value="${value//|/\\|}"
  printf "%s\n" "${value}"
}

require_root() {
  [[ ${EUID} -eq 0 ]] || log_fail "must run as root"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

parse_args() {
  for arg in "$@"; do
    case "${arg}" in
      --non-interactive) NON_INTERACTIVE="true" ;;
      --force-reconfigure) FORCE_RECONFIG="true" ;;
      *) log_fail "unknown argument: ${arg}" ;;
    esac
  done
}

require_pypi_reachable() {
  command_exists curl || log_fail "curl not found"

  if ! curl -fsSI -o /dev/null https://pypi.org/simple/; then
    log_fail "pypi.org unreachable; cannot pip install"
  fi
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

validate_root_owned_not_writable() {
  local path="$1"
  local owner perms

  read -r owner perms < <(stat -Lc "%u %A" -- "${path}") || \
    log_fail "failed to stat ${path}"

  [[ "${owner}" == "0" ]] || log_fail "${path} must be owned by root before sudoers grant"
  [[ "${perms:5:1}" != "w" && "${perms:8:1}" != "w" ]] || \
    log_fail "${path} must not be writable by group or other before sudoers grant"
}

validate_storcli_sudo_target() {
  [[ "${STORCLI_PATH}" = /* ]] || log_fail "storcli path must be absolute: ${STORCLI_PATH}"
  [[ -e "${STORCLI_PATH}" ]] || log_fail "storcli64 not found at ${STORCLI_PATH}"
  [[ -f "${STORCLI_PATH}" ]] || log_fail "storcli path is not a regular file: ${STORCLI_PATH}"
  [[ -x "${STORCLI_PATH}" ]] || log_fail "storcli64 is not executable at ${STORCLI_PATH}"
  [[ ! -L "${STORCLI_PATH}" ]] || log_fail "storcli path must not be a symlink: ${STORCLI_PATH}"

  validate_root_owned_not_writable "${STORCLI_PATH}"

  local parent
  parent="$(dirname "${STORCLI_PATH}")"
  while [[ "${parent}" != "/" ]]; do
    validate_root_owned_not_writable "${parent}"
    parent="$(dirname "${parent}")"
  done
  validate_root_owned_not_writable "/"
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

  require_pypi_reachable
  sudo -u "${INSTALL_USER}" "${venv}/bin/pip" install --upgrade "pip>=24" >/dev/null
}

phase_pip() {
  log_info "Phase 5: pip install"

  require_pypi_reachable

  local src_dir="${INSTALL_PREFIX}/src"
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local repo_root
  repo_root="$(dirname "${script_dir}")"

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

  if ! (
    cd "${repo_root}"
    git ls-files -z --cached --others --exclude-standard |
      while IFS= read -r -d '' path; do
        [[ -e "${path}" ]] && printf '%s\0' "${path}"
      done |
      tar --null --files-from=- --create --file=- |
      tar -x -C "${staging_dir}"
  ); then
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

  if ! sudo -u "${INSTALL_USER}" "${INSTALL_PREFIX}/.venv/bin/pip" install -e "${src_dir}"; then
    rm -rf -- "${src_dir}"
    if [[ -n "${backup_dir}" ]]; then
      mv "${backup_dir}" "${src_dir}"
    fi
    log_fail "pip install failed; restored previous source tree"
  fi

  if [[ -n "${backup_dir}" ]]; then
    rm -rf -- "${backup_dir}"
  fi

  log_info "package installed"
}

phase_smoke() {
  log_info "Phase 6: smoke"

  local out
  out="$(sudo -u "${INSTALL_USER}" "${INSTALL_PREFIX}/.venv/bin/python" -c \
    "import megaraid_dashboard; print(megaraid_dashboard.__version__)")"
  log_info "imported megaraid_dashboard ${out}"
}

env_file_value() {
  local var="$1"

  awk -F= -v key="${var}" '$1 == key { print substr($0, index($0, "=") + 1); exit }' \
    "${ENV_FILE}" 2>/dev/null || true
}

prompt_config_value() {
  local var="$1"
  local msg="$2"
  local default="${3:-}"
  local secret="${4:-}"
  local current
  current="$(env_file_value "${var}")"

  if [[ -n "${current}" && "${FORCE_RECONFIG}" != "true" ]]; then
    log_info "${var} already set, keep"
    printf -v "${var}" "%s" "${current}"
    return
  fi

  local env_var="MEGARAID_INSTALL_${var}"
  if [[ -n "${!env_var:-}" ]]; then
    printf -v "${var}" "%s" "${!env_var}"
    return
  fi

  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    if [[ -n "${default}" ]]; then
      printf -v "${var}" "%s" "${default}"
    else
      MISSING_CONFIG_VARS+=("${env_var}")
      printf -v "${var}" "%s" ""
    fi
    return
  fi

  local input
  if [[ -n "${secret}" ]]; then
    read -r -s -p "${msg}: " input
    echo
  else
    read -r -p "${msg}${default:+ [${default}]}: " input
  fi
  [[ -n "${input}" ]] || input="${default}"
  [[ -n "${input}" ]] || log_fail "${var} required"
  printf -v "${var}" "%s" "${input}"
}

managed_config_keys() {
  cat <<EOF
ADMIN_USERNAME
ADMIN_PASSWORD_HASH
ALERT_SMTP_HOST
ALERT_SMTP_PORT
ALERT_SMTP_USER
ALERT_SMTP_PASSWORD
ALERT_SMTP_USE_STARTTLS
ALERT_FROM
ALERT_TO
STORCLI_PATH
STORCLI_USE_SUDO
LOG_LEVEL
METRICS_INTERVAL_SECONDS
DATABASE_URL
EOF
}

write_preserved_env_lines() {
  local target="$1"
  local managed_keys
  managed_keys="$(managed_config_keys)"

  awk -F= -v managed_keys="${managed_keys}" '
    BEGIN {
      split(managed_keys, keys, "\n")
      for (idx in keys) {
        managed[keys[idx]] = 1
      }
    }
    $1 in managed { next }
    { print }
  ' "${ENV_FILE}" >"${target}"
}

bcrypt_hash() {
  local plain="$1"

  "${INSTALL_PREFIX}/.venv/bin/python" -c \
    'import bcrypt, sys; print(bcrypt.hashpw(sys.argv[1].encode(), bcrypt.gensalt()).decode())' \
    "${plain}"
}

phase_config() {
  log_info "Phase 7: config wizard"
  MISSING_CONFIG_VARS=()

  local ADMIN_USERNAME ADMIN_PASSWORD ALERT_SMTP_HOST ALERT_SMTP_PORT ALERT_SMTP_USER
  local ALERT_SMTP_PASSWORD ALERT_FROM ALERT_TO LOG_LEVEL METRICS_INTERVAL_SECONDS
  local ADMIN_PASSWORD_HASH
  local preserve_existing_hash="false"

  ADMIN_PASSWORD_HASH="$(env_file_value ADMIN_PASSWORD_HASH)"
  if [[ -n "${ADMIN_PASSWORD_HASH}" && "${FORCE_RECONFIG}" != "true" ]]; then
    log_info "ADMIN_PASSWORD_HASH already set, keep"
    preserve_existing_hash="true"
  else
    prompt_config_value ADMIN_PASSWORD "admin password" "" "secret"
  fi

  prompt_config_value ADMIN_USERNAME "admin username" "admin"
  prompt_config_value ALERT_SMTP_HOST "SMTP host"
  prompt_config_value ALERT_SMTP_PORT "SMTP port" "587"
  prompt_config_value ALERT_SMTP_USER "SMTP user"
  prompt_config_value ALERT_SMTP_PASSWORD "SMTP password / app token" "" "secret"
  prompt_config_value ALERT_FROM "from address"
  prompt_config_value ALERT_TO "to address"
  prompt_config_value STORCLI_PATH "storcli path" "${STORCLI_PATH}"
  prompt_config_value LOG_LEVEL "log level" "info"
  prompt_config_value METRICS_INTERVAL_SECONDS "collector interval seconds" "300"

  if [[ "${#MISSING_CONFIG_VARS[@]}" -gt 0 ]]; then
    log_fail "required config missing in non-interactive mode: ${MISSING_CONFIG_VARS[*]}"
  fi

  if [[ "${preserve_existing_hash}" != "true" ]]; then
    ADMIN_PASSWORD_HASH="$(bcrypt_hash "${ADMIN_PASSWORD}")"
  fi

  install -m 0600 -o root -g "${INSTALL_USER}" /dev/null "${ENV_FILE}.tmp"
  write_preserved_env_lines "${ENV_FILE}.tmp"
  cat >>"${ENV_FILE}.tmp" <<EOF
ADMIN_USERNAME=${ADMIN_USERNAME}
ADMIN_PASSWORD_HASH=${ADMIN_PASSWORD_HASH}
ALERT_SMTP_HOST=${ALERT_SMTP_HOST}
ALERT_SMTP_PORT=${ALERT_SMTP_PORT}
ALERT_SMTP_USER=${ALERT_SMTP_USER}
ALERT_SMTP_PASSWORD=${ALERT_SMTP_PASSWORD}
ALERT_SMTP_USE_STARTTLS=true
ALERT_FROM=${ALERT_FROM}
ALERT_TO=${ALERT_TO}
STORCLI_PATH=${STORCLI_PATH}
STORCLI_USE_SUDO=true
LOG_LEVEL=${LOG_LEVEL}
METRICS_INTERVAL_SECONDS=${METRICS_INTERVAL_SECONDS}
DATABASE_URL=sqlite:///${DATA_DIR}/megaraid.db
EOF
  chmod 0600 "${ENV_FILE}.tmp"
  chown "root:${INSTALL_USER}" "${ENV_FILE}.tmp"
  mv "${ENV_FILE}.tmp" "${ENV_FILE}"
  log_info "wrote ${ENV_FILE}"
}

phase_sudoers() {
  log_info "Phase 8: sudoers"

  local sudoers_dir sudoers_tmp
  sudoers_dir="$(dirname "${SUDOERS_FILE}")"
  sudoers_tmp="${SUDOERS_FILE}.tmp"

  validate_storcli_sudo_target

  install -d -m 0755 "${sudoers_dir}"
  cat >"${sudoers_tmp}" <<EOF
${INSTALL_USER} ALL=(root) NOPASSWD: ${STORCLI_PATH}
EOF
  chmod 0440 "${sudoers_tmp}"
  if visudo -c -f "${sudoers_tmp}" >/dev/null 2>&1; then
    mv "${sudoers_tmp}" "${SUDOERS_FILE}"
    log_info "wrote ${SUDOERS_FILE}"
  else
    rm -f "${sudoers_tmp}"
    log_fail "sudoers fragment failed visudo check"
  fi
}

phase_systemd() {
  log_info "Phase 9: systemd unit"

  local unit_template unit_tmp
  unit_template="${INSTALL_PREFIX}/src/deploy/megaraid-dashboard.service"
  unit_tmp="$(mktemp /tmp/megaraid-dashboard.service.XXXXXXXX)"

  install -d -m 0750 -o root -g "${INSTALL_USER}" "${INSTALL_PREFIX}/scripts"
  install -m 0750 -o root -g "${INSTALL_USER}" \
    "${INSTALL_PREFIX}/src/scripts/preflight.sh" \
    "${INSTALL_PREFIX}/scripts/preflight.sh"

  if ! sed \
    -e "s|User=raid-monitor|User=$(sed_replacement_escape "${INSTALL_USER}")|" \
    -e "s|Group=raid-monitor|Group=$(sed_replacement_escape "${INSTALL_USER}")|" \
    -e "s|/opt/megaraid-dashboard|$(sed_replacement_escape "${INSTALL_PREFIX}")|g" \
    -e "s|/var/lib/megaraid-dashboard|$(sed_replacement_escape "${DATA_DIR}")|g" \
    -e "s|/etc/megaraid-dashboard/env|$(sed_replacement_escape "${ENV_FILE}")|g" \
    -e "s|--port 8090|--port $(sed_replacement_escape "${APP_PORT}")|" \
    "${unit_template}" >"${unit_tmp}"; then
    rm -f "${unit_tmp}"
    log_fail "failed to render systemd unit"
  fi

  if ! install -m 0644 -o root -g root "${unit_tmp}" /etc/systemd/system/megaraid-dashboard.service; then
    rm -f "${unit_tmp}"
    log_fail "failed to install systemd unit"
  fi
  rm -f "${unit_tmp}"
  systemctl daemon-reload
  systemctl enable megaraid-dashboard.service
  systemctl start megaraid-dashboard.service
}

phase_journald() {
  log_info "Phase 10: journald drop-in"

  install -d -m 0755 /etc/systemd/journald@megaraid-dashboard.conf.d
  install -m 0644 -o root -g root \
    "${INSTALL_PREFIX}/src/deploy/journald-megaraid.conf" \
    /etc/systemd/journald@megaraid-dashboard.conf.d/00-retention.conf
  systemctl restart "systemd-journald@megaraid-dashboard.service" 2>/dev/null || true
}

phase_finalize() {
  log_info "Phase 11: start + smoke"

  systemctl restart megaraid-dashboard.service

  for ((attempt = 1; attempt <= 30; attempt++)); do
    if systemctl is-active --quiet megaraid-dashboard.service; then
      break
    fi
    if [[ "${attempt}" -lt 30 ]]; then
      sleep 1
    fi
  done
  systemctl is-active --quiet megaraid-dashboard.service || \
    log_fail "service failed to become active"

  local healthz
  healthz=""
  for ((attempt = 1; attempt <= 30; attempt++)); do
    healthz="$(curl -fs "http://127.0.0.1:${APP_PORT}/healthz" || true)"
    if [[ -n "${healthz}" ]]; then
      break
    fi
    if [[ "${attempt}" -lt 30 ]]; then
      sleep 1
    fi
  done
  if [[ -z "${healthz}" ]]; then
    log_fail "healthz did not respond"
  fi
  log_info "healthz: ${healthz}"

  cat <<SUMMARY

==============================
INSTALL COMPLETE
==============================
Service:   megaraid-dashboard.service
Status:    $(systemctl is-active megaraid-dashboard.service)
URL:       http://$(hostname -f):${APP_PORT}/  (basic auth)
Healthz:   http://$(hostname -f):${APP_PORT}/healthz
Logs:      journalctl --namespace=megaraid-dashboard -f
Config:    ${ENV_FILE}
Reload:    sudo systemctl restart megaraid-dashboard.service
Uninstall: sudo bash ${INSTALL_PREFIX}/src/scripts/uninstall.sh

Reverse proxy: see ${INSTALL_PREFIX}/src/docs/PROXY-SETUP.md
==============================
SUMMARY
}

main() {
  parse_args "$@"
  require_root
  phase_preflight
  phase_user
  phase_dirs
  phase_venv
  phase_pip
  phase_smoke
  phase_config
  phase_sudoers
  phase_systemd
  phase_journald
  phase_finalize
  log_info "install phases complete"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
