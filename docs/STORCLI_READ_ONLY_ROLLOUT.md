# Operator procedure: StorCLI 1.23.02 read-only rollout

This is a separate operator action after PR CI and current-head Codex review.
Nothing here was executed on production by the PR author. No controller writes,
LED effects, buzzer changes, rebuild initiation or reconstruction were physically
verified. Use an approved window and an operator root shell. Stop on any failed
step. Do not run the installer: its default account is raid-monitor, whereas this
host actually runs the service as **megaraid**.

## Recorded selections and recovery media

| Selection | Absolute path | SHA-256 |
| --- | --- | --- |
| Current 7.37 | `/usr/local/sbin/storcli64` | `dd2a9409ed37362bcfe0d9940f25537c61f564c95062cf280ef419b9bd5bb119` |
| Installed 1.23.02 source | `/usr/local/MegaRAID Storage Manager/StorCLI/storcli64` | `642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547` |
| New 1.23.02 copy | `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64` | Same as installed 1.23.02 source |

Both original binaries remain untouched, including ownership and permissions.
No executable or distribution package belongs in this public repository, CI
artifacts or PR attachments. Never download a substitute based on its filename.
A recorded local binary hash detects byte changes; **it is not a vendor signature
or proof of authenticity**. Verify any vendor signature/checksum independently if
available. Keep that provenance separate from the locally recorded hashes.

Before staging, locate the original distribution package from the operator's
existing installation media. Review its actual license for copying, retention,
backup and restoration rights; the repository's MIT license does not cover it.
Subject to those terms, preserve a private offline copy, its license, provenance,
original filename, and a locally computed SHA-256 manifest on separately protected
media. Record the package's *actual* hash; no package hash was supplied here and
the binary hash is not the package hash. Preserve private copies of both existing
binaries too if permitted. Do not proceed without a usable recovery source.

Recovery from media: mount/read the private media, run `sha256sum -c SHA256SUMS`
from its directory, compare the manifest with the separately retained trusted
record, and stop on mismatch. Extract the verified package privately without
executing its installer. Check the extracted executable against the recorded
1.23.02 binary hash before staging. Never overwrite either original binary as
part of this rollout. For 7.37 recovery use its independently preserved private
copy and its own recorded hash, not the legacy distribution. A missing or
license-restricted recovery source requires operator resolution, not an
unverified download.

## 1. Preserve and inspect the effective deployment

Set these shell variables to inspected, real deployment paths. Examples below
assume the existing unit is named megaraid-dashboard.service. If its name differs,
set `rollout_unit` accordingly. `rollout_release` is an unpacked, reviewed release
of this PR with its own installed virtualenv; staging it does not activate it.

```sh
set -eu
umask 077
rollout_unit=megaraid-dashboard.service
rollout_release=/opt/megaraid-dashboard-rollout
rollout_backup=/root/megaraid-rollout-backup
mkdir -m 0700 "$rollout_backup"  # Exclusive: stop if it already exists.
systemctl cat "$rollout_unit" > "$rollout_backup/unit.txt"
systemctl show "$rollout_unit" -p User -p Group -p WorkingDirectory -p ExecStart -p ExecStartPre -p EnvironmentFiles -p Environment -p NoNewPrivileges -p ProtectSystem -p ProtectHome -p PrivateTmp -p ReadOnlyPaths -p ReadWritePaths -p InaccessiblePaths -p SystemCallFilter -p CapabilityBoundingSet > "$rollout_backup/effective-unit.txt"
visudo -c
sudo -l -U megaraid > "$rollout_backup/sudo-before.txt"
sha256sum -- /usr/local/sbin/storcli64 '/usr/local/MegaRAID Storage Manager/StorCLI/storcli64' > "$rollout_backup/binaries-before.sha256"
```

Confirm User=megaraid and identify the actual effective environment files,
application directory/release pointer, unit fragment and drop-ins. Record their
absolute paths in the private change log. Save root-only `cp -a` copies of each
one, `/etc/sudoers` and `/etc/sudoers.d`, and a consistent SQLite backup using the
SQLite backup API (or the database's own backup tooling). Never copy a live WAL
SQLite file alone. Record the running application SHA. This PR has no schema
migration. Inspect `/proc/<MainPID>/environ` privately for selected keys only:
STORCLI_PATH, STORCLI_USE_SUDO, CONTROLLER_READ_ONLY, MAINTENANCE_MODE,
DESTRUCTIVE_MODE, DATABASE_URL. Do not dump credentials or post this file.

Resolve precedence: systemd Environment/EnvironmentFile and drop-ins can override
an application's `.env`; environment wins over pydantic's `.env`. Verify actual
WorkingDirectory/ExecStart selects the intended release and env. Retain all
unrelated environment values. Do not copy the repository unit over production.

**Systemd prerequisite:** this procedure uses narrow sudo elevation from megaraid.
If NoNewPrivileges=yes, nosuid mounts, capability filters, device restrictions or
other unit policy prevent it, STOP and obtain a separately reviewed service-policy
change. A shell probe cannot prove systemd access. Do not silently turn off
hardening or run FastAPI as root. The repo default unit has NoNewPrivileges=true;
it is not a working sudo deployment recipe. Do not change production service
policy as part of preparing this PR.

## 2. Stage the separate binary

From the reviewed release, run:

```sh
"$rollout_release/.venv/bin/python" "$rollout_release/scripts/storcli_rollout.py" stage
```

This has no StorCLI invocation. It verifies both recorded original hashes and
root ownership of every path component, rejects symlinks and group/other writable
components (including the source path), creates only missing safe staging parents,
and creates the target exclusively as root:root 0755. A pre-existing target is an
error, even if its hash matches. It then rechecks all three hashes. Resolve unsafe
original paths separately; the tool never repairs them. On failure, inspect any
partial new staging artifact and remove it explicitly before retrying; never
blindly overwrite it. Version is required from JSON in step 4, before activation.
Do not confuse safe file staging with successful hardware compatibility.

## 3. Install a complete exact read-only policy

Generate the policy for the new path into the private backup directory:

```sh
"$rollout_release/.venv/bin/python" "$rollout_release/scripts/storcli_rollout.py" policy > "$rollout_backup/reads.sudoers"
visudo -c -f "$rollout_backup/reads.sudoers"
test ! -e /etc/sudoers.d/megaraid-dashboard-rollout
test ! -L /etc/sudoers.d/megaraid-dashboard-rollout
install -o root -g root -m 0440 "$rollout_backup/reads.sudoers" /etc/sudoers.d/megaraid-dashboard-rollout
visudo -c
sudo -l -U megaraid > "$rollout_backup/sudo-after.txt"
```

Review the generated file before installing: 25 exact commands, all with trailing
`J`, no wildcard paths/arguments, no writes. It includes controller, virtual and
physical inventory, CV, BBU, foreign show, patrol show, both CC shows, and **show
all plus show rebuild for every /c0/e252/s0 through /c0/e252/s7**. These are the
application's full read surface. Never use ALL or arbitrary arguments.

Inspect the **complete effective policy**, including other included files,
group membership and directory/LDAP sudo rules. Do not leave a broader existing
rule that grants megaraid writes to either selection. Privately back up and
replace any existing StorCLI grant with the reviewed exact-read policy; retain
unrelated service permissions only after review. If a shared/global rule cannot
be safely narrowed, STOP for separate policy review. `visudo -c` validates syntax,
not least privilege; compare `sudo -l -U megaraid` against the generated commands.
Use `sudo -l -U megaraid -- <binary> <args>` for authorization-only checks of
individual reads and negative writes/extra arguments; **do not execute a write
as a permission test**. Revalidate `visudo -c` after every policy change. If full
validation fails, immediately restore the saved policy and validate it again.

The generator's `--binary /usr/local/sbin/storcli64` renders the same read-only
surface for 7.37 recovery if needed. Use only one generated alias at a time:
both policies deliberately use the same alias name.

## 4. Run reproducible preflight as megaraid

Prepare a private JSON inventory file from previously approved asset records,
not automatically from the candidate being checked:

```json
{
  "controller_serial": "ACTUAL_CONTROLLER_SERIAL",
  "drive_serials": {
    "252:0": "ACTUAL_SLOT_0_SERIAL", "252:1": "ACTUAL_SLOT_1_SERIAL",
    "252:2": "ACTUAL_SLOT_2_SERIAL", "252:3": "ACTUAL_SLOT_3_SERIAL",
    "252:4": "ACTUAL_SLOT_4_SERIAL", "252:5": "ACTUAL_SLOT_5_SERIAL",
    "252:6": "ACTUAL_SLOT_6_SERIAL", "252:7": "ACTUAL_SLOT_7_SERIAL"
  }
}
```

Replace every placeholder. Store a reviewed copy readable by megaraid at
`/var/lib/megaraid-dashboard/rollout-inventory.json` with root:megaraid 0640,
under a safe parent. Keep raw response evidence private. Run as the actual account:

```sh
sudo -u megaraid -- "$rollout_release/.venv/bin/python" "$rollout_release/scripts/storcli_rollout.py" probe --inventory /var/lib/megaraid-dashboard/rollout-inventory.json > "$rollout_backup/preflight.json"
sha256sum -c "$rollout_backup/binaries-before.sha256"
```

All actual invocations go through the application's allowlisted JSON wrapper
using `sudo -n`. A nonzero process, sudo rejection, timeout, malformed JSON,
missing/unknown command status, wrong controller, wrong CLI version, incomplete
inventory, changed serial or mismatched per-drive identity stops the preflight.
The candidate must report 1.23.02 (including padded 001.2302 forms), correct model,
approved controller/drive identities, and all 8 per-drive show-all and rebuild
reads. Inventory uses the same pydantic models and completeness checks as the
collector. CV must be present with parseable health. The known invalid optional
learn date remains unknown with a warning; it is not a required health failure.
Patrol and both CC commands are **required here**, even though the normal
collector tolerates their absence. Unknown states fail; active states are
reported accurately, never called idle/completed.

Only BBU and foreign show may return explicit `Un-supported command` or
`Unsupported command`; BBU's explicit `use /cx/cv` redirect is also accepted
because this procedure requires CV. Foreign `No foreign configuration` is a
validated absence, not an unsupported success. Other failures, missing statuses,
transport errors and malformed successful payloads remain fatal even for these
optional probes. Evidence retains optional unsupported results as such. Do not
call them healthy. Review CV health, patrol/CC states and all inventory evidence
before continuing; unhealthy hardware requires separate investigation, not writes.

## 5. Activate the read-only application selection

The following concrete activation uses an additive systemd drop-in and a small
last-loaded EnvironmentFile. It keeps the existing release in place. It assumes
step 1 confirmed the current command is uvicorn's factory entrypoint with
`--host 0.0.0.0 --port 8090`, and that moving WorkingDirectory preserves all
relative config/data paths (DATABASE_URL and lock paths must be absolute).
If those assumptions fail, STOP and adapt the reviewed procedure to the recorded
unit before the operator window; do not guess arguments or data locations.
The new release must be root-owned and not writable by megaraid. Its virtualenv
must contain the reviewed application wheel and dependencies, and have no
editable-install reference to the old checkout. Require all existing non-rollout
dependencies/config to resolve identically. No new `.env` may override missing
service settings. Confirm `/health` will report the reviewed application SHA.

From the same operator shell, create exclusive files (Bash noclobber is intentional):

```sh
rollout_env=/etc/megaraid-dashboard/storcli-read-only.env
rollout_dropin_dir="/etc/systemd/system/$rollout_unit.d"
rollout_dropin="$rollout_dropin_dir/90-storcli-read-only.conf"
test ! -e "$rollout_env" && test ! -L "$rollout_env"
test ! -e "$rollout_dropin" && test ! -L "$rollout_dropin"
# Inspect ancestors for root ownership, no symlinks, no group/other writes first.
install -d -o root -g root -m 0755 "$rollout_dropin_dir"
(set -C; cat > "$rollout_env" <<'ENV'
STORCLI_PATH=/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64
STORCLI_USE_SUDO=true
CONTROLLER_READ_ONLY=true
MAINTENANCE_MODE=false
DESTRUCTIVE_MODE=false
ENV
)
chmod 0600 "$rollout_env"
(set -C; cat > "$rollout_dropin" <<EOF
[Service]
WorkingDirectory=$rollout_release
EnvironmentFile=$rollout_env
ExecStart=
ExecStart=$rollout_release/.venv/bin/uvicorn megaraid_dashboard.app:create_app --factory --host 0.0.0.0 --port 8090
EOF
)
chmod 0644 "$rollout_dropin"
systemctl daemon-reload
systemctl cat "$rollout_unit" > "$rollout_backup/unit-staged.txt"
systemctl show "$rollout_unit" -p User -p WorkingDirectory -p ExecStart -p EnvironmentFiles -p NoNewPrivileges > "$rollout_backup/effective-staged.txt"
```

Review that the new environment file is last in the effective EnvironmentFiles
list, no later drop-in supersedes these choices, and all unrelated settings,
User=megaraid and security restrictions remain the recorded ones. A failed
review means remove only the two exclusive files and daemon-reload; do not
restart. These paths and commands are a proposed operator deployment, not a
change applied by this PR.

CONTROLLER_READ_ONLY defaults to false for compatibility. Explicit true blocks
all action POSTs before handler probes/subprocess execution, including locate,
patrol, CC, buzzer, advanced drive actions, hot spare, replacement and foreign
import/clear, including dry runs. Maintenance start/stop POSTs are also refused;
a previously persisted active window or configured true modes cannot bypass it.
The UI disables affected controls with “Controller actions disabled: read-only
rollout.” GET navigation, status and history remain usable. This setting is never
inferred from version, successful reads or sudo permissions. Future writes need
a separate reviewed rollout and physical validation; simply turning off this flag
is not write acceptance testing.

After the staged-unit review, run `systemctl restart "$rollout_unit"`.
Privately recheck effective User/environment, application SHA and logs. Require
two fresh collector polls **inside the service's actual systemd restrictions**;
stop and roll back on denied elevation/device access or stale polls. Check CV,
patrol/CC, all 8 drives, unknown learn time, no invented completion. Use the
GET-only [production smoke procedure](PRODUCTION_READ_ONLY_SMOKE.md), verifying
disabled controls without clicking them. Direct mutation refusals are tested
offline, not by production POSTs. Retain private evidence and both originals.

## 6. Exact reversal

For the additive activation above, the exact reversal of application selection is:

```sh
systemctl stop "$rollout_unit"  # Only if the new selection was already restarted.
rm -- "$rollout_dropin" "$rollout_env"  # Only the two exclusively created files.
systemctl daemon-reload
# Restore narrowed original sudoers files with cp -a from their private backups.
rm -- /etc/sudoers.d/megaraid-dashboard-rollout
visudo -c
sha256sum -c "$rollout_backup/binaries-before.sha256"
systemctl show "$rollout_unit" -p User -p WorkingDirectory -p ExecStart -p EnvironmentFiles
sudo -l -U megaraid
# Compare with saved records; stop on differences before restarting.
systemctl restart "$rollout_unit"
```

If activation never restarted the unit, omit stop/restart and remove the additive
files only. The prior release, env files and original binaries were never moved.
If the operator's separately reviewed procedure edited existing files instead:

Before restart: restore every edited effective environment/policy file from its
`cp -a` backup; remove only the new rollout sudoers fragment. Restore any narrowed
original rules, run `visudo -c`, compare the saved `sudo -l` output, and leave the
running release alone. A failed preflight never justifies activation.

After restart: stop `"$rollout_unit"`; restore the recorded prior application
release/directory selection, effective environment files, unit and drop-ins from
the saved copies. Restore the complete pre-change sudoers state, removing only
`/etc/sudoers.d/megaraid-dashboard-rollout`, then run `visudo -c` and compare
`sudo -l -U megaraid` with the saved output. Restore the prior effective
STORCLI_PATH=/usr/local/sbin/storcli64 and its recorded STORCLI_USE_SUDO value;
remove rollout-only CONTROLLER_READ_ONLY overrides if returning to the prior
release. Verify `sha256sum -c "$rollout_backup/binaries-before.sha256"`.
Run daemon-reload if unit/drop-ins were restored, then restart the prior service.
Confirm effective User=megaraid, prior SHA and environment and observed prior
behavior. The 7.37 patrol-read limitation returns; rollback does not fix it.
Do not restore the database unless independently necessary: this PR has no schema
change, and replacing it would discard later events. Keep its backup for recovery.

If keeping the new application while selecting 7.37, retain
CONTROLLER_READ_ONLY=true and replace the rollout policy with the generator's
`--binary /usr/local/sbin/storcli64` output; validate the full policy and effective
environment again. The strict 1.23.02 preflight is not a 7.37 acceptance gate:
7.37's known patrol unsupported result fails the required probe deliberately.

Once no environment, unit or sudoers rule references the new copy, remove only
that new staging directory and the private working inventory file, or quarantine
them root-only for diagnosis. Retain offline recovery media and change evidence
under the actual license. There is no RAID-state reversal: this rollout uses reads.
