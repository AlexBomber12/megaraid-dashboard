# StorCLI 1.23.02 compatibility on the 9270CV-8i

Target: production LSI MegaRAID SAS 9270CV-8i, SAS 2208, firmware
23.34.0-0019. The operator corrected the production binary/account inventory:

- `/usr/local/sbin/storcli64` is **7.37**, the currently deployed selection.
- `/usr/local/MegaRAID Storage Manager/StorCLI/storcli64` is **1.23.02**,
  the binary used for the supplied successful legacy read observations.
- The production service account is **megaraid**, whose existing sudo permission
  covers only `/usr/local/sbin/storcli64`. It cannot currently sudo either the
  legacy source path or a new staged path.

These production facts differ from repository installer defaults (`raid-monitor`).
This PR does not change installer defaults, replace either binary, or execute
anything on production.
No physical write operation has been verified by this PR.

## Evidence and parsing contract

The operator supplied the following production read observations. These are
reported observations, not new verification performed by the PR author:

| Read command (append `J`) | Supplied production result |
| --- | --- |
| `/c0 show all` | Inventory command succeeded before the collector failed on the later CV date. |
| `/c0/vall show all` | Inventory command succeeded. |
| `/c0/eall/sall show all` | Inventory command succeeded. |
| `/c0/cv show all` | Both versions: Optimal, replacement No, 29 C, 89%; 1.23.02 returned an invalid learn date. |
| `/c0 show patrolread` | 1.23.02 succeeded; 7.37 returned `Un-supported command`. Do not upgrade to solve this issue. |
| `/c0 show cc` | Concurrent mode, Stopped current state on 1.23.02. |
| `/c0/vall show cc` | CC row: progress `-`, Not in progress on 1.23.02. |
| `/c0/e252/s0 show rebuild` | 1.23.02 returned one Response Data list row with matching Drive-ID; progress and ETA `-`, Not in progress. |

Redacted fixtures reconstruct the supplied relevant fields; they are not full
unedited captures. Existing inventory fixtures supply the collector regression.
Rebuild parsing supports a single row list or one named per-drive Rebuild Progress
table, rejects multiple tables/rows and mismatched identities, and requires command
success. A drive-specific request requires a matching Drive-ID in a row list;
a named per-drive table may supply that identity when its row omits Drive-ID.
Missing progress stays null. Idle status, even after an observed rebuild, is
insufficient evidence of completion; an explicit Complete/Completed state and valid
100% progress are both required before recording completion. In progress at 100%
remains active; 100% without an affirmative completed state cannot create a
completion audit. A missing progress value never becomes 0%.

The invalid date `2026/13/07 17:50:43 (847389043 seconds)` becomes unknown.
It is never corrected from the seconds suffix or inferred from the newer binary's
`2026/11/07` value. A structured `cachevault_next_learn_time_invalid` warning
includes the value. The collector retains the original CV JSON in snapshot raw
payload storage. CV command failure, malformed health values and disagreements
about CV presence continue to fail collection. Replacement flags must explicitly
say Yes or No; the summary fallback no longer invents No when missing.

## Complete command audit

All commands enter the wrapper allowlist and receive the trailing JSON `J` token.
The table covers collector probes, route builders and installer sudoers. Syntax
review is distinct from successful execution on the selected binary/hardware.

Classic command references: [LSI January 2013 manual](https://docs.broadcom.com/doc/12352476)
(chapters 3 and 4), and [Lenovo August 2017 guide](https://download.lenovo.com/storage/thinksystem_storage_adapter_sw_ug.pdf)
(chapter 6). Neither substitutes for exact-binary help and physical validation.
The operator's supplied 1.23.02 CC help explicitly advertises off/seq/conc.

| Surface / command family (append `J`) | Syntax disposition / verification limit |
| --- | --- |
| Inventory, CV, patrol and CC reads above | Retained; supplied production read evidence only. |
| `/c0/bbu show all`, `/c0/fall show all`, `/c0/eE/sS show all` | Classic documented reads; no production verification supplied for these individual commands. |
| `/c0 start/stop patrolread` | Classic documented; physical effects unverified. |
| `/c0 set patrolread=on mode=auto/manual`, `/c0 set patrolread=off` | Classic documented patrol scheduling; unchanged, physical effects unverified. |
| `/c0/vN start cc`, `/c0/vall start/stop cc` | Classic documented; physical effects unverified. |
| `/c0 set cc=conc/seq` | Replaces invalid consistencycheck=on mode=auto/manual across API, UI, allowlist and generated sudoers. Physical effects unverified. |
| `/c0 set alarm=silence/off/on` | Classic documented; physical effects unverified. |
| `/c0/eE/sS start/stop locate` | Classic documented; physical effects unverified. |
| `/c0/eE/sS set offline/missing/good` | Classic documented; physical effects unverified. |
| `/c0/eE/sS set bad` | Not documented for classic CLI in these references; UI unavailable with explanation and wrapper rejects before subprocess. No replacement command guessed. |
| `/c0/eE/sS spindown` | Classic documented for unconfigured drives; physical effects unverified. Controller can reject an ineligible drive. |
| `/c0/eE/sS add hotsparedrive dgs=N` | Classic documented; physical effects unverified. |
| `/c0/eE/sS insert dg=N array=N row=N` | Classic documented in 2017 guide; exact 1.23.02 availability and physical effects unverified. Confirm selected-binary help before any later write verification. |
| `/c0/fall import/delete` | Classic documented; physical effects unverified. |

CC concurrent/sequential controls describe execution order, not automatic/manual
scheduling. They do not set a start time or delay. Concurrent CC can increase I/O
contention. The API rejects the old auto/manual mode values. Patrol read retains
its separate auto/manual/disable meanings. The generated sudoers changes only the
two CC mode entries; it does not broaden permissions for drive or foreign writes.
An existing deployment's narrower sudoers can therefore still reject those writes.

## Reversible deployment and read-only verification plan (not executed)

Everything below is a proposed operator procedure for a separately approved
window, not a record of actions taken by this PR. No physical write operation
has been verified. Keep maintenance and destructive modes off throughout.

### 1. Record the effective deployment and rollback inputs

After CI/review, record the application SHA/release and inspect the actual service
unit, drop-ins, User, EnvironmentFiles, environment overrides, and NoNewPrivileges
setting. Confirm User=megaraid; do not install the default raid-monitor unit over
this deployment. Record the effective STORCLI_PATH and STORCLI_USE_SUDO, not merely
the values in an unused .env file. Save root-only copies of the effective unit,
drop-ins, environment file(s), and complete relevant sudoers configuration, plus
a consistent database backup. Record `sudo -l -U megaraid` and hashes/version
information for both original binaries. This PR has no migrations.

The service must permit the existing narrow sudo execution model. If
NoNewPrivileges=yes or another service restriction prevents sudo elevation, stop:
a separately reviewed service-policy adjustment is required before this plan can
work. A successful interactive shell probe does not override systemd restrictions.
Do not weaken the unit's hardening implicitly or run the whole web app as root.

### 2. Stage an immutable selection at a path without spaces

Use a new root-owned regular executable at:

`/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64`

Do not overwrite `/usr/local/sbin/storcli64` (7.37), its permissions, or the legacy
source. The following commands are examples for the operator's root shell only;
none were executed during this PR. First save/review the source SHA-256 against
the operator's trusted 1.23.02 copy; do not invent a known-good hash or treat a
version label as proof of authenticity. Inspect existing ancestors with `namei -l`
before staging and refuse symlinks or non-root/group-writable parents.

```sh
set -eu
compat_source='/usr/local/MegaRAID Storage Manager/StorCLI/storcli64'
compat_dir='/usr/local/libexec/megaraid-dashboard/storcli-1.23.02'
compat_bin="$compat_dir/storcli64"
sha256sum -- "$compat_source" /usr/local/sbin/storcli64
# Save the hashes in the root-only rollback record before proceeding.
test ! -e "$compat_dir"
test ! -L "$compat_dir"
install -d -o root -g root -m 0755 "$compat_dir"
install -o root -g root -m 0755 -- "$compat_source" "$compat_bin"
test -f "$compat_bin"
test ! -L "$compat_bin"
namei -l -- "$compat_bin"
stat -c '%U:%G %a %F' -- "$compat_bin"
sha256sum -- "$compat_source" "$compat_bin" /usr/local/sbin/storcli64
cmp -- "$compat_source" "$compat_bin"
```

Require identical source/staged SHA-256 values and an unchanged 7.37 hash. Require
root:root ownership, mode 0755 for the staged executable/directories, no symlinks,
and no group/other-writable ancestor in the entire path. Verify megaraid cannot
replace the binary or alter its parent directories. Stop on any discrepancy or
pre-existing staging directory; never overwrite an unknown staged artifact.

### 3. Stage matching read-only sudoers and application configuration

The current rule for `/usr/local/sbin/storcli64` does **not** authorize the staged
path. Prepare a distinct `/etc/sudoers.d/megaraid-dashboard-123` fragment with
root:root ownership and mode 0440. Preserve existing rules for rollback. Use exact
read commands for megaraid at the staged absolute path, for example:

```sudoers
Cmnd_Alias MEGARAID_123_READS = /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0 show all J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/vall show all J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/eall/sall show all J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/cv show all J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/bbu show all J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/fall show all J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0 show patrolread J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0 show cc J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/vall show cc J, \
  /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/e252/s0 show rebuild J
megaraid ALL=(root) NOPASSWD: MEGARAID_123_READS
```

Validate the staged fragment with `visudo -c -f <staged-fragment>`; after installing
it, validate the complete policy with `visudo -c` and inspect `sudo -l -U megaraid`.
Do not replace this with ALL, arbitrary arguments, a wildcard binary path, or a
rule for the source path containing spaces. Grant no writes to the staged binary
for this read-only rollout; write acceptance and physical effects remain unverified.
The installer's CC write-rule substitutions are not a deployment recipe here.

Prepare the **effective** environment file/overrides to select exactly:

```dotenv
STORCLI_PATH=/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64
STORCLI_USE_SUDO=true
MAINTENANCE_MODE=false
DESTRUCTIVE_MODE=false
```

Preserve environment ownership/mode and other settings. Check for a unit or
drop-in override that would still select 7.37. Stage the new application release
separately; do not run the installer blindly or restart yet.

### 4. Prove the new read path as megaraid before restart

From the operator's root shell, after installing the validated narrow read policy,
run the following through the actual service identity, with no password prompt:

```sh
sudo -u megaraid -- sudo -n /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0 show patrolread J
sudo -u megaraid -- sudo -n /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/cv show all J
sudo -u megaraid -- sudo -n /usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64 /c0/e252/s0 show rebuild J
```

Use the same prefix for the other inventory/CC reads explicitly listed above.
Require command Success in JSON (exit zero alone is insufficient), identify the
CLI as 1.23.02 from its response/version metadata, and confirm hashes still match.
The malformed optional CV date may persist; that is expected evidence, not a date
to correct. Optional BBU/foreign probes may be unsupported; record their real
results without calling an unsupported result healthy. Stop on permission/path,
version, identity, required-read or hash failures. Do not test a mutating command.

### 5. Restart and verify the application using reads only

Only after those checks, activate the staged application release and matching
configuration, then restart the saved service. Recheck the effective User and
environment. Over two collector polls, verify freshness, unchanged inventory,
CV presence/health, unknown next-learn time, the structured diagnostic and the
original raw value. Confirm patrol read is available and CC reads Concurrent /
Stopped with unknown progress. Check the known drive's status endpoint for null
progress/ETA and no completion audit from idle. Confirm CC controls say
Sequential/Concurrent, patrol controls remain Auto/Manual and Mark as UBad is
unavailable. Keep write modes off and do not click write controls.

### 6. Roll back both configuration and binary selection

If any pre-restart check fails, restore changed environment/policy files and remove
only the newly added staged read fragment; validate `visudo -c`. Do not restart
into a failed selection. If a post-restart poll fails or any unknown/active state
appears completed, stop the service, restore the prior application release,
effective environment (including STORCLI_PATH=/usr/local/sbin/storcli64 and its
previous sudo flag), saved unit/drop-ins and sudoers state. Remove the added
megaraid-dashboard-123 fragment and any rollout-only override, validate the full
sudoers policy, and daemon-reload if the unit changed. Verify the preserved 7.37
hash and prior sudo access as megaraid, then restart the prior service selection.
This restores the pre-change behavior, including its known 7.37 patrol limitation.

Retain logs, hashes and raw responses for diagnosis. Once no configuration or
sudoers entry references it, remove the newly staged 1.23.02 copy/directory, or
quarantine it root-only for diagnosis. Leave both original binary paths/bytes
unchanged. No hardware state needs reversal because verification used reads only.

Any future physical write verification needs a separate approved maintenance
procedure, exact 1.23.02 command help, backups and recovery arrangements. Fake
subprocess tests prove argument/gate/audit behavior, not controller acceptance,
LED behavior, rebuild initiation or successful data reconstruction.
