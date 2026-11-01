# StorCLI 1.23.02 compatibility on the 9270CV-8i

Target: production LSI MegaRAID SAS 9270CV-8i, SAS 2208, firmware
23.34.0-0019. **Current selection:** pinned 1.23.02 at
`/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64`, service account
`megaraid`, with the protected exact-command policy. Application release is `27af3da`.
See [CURRENT_STATE.md](CURRENT_STATE.md) for verified deployment evidence.

The following is the **historical pre-rollout inventory**, retained to explain why
the binary selection changed; it does not identify the currently selected executable:

- `/usr/local/sbin/storcli64` is **7.37**, the pre-rollout deployed selection.
- `/usr/local/MegaRAID Storage Manager/StorCLI/storcli64` is **1.23.02**,
  the binary used for the supplied successful legacy read observations.
- The production service account is **megaraid**, whose existing sudo permission
  covered only `/usr/local/sbin/storcli64` at that checkpoint; the later protected
  rollout policy separately approved the pinned executable.

These production facts differ from repository installer defaults (`raid-monitor`).
The compatibility implementation did not change installer defaults or itself perform
activation; deployment happened later through the separately reviewed rollout.
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

The invalid calendar text `2026/13/07 17:50:43 (847389043 seconds)` is recovered
from the numeric next-learn timestamp as `2026-11-07T17:50:43+00:00`. IBM documents
this value as seconds since 2000-01-01 UTC; the bounded fallback and its sources
are described in [CACHEVAULT_LEARN_TIME.md](CACHEVAULT_LEARN_TIME.md). The newer
binary's calendar text is not an input to recovery. An informational
`cachevault_next_learn_time_recovered` event retains the original value and the
decoded timestamp. Unrecoverable values still become unknown and emit
`cachevault_next_learn_time_invalid`; valid calendar dates retain their existing
parsing behavior. The collector retains the original CV JSON in snapshot raw
payload storage when enabled. CV command failure, malformed health values and disagreements
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
| `/c0/eE/sS insert dg=N array=N row=N` | Operator-confirmed global 1.23.02 help advertises `storcli /cx[/ex]/sx insert dg=A array=B row=C`. This establishes syntax only. Current production DG/array/row approval and physical effects remain unverified; the historical fixture must not authorize topology. |
| `/c0/fall import/delete` | Classic documented; physical effects unverified. |

CC concurrent/sequential controls describe execution order, not automatic/manual
scheduling. They do not set a start time or delay. Concurrent CC can increase I/O
contention. The API rejects the old auto/manual mode values. Patrol read retains
its separate auto/manual/disable meanings. The generated sudoers changes only the
two CC mode entries; it does not broaden permissions for drive or foreign writes.
An existing deployment's narrower sudoers can therefore still reject those writes.

## Read-only rollout

The executable operator procedure is now [STORCLI_READ_ONLY_ROLLOUT.md](STORCLI_READ_ONLY_ROLLOUT.md).
It supersedes the earlier illustrative slot-0-only sudoers plan. PR #128 added
parser compatibility; it did not change the production selection. A successful
read does not establish write capability, and CLI version never enables writes.

## Final combined rollout evidence update

The operator confirmed the prior global 1.23.02 help capture for insert syntax in
this review. `/etc/megaraid-dashboard-rollout/inventory.json` approves controller
and drive identities only. It does **not** approve DG/array/row. The historical
`tests/fixtures/storcli/redacted/c0_show_all.json` can have row different from slot;
it is a parser/test example, never current production authorization.

Follow [STORCLI_FULL_ROLLOUT.md](STORCLI_FULL_ROLLOUT.md) for the single final release.
Insert remains unavailable until current read-only topology is independently checked
and each exact slot/DG/array/row command is approved. No guessed topology grant is
created. All physical effects remain unverified.
