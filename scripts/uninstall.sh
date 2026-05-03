#!/usr/bin/env bash
# Conservative MegaRAID Dashboard uninstaller.
set -euo pipefail

PURGE="false"

for arg in "$@"; do
  case "${arg}" in
    --purge) PURGE="true" ;;
    *) echo "unknown argument: ${arg}" >&2; exit 1 ;;
  esac
done

INSTALL_USER="${INSTALL_USER:-raid-monitor}"
INSTALL_PREFIX="${INSTALL_PREFIX:-/opt/megaraid-dashboard}"
DATA_DIR="${DATA_DIR:-/var/lib/megaraid-dashboard}"
ETC_DIR="${ETC_DIR:-/etc/megaraid-dashboard}"

[[ ${EUID} -eq 0 ]] || { echo "must run as root" >&2; exit 1; }

systemctl stop megaraid-dashboard.service 2>/dev/null || true
systemctl disable megaraid-dashboard.service 2>/dev/null || true
rm -f /etc/systemd/system/megaraid-dashboard.service
rm -f /etc/sudoers.d/megaraid-dashboard
rm -f /etc/systemd/journald@megaraid-dashboard.conf.d/00-retention.conf
systemctl daemon-reload

if [[ "${PURGE}" == "true" ]]; then
  rm -rf "${INSTALL_PREFIX}" "${DATA_DIR}" "${ETC_DIR}"
  userdel "${INSTALL_USER}" 2>/dev/null || true
  echo "purged"
else
  echo "service removed; data and config preserved at ${DATA_DIR} and ${ETC_DIR}"
fi
