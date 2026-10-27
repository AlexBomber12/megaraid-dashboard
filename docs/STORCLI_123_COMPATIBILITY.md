# StorCLI 1.23.02 compatibility on the 9270CV-8i

Target: production LSI MegaRAID SAS 9270CV-8i, SAS 2208, firmware
23.34.0-0019, existing `/usr/local/sbin/storcli64` version 1.23.02.
This change does not replace that binary or execute anything on production.
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
success. Missing progress stays null. Idle status, even after an observed rebuild,
is insufficient evidence of completion; explicit 100% plus a compatible state is
required before recording completion. A missing progress value never becomes 0%.

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

1. Merge only after CI is green. Record the previous application commit/release,
   service unit, environment and sudoers fragment; take a database backup using
   the deployment's normal backup method. This PR has no migrations.
2. Build an application release from the merged SHA and stage it in a separate
   release directory. Preserve the existing StorCLI binary and configured path.
   Keep maintenance and destructive modes off. Do not run the installer blindly:
   stage and review its two CC sudoers substitutions, retaining all other entries.
   Validate a staged sudoers file with `visudo -c -f` before installing it.
3. In an approved application deployment window, switch the application release
   and restart its service. This plan authorizes no action during PR creation.
4. Through the authenticated dashboard, inspect collector freshness across two
   polls, the unchanged physical/virtual inventory and CV presence/health values.
   Confirm next learn is unknown, the diagnostic is present and the raw malformed
   value is retained. Confirm CC reads Concurrent/Stopped and unknown progress;
   confirm patrol read is available with 1.23.02. Do not click write controls.
5. Request rebuild status only for the known drive through the read-only endpoint.
   Expect null progress/ETA and Not in progress, with no new completion audit.
   Inspect the UI: CC offers Sequential/Concurrent; patrol offers Auto/Manual;
   Mark as UBad is disabled with a compatibility explanation.
6. Roll back if collection stalls, identities/health change unexpectedly, or any
   unknown status appears healthy/completed: stop the application service, restore
   the prior application release and any changed sudoers file, validate sudoers,
   and restart. Retain logs/raw responses for diagnosis. Do not roll back or change
   the StorCLI binary. No hardware state needs reversal because this verification
   uses reads only.

Any future physical write verification needs a separate approved maintenance
procedure, exact 1.23.02 command help, backups and recovery arrangements. Fake
subprocess tests prove argument/gate/audit behavior, not controller acceptance,
LED behavior, rebuild initiation or successful data reconstruction.
