> Superseded for the requested final release by [STORCLI_FULL_ROLLOUT.md](STORCLI_FULL_ROLLOUT.md).
> Do not deploy this historical intermediate release. Its binary staging and read-only
> probe instructions remain reference material for the single final activation.

# Operator procedure: StorCLI 1.23.02 locate-only rollout

Preparation only. This PR changes no production files, service selection, sudoers,
maintenance state or controller state. **Physical LED behavior remains unverified.**
Do not infer write compatibility from successful reads or JSON acceptance.
Use this procedure only after the reviewed PR is merged and CI is green.

## Action POST inventory and gates

Inventory taken from `web/routes.py` at base
`51267e8138918fa7ed48f98bbe6a954cd7a8ae9f`. All entries already pass through
application authentication (including proxy identity handling), CSRF and the shared
read-only dependency. The table describes existing inner checks; the new capability
gate runs before handlers, including their read probes and dry runs.

`{drive}` below is `{enclosure}:{slot}`. Every controller command uses the wrapper's
argv allowlist and JSON mode; mutations require explicit controller Success and
persist an actor-attributed audit result. Audit persistence failure returns 500,
including when the controller may already have accepted the command. Some early
input/identity refusals return before the existing audit path.

| POST route(s) | Existing handler gates | Locate-only availability |
| --- | --- | --- |
| `/drives/{drive}/locate/start` | Valid enclosure/slot, effective maintenance; explicit Success, audit | Allowed during maintenance |
| `/drives/{drive}/locate/stop` | Valid enclosure/slot; explicit Success, audit | Recovery allowed with inactive or expired maintenance |
| `/drives/{drive}/replace/offline`, `/replace/missing` | Snapshot and typed serial; maintenance + destructive modes for execution; live serial and legal state transition; dry-run validation | Unavailable |
| `/drives/{drive}/replace/insert` | Prior missing-step audit; new typed serial, topology and state; maintenance + destructive modes; live identity; dry-run validation | Unavailable |
| `/drives/{drive}/actions/mark-ubad` | Snapshot/live serial, valid state, maintenance + destructive modes; unsupported set-bad command deliberately fails closed | Unavailable |
| `/drives/{drive}/actions/mark-ugood`, `/actions/spindown` | Snapshot/live serial and legal state, maintenance | Unavailable |
| `/drives/{drive}/actions/hotspare` | Snapshot/live serial, UGood state, valid existing disk group, maintenance | Unavailable |
| `/controller/patrol-read/start`, `/stop`, `/mode` | Maintenance, validated mode, live known operation state and start/stop eligibility | Unavailable |
| `/controller/consistency-check/start`, `/stop`, `/mode` | Maintenance, validated target/mode, live known state and start/stop eligibility | Unavailable |
| `/controller/buzzer/silence`, `/disable`, `/enable` | Maintenance, validated buzzer action; result/audit | Unavailable |
| `/controller/foreign-config/import` | Live foreign configuration and digest confirmation, no rebuilding drive, maintenance + destructive modes for execution; dry-run validation | Unavailable |
| `/controller/foreign-config/clear` | Typed clear phrase, live foreign configuration/digest and no rebuilding drive, maintenance + destructive modes for execution; dry-run validation | Unavailable |
| `/maintenance/start`, `/maintenance/stop` | Start validates duration/reason; database window and actor audit | Allowed with locate capability, no StorCLI call |

Read-only mode rejects **all** these POSTs, including maintenance controls, even
with active/persisted maintenance and destructive mode enabled. GETs remain usable.
The route inventory test fails if a new action POST is added without an expectation.

## Application capability

The independent defaults are `CONTROLLER_READ_ONLY=true` and
`CONTROLLER_ACTION_CAPABILITY=none`. The only other capability value is `locate`;
invalid values fail settings validation. There is no all-writes selection.

With `CONTROLLER_READ_ONLY=false` and `CONTROLLER_ACTION_CAPABILITY=locate`, only
the two locate routes and database maintenance controls pass the outer gate.
Every other action receives 403 `controller_action_unavailable` before handler
execution, with an operator-facing reason. Future unnamed POST actions also deny
by default. Locate Start additionally requires effective active maintenance.
Locate Stop remains available with inactive or expired maintenance as a recovery
action. The UI disables Start with a clear reason outside maintenance and keeps
Stop usable; read-only mode or capability=none disables both. Authentication,
CSRF, exact sudo authorization, controller-result and audit checks still apply
to Stop. No other action gains an exception.

This is a UI/API gate, **not a substitute for sudoers**. It does not establish
binary identity or restrict the wrapper's independent command inventory. The
production sudoers boundary below limits this rollout to c0/e252/s0 through s7.
No button-specific flags, privilege changes, schema changes or startup installation
are introduced. Test fixtures that bypass the outer gate are explicitly limited
to legacy handler regressions; rollout acceptance tests use the actual gate.

## Pinned binary and protected inputs

Use only `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64`, SHA-256
`642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547`.
This is a recorded local hash, not a vendor signature. Preserve the original
binaries and approved private inventory. Follow the recovery-media, root-controlled
path/hash checks, effective systemd inspection and read preflight in
[the read-only rollout](STORCLI_READ_ONLY_ROLLOUT.md). Revalidate current state;
historical revisions in that document are not evidence of today's running release.

The executable, every ancestor, reviewed release/virtualenv, effective environment
files, systemd drop-ins and sudoers files must be root-controlled, symlink-free
and non-writable by the service account (including ACLs). Sudoers fragment:
root:root 0440. Protected rollout env: root:root 0600 in a root-controlled directory.
Do not use a service-writable `.env` or parent directory as the rollout authority.
Record actual file paths, owners, modes, SHA, service identity, release revision,
configuration precedence and backups privately. This host's established service
identity is **megaraid**, despite the generic installer's raid-monitor default.
The generator intentionally uses the recorded production identity; stop if it differs.
Do not relax NoNewPrivileges or other systemd hardening as part of this procedure.

## Generate and validate additive sudoers policy

From a reviewed release, in an operator root shell, set `rollout_release` and a
new private `rollout_backup` directory as in the read-only procedure. Back up the
complete effective sudoers policy and application configuration first. The command
below only writes a candidate file; it neither installs policy nor invokes StorCLI.

```sh
(set -C; "$rollout_release/.venv/bin/python" "$rollout_release/scripts/storcli_rollout.py" locate-policy > "$rollout_backup/locate.sudoers")
visudo -c -f "$rollout_backup/locate.sudoers"
visudo -c
sudo -l -U megaraid > "$rollout_backup/sudo-before-locate.txt"
```

Inspect all 16 grants: for each literal slot 0 through 7, exactly
`<pinned binary> /c0/e252/sN start locate J` and `stop locate J`.
The output uses a separate `MEGARAID_LOCATE` alias and preserves the existing
`MEGARAID_RO_READS` fragment and its **25 exact reads** unchanged. Total intended
StorCLI inventory: 41 commands. There are no wildcard arguments, arbitrary ranges,
generic binary grants or destructive commands. The generator rejects unknown
arguments and any non-pinned binary selection. Do not install both read-selection
aliases or grant writes to the original `/usr/local/sbin/storcli64`.

Only during a separately authorized activation, after candidate review:

```sh
test ! -e /etc/sudoers.d/megaraid-dashboard-locate
test ! -L /etc/sudoers.d/megaraid-dashboard-locate
install -o root -g root -m 0440 "$rollout_backup/locate.sudoers" /etc/sudoers.d/megaraid-dashboard-locate
visudo -c
sudo -l -U megaraid > "$rollout_backup/sudo-after-locate.txt"
```

Inspect **all effective rules**, including groups and external policy sources;
a broader existing grant invalidates this rollout. On validation failure remove
only this new fragment, validate again and leave the application read-only.

Use authorization-only checks, never an executed write as a permission test:

```sh
rollout_binary=/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64
sudo -l -U megaraid -- "$rollout_binary" /c0/e252/s0 start locate J
sudo -l -U megaraid -- "$rollout_binary" /c0/e252/s7 stop locate J
# All following checks must refuse authorization:
sudo -l -U megaraid -- "$rollout_binary" /c0/e252/s8 start locate J
sudo -l -U megaraid -- "$rollout_binary" /c0/e253/s0 start locate J
sudo -l -U megaraid -- "$rollout_binary" /c0/e252/s0 start locate J force
sudo -l -U megaraid -- "$rollout_binary" /c0/e252/s0 start locate
sudo -l -U megaraid -- "$rollout_binary" /c0/e252/s0 set offline J
sudo -l -U megaraid -- "$rollout_binary" /c0 start patrolread J
sudo -l -U megaraid -- "$rollout_binary" /c0 set alarm=off J
sudo -l -U megaraid -- /usr/local/sbin/storcli64 /c0/e252/s0 start locate J
sudo -l -U megaraid -- "$rollout_binary"
```

Repeat positive checks for all 16 generated commands and all 25 existing reads;
compare against the exact generated inventories. Check negative missing/extra
arguments on both verbs, wrong controller, foreign import/delete, CC, hot spare,
replacement and rebuild commands too. A negative authorization exit is expected;
run checks individually and retain results, rather than using `set -e` to skip
remaining negatives. `sudo -l` checks authorization only; it does not prove device
access, systemd compatibility, controller acceptance or physical LED behavior.

## Controlled activation and rollback

1. Complete policy/hash/release review and the existing read-only preflight while
   `CONTROLLER_READ_ONLY=true`. Verify fresh collector data, healthy controller,
   independent drive identities, audit storage and the intended physical slot.
   Keep maintenance inactive and `DESTRUCTIVE_MODE=false`.
2. Install only the reviewed additive policy above and complete authorization-only
   positive/negative checks. Keep the 25 reads unchanged.
3. Stage a root-controlled effective environment override selecting the pinned
   path, `STORCLI_USE_SUDO=true`, `CONTROLLER_READ_ONLY=false`,
   `CONTROLLER_ACTION_CAPABILITY=locate`, `MAINTENANCE_MODE=false`, and
   `DESTRUCTIVE_MODE=false`. Record the prior values and exact additive files.
   Use the inspected release/systemd activation procedure; verify precedence,
   restart only at the approved activation, then recheck process env/revision,
   fresh polls and GET-only controls: Start disabled with its maintenance reason,
   Stop available for recovery, all other controller writes disabled. No
   maintenance window yet.
4. **An active maintenance window suppresses alerts. Do not start a test window
   merely to prove a button.** Use local fake acceptance tests for that evidence.
   A physical test is a separate operator decision during a real approved window,
   with alert suppression understood, an observer at the chassis and independently
   confirmed slot/serial. UI Start requires that active window; Stop does not. Success
   means command acceptance plus persisted audit; LED state stays unverified until
   physically observed. Never retry blindly after timeout or audit failure; the
   command may have run. Inspect audit/controller access and physical state first.
5. End the actual window promptly and verify alerting resumes. Record physical
   observations privately; this PR supplies no physical verification.

Rollback: if Locate Start may have run, have the operator inspect the LED and
use the Stop recovery procedure below before removing permissions. Ending maintenance or reverting config does not stop a physical LED.
Restore `CONTROLLER_READ_ONLY=true`, `CONTROLLER_ACTION_CAPABILITY=none`, and
`MAINTENANCE_MODE=false` in the effective protected configuration; explicitly end
any persisted window through the authorized UI before locking POSTs, or wait for
its verified expiry. A restart alone does not clear persisted maintenance. Restore
prior root-controlled config/drop-ins/release selection from recorded backups,
keeping read-only true if retaining this release. Restart through the inspected
unit procedure and verify effective settings, disabled GET controls and fresh polls.
Remove only `/etc/sudoers.d/megaraid-dashboard-locate`; run `visudo -c`, inspect
`sudo -l -U megaraid`, and verify the 25 reads still authorize while all 16 locates
refuse. Preserve the pinned read binary, read policy, audit history and database;
there is no migration or RAID-state rollback in this PR. Do not restore the database
and discard later audit events. Keep private evidence and recovery media.


## Stop recovery after an inactive or expired window

A Start timeout, unknown/missing result, transport failure or audit persistence
failure can leave a possibly blinking LED. Do not repeat Start blindly: inspect
the physical LED, available audit records and controller access first. A 500 audit
failure can follow controller acceptance; it does not prove the LED stayed off.
Neither ending maintenance nor a restart stops a physical LED.

With the existing approved locate-only selection still active
(`CONTROLLER_READ_ONLY=false`, `CONTROLLER_ACTION_CAPABILITY=locate`, and the
same exact sudoers policy), authenticate to the HTTPS dashboard, refresh the
correct drive page and use **Stop Locate**. Its CSRF-protected POST is permitted
even after a persisted window expires and even if the prior Start has no saved
audit record. Start stays disabled with an active-maintenance reason. **Do not
open another window merely to recover Stop:** doing so would suppress alerts.
Stop does not start, extend or otherwise change maintenance or alert suppression.

A 200 Stop response requires explicit controller Success and a persisted audit
result. Physical LED state is still unverified until observed. If Stop fails or
its audit cannot be saved, inspect the physical LED, audit/database health and
controller access before deciding the next action; do not loop on either button.
When read-only is true or capability is none, both actions remain denied. Do not
bypass those gates or broaden sudoers; escalate to the operator responsible for
the separately approved rollout/recovery procedure. Physical LED behavior remains
unverified by this PR.
