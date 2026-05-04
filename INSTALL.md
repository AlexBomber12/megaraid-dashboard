# Install MegaRAID Dashboard

This guide installs MegaRAID Dashboard on a fresh Ubuntu 24.04 host as the
`raid-monitor` service user. It assumes one local LSI MegaRAID controller, `storcli64` at
`/usr/local/sbin/storcli64`, and nginx in front of the FastAPI service after the local
service is healthy.

Follow the steps in order. The installer is intentionally opinionated: it creates the service
account, application directories, virtual environment, sudoers allowlist, systemd unit, and
journald retention drop-in for the standard single-host deployment.

If a command fails, stop and keep the terminal output. The troubleshooting index is
`TROUBLESHOOTING.md`; PR-058 will fill in the detailed failure playbooks.

## Pre-flight

Pre-flight proves that the host has the five prerequisites the installer depends on before it
changes anything.

Run these commands as the administrative user that will perform the install.

### 1. Ubuntu 24.04

This verifies the supported OS baseline.

```bash
. /etc/os-release
printf '%s %s\n' "$ID" "$VERSION_ID"
```

Expected output:

```text
ubuntu 24.04
```

If the host is not Ubuntu 24.04, stop. Older releases may not have the expected Python and
systemd behavior.

### 2. Python 3.12 and venv

This verifies the Python runtime used by the application virtual environment.

```bash
python3 --version
python3 -m venv --help >/dev/null && echo "venv ok"
```

Expected output:

```text
Python 3.12.x
venv ok
```

If `python3 -m venv` fails, install the OS package before continuing:

```bash
sudo apt update
sudo apt install python3-venv
```

### 3. storcli64 binary

This verifies the exact binary path that the service will call through sudo.

```bash
sudo test -x /usr/local/sbin/storcli64
sudo /usr/local/sbin/storcli64 /c0 show all J >/tmp/megaraid-dashboard-storcli.json
python3 -m json.tool /tmp/megaraid-dashboard-storcli.json >/dev/null
echo "storcli json ok"
rm -f /tmp/megaraid-dashboard-storcli.json
```

Expected output:

```text
storcli json ok
```

The command must use `J` JSON output. The dashboard does not parse textual `storcli` tables.

### 4. TCP port 8090 is free

This verifies the local FastAPI listen port is available.

```bash
if ss -lnt | awk '{print $4}' | grep -q ':8090$'; then
  echo "port 8090 in use"
else
  echo "port 8090 free"
fi
```

Expected output:

```text
port 8090 free
```

If another service already owns this port, move it or set `APP_PORT` when running the
installer.

### 5. Network access and SMTP credential

This verifies that the host can reach PyPI for installation and that you have the SMTP values
needed by the config wizard.

```bash
curl -fsSI -o /dev/null https://pypi.org/simple/ && echo "pypi ok"
printf 'SMTP host: %s\nSMTP user: %s\nSMTP token present: %s\n' \
  "smtp.example.com" \
  "alerts@example.com" \
  "yes"
```

Expected output:

```text
pypi ok
SMTP host: smtp.example.com
SMTP user: alerts@example.com
SMTP token present: yes
```

Use your real SMTP host, user, and token during the installer prompts. Do not paste secrets
into shell history just to run this verification.

## Step 1: Clone Repository

This gets a clean copy of the project onto the host.

```bash
sudo apt update
sudo apt install git curl
git clone https://github.com/AlexBomber12/megaraid-dashboard.git
cd megaraid-dashboard
git status --short
```

Expected output:

```text
```

An empty `git status --short` output means the checkout is clean. The installer later copies
this checkout into `/opt/megaraid-dashboard/src`.

## Step 2: Run scripts/install.sh

This runs the production installer. It wraps the previous installer work: pre-flight checks,
service user and directory creation, virtual environment setup, package installation, config
wizard, sudoers allowlist, systemd unit, journald retention, start, and health smoke test.

```bash
sudo bash scripts/install.sh
```

Expected output includes the phase labels and final summary:

```text
[info] Phase 1: pre-flight
[info] Phase 2: system user
[info] Phase 3: directories
[info] Phase 4: venv
[info] Phase 5: pip install
[info] Phase 6: smoke
[info] Phase 7: config wizard
[info] Phase 8: sudoers
[info] Phase 9: systemd unit
[info] Phase 10: journald drop-in
[info] Phase 11: start + smoke
INSTALL COMPLETE
Service:   megaraid-dashboard.service
Status:    active
```

The config wizard asks for the admin password, admin username, SMTP host, SMTP port, SMTP
user, SMTP password or app token, sender address, recipient address, `storcli` path, log
level, and collector interval. Use these production defaults unless you have a reason to
override them:

```text
admin username: admin
SMTP port: 587
storcli path: /usr/local/sbin/storcli64
log level: info
collector interval seconds: 300
```

The installer writes configuration to `/etc/megaraid-dashboard/env`, creates the SQLite
database under `/var/lib/megaraid-dashboard/`, and installs the service code under
`/opt/megaraid-dashboard/`.

If a known failure appears, leave the partial install in place for inspection and use
`TROUBLESHOOTING.md` once PR-058 lands.

## Step 3: Edit .env If Config Wizard Skipped

This fills `/etc/megaraid-dashboard/env` manually when the wizard was skipped or needs a
correction.

```bash
sudo install -d -m 0750 -o root -g raid-monitor /etc/megaraid-dashboard
sudoedit /etc/megaraid-dashboard/env
```

Expected file contents:

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=$2b$12$replace-with-a-real-bcrypt-hash
ALERT_SMTP_HOST=smtp.example.com
ALERT_SMTP_PORT=587
ALERT_SMTP_USER=alerts@example.com
ALERT_SMTP_PASSWORD=replace-with-smtp-token
ALERT_SMTP_USE_STARTTLS=true
ALERT_FROM=alerts@example.com
ALERT_TO=operator@example.com
STORCLI_PATH=/usr/local/sbin/storcli64
STORCLI_USE_SUDO=true
LOG_LEVEL=info
METRICS_INTERVAL_SECONDS=300
DATABASE_URL=sqlite:////var/lib/megaraid-dashboard/megaraid.db
TRUSTED_PROXY_IPS=
```

Generate a bcrypt password hash with the installed virtual environment:

```bash
sudo /opt/megaraid-dashboard/.venv/bin/python -c 'import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass("Admin password: ").encode(), bcrypt.gensalt()).decode())'
```

Expected output:

```text
Admin password:
$2b$12$...
```

After editing, set ownership and permissions:

```bash
sudo chown root:raid-monitor /etc/megaraid-dashboard/env
sudo chmod 0600 /etc/megaraid-dashboard/env
sudo systemctl restart megaraid-dashboard.service
```

Expected output:

```text
```

Secrets belong only in `/etc/megaraid-dashboard/env` or the process environment. Do not commit
them to the repository.

## Step 4: Verify systemctl Status

This confirms systemd has started the dashboard service.

```bash
systemctl status megaraid-dashboard.service --no-pager
```

Expected output includes:

```text
Loaded: loaded (/etc/systemd/system/megaraid-dashboard.service; enabled
Active: active (running)
```

If the service is not active, inspect the unit log:

```bash
journalctl --namespace=megaraid-dashboard -u megaraid-dashboard.service -n 100 --no-pager
```

Expected output should show either normal startup logs or a direct configuration, database,
or `storcli` error to fix.

## Step 5: Verify Health Endpoint

This confirms the local application can answer the unauthenticated liveness route.

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"
curl -fsS "http://127.0.0.1:${APP_PORT:-8090}/healthz"
```

Expected output:

```json
{"status":"ok","database":"ok","collector":"ok"}
```

The `collector` value may be `idle` when collection is disabled or not yet running. The
install is healthy when `status` is `ok` and `database` is `ok`.

If the command returns HTTP 503, keep the JSON payload and use `TROUBLESHOOTING.md` once
PR-058 lands.

## Step 6: Configure nginx Reverse Proxy

This publishes the local service through nginx with TLS, `/raid/` path prefix handling,
security headers, and auth-entry rate limiting.

Follow the complete proxy guide:

```bash
less docs/PROXY-SETUP.md
```

Expected output:

```text
# Reverse Proxy Setup
```

Install the sample fragment from `docs/PROXY-SETUP.md` into nginx, then test and reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Expected output includes:

```text
nginx: the configuration file /etc/nginx/nginx.conf syntax is ok
nginx: configuration file /etc/nginx/nginx.conf test is successful
```

If nginx is on the same host as FastAPI and forwards client addresses, set the trusted proxy
list in `/etc/megaraid-dashboard/env`:

```dotenv
TRUSTED_PROXY_IPS=127.0.0.1
```

Then restart the app:

```bash
sudo systemctl restart megaraid-dashboard.service
```

Expected output:

```text
```

## Step 7: First Login, Password, and SMTP Test

This verifies the operator-facing web path and confirms alert email delivery.

Open the proxied dashboard URL in a browser:

```text
https://server.alexbomber.com/raid/
```

Expected result:

```text
The browser asks for HTTP Basic auth, then shows the MegaRAID Dashboard overview.
```

Log in with the `ADMIN_USERNAME` from `/etc/megaraid-dashboard/env` and the admin password
entered during the installer.

To change the admin password, generate a new hash and replace `ADMIN_PASSWORD_HASH`:

```bash
sudo /opt/megaraid-dashboard/.venv/bin/python -c 'import bcrypt,getpass; print(bcrypt.hashpw(getpass.getpass("New admin password: ").encode(), bcrypt.gensalt()).decode())'
sudoedit /etc/megaraid-dashboard/env
sudo systemctl restart megaraid-dashboard.service
```

Expected output from the hash command:

```text
New admin password:
$2b$12$...
```

Send one SMTP test message using the installed package and service environment:

```bash
sudo systemd-run --wait --pipe --collect \
  --uid=raid-monitor \
  --property=EnvironmentFile=/etc/megaraid-dashboard/env \
  /opt/megaraid-dashboard/.venv/bin/python -m megaraid_dashboard.alerts test
```

Expected output:

```text
Sent test alert to operator@example.com
```

The recipient should receive an email with subject:

```text
[megaraid-dashboard] SMTP test
```

If authentication, TLS, or connection errors appear, check the SMTP host, port, username,
token, and STARTTLS setting in `/etc/megaraid-dashboard/env`.

## Smoke Test Checklist

Run these ten checks after the local service and nginx proxy are configured. Each item has one
command and one expected result.

### 1. Service enabled

```bash
systemctl is-enabled megaraid-dashboard.service
```

Expected output:

```text
enabled
```

### 2. Service active

```bash
systemctl is-active megaraid-dashboard.service
```

Expected output:

```text
active
```

### 3. Health status ok

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"; curl -fsS "http://127.0.0.1:${APP_PORT:-8090}/healthz" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
```

Expected output:

```text
ok
```

### 4. Database check ok

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"; curl -fsS "http://127.0.0.1:${APP_PORT:-8090}/healthz" | python3 -c 'import json,sys; print(json.load(sys.stdin)["database"])'
```

Expected output:

```text
ok
```

### 5. Controller JSON readable through sudoers

```bash
sudo -u raid-monitor sudo /usr/local/sbin/storcli64 /c0 show all J >/tmp/c0.json && python3 -m json.tool /tmp/c0.json >/dev/null && echo ok; rm -f /tmp/c0.json
```

Expected output:

```text
ok
```

### 6. App database exists

```bash
sudo test -s /var/lib/megaraid-dashboard/megaraid.db && echo ok
```

Expected output:

```text
ok
```

### 7. Config file permissions

```bash
stat -c '%U:%G %a' /etc/megaraid-dashboard/env
```

Expected output:

```text
root:raid-monitor 600
```

### 8. Local overview requires auth

```bash
APP_PORT="$(systemctl show -p ExecStart --value megaraid-dashboard.service | sed -n 's/.*--port \([0-9][0-9]*\).*/\1/p')"; curl -s -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:${APP_PORT:-8090}/"
```

Expected output:

```text
401
```

### 9. Proxied health route responds

```bash
curl -fsS https://server.alexbomber.com/raid/healthz | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
```

Expected output:

```text
ok
```

### 10. SMTP test sends

```bash
sudo systemd-run --wait --pipe --collect --uid=raid-monitor --property=EnvironmentFile=/etc/megaraid-dashboard/env /opt/megaraid-dashboard/.venv/bin/python -m megaraid_dashboard.alerts test
```

Expected output:

```text
Sent test alert to operator@example.com
```

## Common Pitfalls

Use this section as a short index. The full diagnostic reference will live in
`TROUBLESHOOTING.md` after PR-058.

- `storcli64 not found at /usr/local/sbin/storcli64`: install Broadcom `storcli64` at the
  expected path or run the installer with `STORCLI_PATH=/absolute/path/to/storcli64`.
- `port 8090 already in use`: stop the conflicting service or run the installer with
  `APP_PORT=<free-port>`.
- `healthz did not return ok status`: inspect `journalctl --namespace=megaraid-dashboard -u
  megaraid-dashboard.service -n 100 --no-pager`.
- HTTP 401 on `/raid/`: use the `ADMIN_USERNAME` and admin password from the install wizard.
- HTTP 404 under `/raid/`: re-check the trailing slash on `proxy_pass
  http://127.0.0.1:8090/` and `X-Forwarded-Prefix /raid` in `docs/PROXY-SETUP.md`.
- SMTP authentication failed: verify `ALERT_SMTP_USER`, `ALERT_SMTP_PASSWORD`, and whether
  the provider requires an app password.
- SMTP connection failed: verify `ALERT_SMTP_HOST`, `ALERT_SMTP_PORT`, firewall egress, and
  `ALERT_SMTP_USE_STARTTLS`.
- Database permission errors: verify `/var/lib/megaraid-dashboard` is owned by
  `raid-monitor:raid-monitor` and the service uses
  `DATABASE_URL=sqlite:////var/lib/megaraid-dashboard/megaraid.db`.
- Missing journal namespace logs: verify the systemd unit includes
  `LogNamespace=megaraid-dashboard`.
- Alerts silent during maintenance: stop the maintenance window in the UI before testing
  alert delivery.
