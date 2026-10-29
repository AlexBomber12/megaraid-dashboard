# One-shot production rollout: all approved supported actions

Independent button-chain audit and deployment hold:
[OPERATOR_ACTION_READINESS.md](OPERATOR_ACTION_READINESS.md). The audit fixes have
not been deployed; the previously staged activation helper was withdrawn.

This is the combined release procedure. Do not deploy intermediate read-only
or locate-only releases. No procedure in this PR has been executed on production;
no controller write is a permission test. Keep this PR open and unmerged until review.

## Evidence and approval boundary

The recorded evidence is in [STORCLI_123_COMPATIBILITY.md](STORCLI_123_COMPATIBILITY.md).
It establishes successful 1.23.02 status reads and CC `conc/seq` help. Most write
syntax there comes from classic manuals. The operator has now confirmed a previous
global 1.23.02 help capture: `storcli /cx[/ex]/sx insert dg=A array=B row=C`.
This establishes insert syntax only, not its physical acceptance on this controller.
No Mark UBad command is established. Do not invent one.

The protected `/etc/megaraid-dashboard-rollout/inventory.json` approves controller
and drive identities only. It does not approve DG/array/row. Independently check
current read-only topology and approve VD, DG and each replacement array/row;
never derive these from tests or slot ordering. In particular, the historical
redacted `c0_show_all.json` fixture contains rows that can differ from slot numbers
and must never be used as current production authorization. Synthetic test policies
are labelled **SYNTHETIC TEST ONLY** and must never be installed. No production
command contract is checked in: it would assert approvals not supplied to this PR.
Actions without this evidence stay unavailable in the final release, with a reason.
Read-only remains the default; choosing a version never enables an action.

## Controller-action POST and command matrix

Commands below end with literal `J`, use controller 0, enclosure 252 and an approved
slot S in 0–7. V and D are the independently approved VD and DG. A/R are explicit
replacement topology, never inferred from a single-span assumption. Every enabled
write requires Basic authentication, HTTPS CSRF, exact policy approval, active
maintenance (except Stop Locate), explicit controller Success and durable
actor-attributed audit. A positive result means **controller accepted**, not
physical verification. Offline/Missing additionally require a same-serial,
exact-slot live state read after Success before authorizing physical swap. A dry
run never authorizes removal. API state/identity checks remain authoritative after render.

| UI / POST suffix | Candidate argv before `J` | State and confirmation | Expected / refusal and next action |
| --- | --- | --- | --- |
| Blink Locate LED `/drives/252:S/locate/start` | `/c0/e252/sS start locate` | Present drive; active maintenance; UI click | Accepted; LED unverified. Missing maintenance: start window. Unknown outcome: inspect LED/audit, use Stop before another Start. |
| Stop Locate `/drives/252:S/locate/stop` | `/c0/e252/sS stop locate` | Exact approval; maintenance may be inactive/expired | Accepted; LED unverified. Read-only/none/unapproved: administrator must restore approved recovery capability. |
| Buzzer silence/disable/enable `/controller/buzzer/{silence,disable,enable}` | `/c0 set alarm={silence,off,on}` | Known buzzer setting shown in UI; maintenance; click | Controller acceptance; audible state unverified. Unknown setting: refresh collection and inspect controller. |
| Patrol Start/Stop `/controller/patrol-read/{start,stop}` | `/c0 {start,stop} patrolread` | Start: stopped/ready; Stop: active/paused/running; maintenance | Accepted operation request; refresh status. Unknown or conflicting state: refresh read probes; never infer idle. |
| Patrol mode `/controller/patrol-read/mode` | `/c0 set patrolread=on mode={auto,manual}` or `/c0 set patrolread=off` | Maintenance; explicit requested mode | Scheduling configuration accepted; does not prove a pass ran. Invalid mode refused. |
| CC Start `/controller/consistency-check/start` | `/c0/vV start cc` or `/c0/vall start cc` | Stopped/ready; maintenance; optional approved VD | Accepted CC request, not completion. Unknown/running state or unapproved VD: refresh inventory/status. `vall` requires the independently verified single-VD inventory. |
| CC Stop `/controller/consistency-check/stop` | `/c0/vall stop cc` | Active/paused/running; maintenance | Accepted stop request. Not running/unknown: refresh status. |
| CC mode `/controller/consistency-check/mode` | `/c0 set cc={conc,seq}` | Maintenance; concurrent/sequential selection | Execution order accepted, not auto/manual scheduling. Other values refused. |
| Mark as UBad `/drives/252:S/actions/mark-ubad` | **None established** | Always unavailable | 1.23.02 evidence does not document `set bad`; no grant and wrapper refuses it. Review exact read-only help; no substitute is guessed. |
| Mark as UGood `/drives/252:S/actions/mark-ugood` | `/c0/e252/sS set good` | UBad only; submitted, snapshot and live serial agree; confirmation dialog | Accepted state request, not repair verification. Other states/changed disk: refresh and inspect identity. |
| Spin Down `/drives/252:S/actions/spindown` | `/c0/e252/sS spindown` | **Unconfigured UGood/UBad only**; serial checks; confirmation dialog | Motor effect unverified. Online, failed, rebuilding, missing or unknown drives refused. Do not take a healthy drive offline to test. |
| Hot Spare `/drives/252:S/actions/hotspare` | `/c0/e252/sS add hotsparedrive dgs=D` | UGood; target DG exists; live serial/state checks; confirmation dialog | Spare assignment accepted, not rebuild verification. Missing/wrong DG or ineligible disk: inspect inventory; never guess DG. |
| Replacement Offline `/drives/252:S/replace/offline` | `/c0/e252/sS set offline` | Existing handler transition check plus live serial/state; maintenance + destructive; typed outgoing serial; dry-run defaults on | Separate audited request. Failed/uncertain Offline stops wizard; inspect hardware/audit before continuing. |
| Replacement Missing `/drives/252:S/replace/missing` | `/c0/e252/sS set missing` | Offln only; maintenance + destructive; typed serial; own audit | Accepted removal request, not physical removal. Wrong state/identity refused. |
| Insert & Start Rebuild `/drives/252:S/replace/insert` | `/c0/e252/sS insert dg=D array=A row=R` | **Syntax established; current topology approval still required**; latest successful Missing audit; live UGood replacement; typed new serial; maintenance + destructive | Unapproved syntax/topology disables wizard/API. Accepted insert does not prove rebuild started. No slot-sort derivation in full capability. |
| Foreign Import `/controller/foreign-config/import` | `/c0/fall import` | Fresh configuration; maintenance + destructive; typed digest; dry-run defaults on; rebuild/state safety checks | Configuration request accepted. Changed/absent/unsupported foreign inventory: refresh and inspect; do not retry stale digest. |
| Foreign Clear `/controller/foreign-config/clear` | `/c0/fall delete` | Fresh configuration matching the reviewed metadata fingerprint; maintenance + destructive; type `CLEAR FOREIGN CONFIG`; dry-run defaults on | Metadata request accepted. Changed metadata or rebuild/conflicting states refused; inspect controller. |

Alternative commands are approved independently: the POST validates its selected
VD/mode, and each rendered control checks the exact option it submits. An approved
CC All start does not require a grant for a separate per-VD start; Patrol Auto and
Manual do not require an unrequested Disable grant. Before enabling Begin Replacement
or accepting any Offline, Missing or Insert POST, all three Offline → Missing → Insert
commands/topology must be approved. This includes a direct Missing request, not only
browser progression. The wrapper rechecks the complete current contract as well;
an incomplete policy is refused before any mutation.

Non-mutating controls are inventoried in `docs/operator-controls.json`: navigation,
Events severity/category/operator filters and pagination, chart ranges, disclosure
panels, wizard cancel/close, confirmations, dry-run toggles and status polling.
Maintenance Start/Stop POSTs mutate only persisted maintenance/audit state; they
never invoke StorCLI and have their own auth/CSRF/capability and audit rollback tests.
Replacement topology and rebuild monitoring are GETs. Rebuild reads use
`/c0/e252/sS show rebuild J`; completion requires explicit completed state **and**
100% progress. Idle, missing progress, failure, stale data and timeout do not mean
completion. Close stops browser polling; snapshots remain collector-owned.

## Action-specific live prewrite evidence

Every full-capability write still verifies the pinned binary, exact command approval,
and the live controller serial/model. After preflight it rechecks the protected
policy, pinned binary and current persisted maintenance/mode authorization before
launching the write. Foreign confirmation fingerprints the complete metadata,
including equal-sized member changes. A missing local VD is not a general prohibition:

| Action family | Required live topology/safety | Does an absent local VD block it? |
| --- | --- | --- |
| Locate Start/Stop | Controller identity and exact approved slot command; existing Start maintenance gate and Stop recovery exception | No. Stop remains available after maintenance expiry even with an incomplete replacement policy. |
| Buzzer | Controller identity and the live alarm setting appropriate to Enable/Disable/Silence | No. |
| Patrol read | Controller identity and existing known patrol operation state/mode gates | No. Controller scheduling does not require a local VD as an authorization prerequisite. |
| Mark UGood / Spin Down | Controller identity plus submitted/snapshot/live drive identity and eligible unconfigured state | No. Never treat an online member as an unconfigured drive. |
| Dedicated hot spare | Controller identity, live explicit approved DG header and consistent DG count in `TOPOLOGY`, plus existing target-DG and UGood/serial checks | No; the approved DG itself must exist. VD presence is not used as a substitute for DG evidence. |
| Per-VD CC Start | Controller identity and complete, unique live VD identities containing the approved DG/VD target, plus known operation state | Yes, if the approved target is missing. An unrelated VD does not by itself block this exact-target command. |
| All-VD CC Start/Stop and controller CC mode | Controller identity and exactly the single approved local DG/VD, plus existing operation gates | Yes. These commands can affect all VDs, so an extra/unapproved VD also refuses the action. |
| Replacement Offline / Missing / Insert | Complete approved three-step contract at every POST and wrapper boundary, approved local DG/VD, explicit approved insert array/row, and existing state/history/typed-serial/destructive gates | Yes. These configured-array operations require the approved local topology; foreign recovery is a separate operation. |
| Foreign Import / Clear | Controller identity, exact command approval, current foreign configuration/confirmation, fresh complete positive PD count and unique approved slots/device IDs; an explicit idle or completed rebuild read for every present drive; unchanged controller/inventory, current foreign digest and foreign response data across the prewrite reads | No. Import can restore an absent local VD. Neither local VD nor local DG existence is inferred or required for this controller-level recovery command. |

Foreign safety checks use existing exact read grants only: `/c0 show all J`,
`/c0/e252/sS show rebuild J`, and `/c0/fall show all J`. No write is a permission
or safety probe. Missing/inconsistent drive membership, unknown/malformed/unsupported
rebuild reads, active rebuilds, or controller/inventory/foreign changes refuse the
write with instructions to refresh live reads, wait for rebuilds and reconfirm the
current foreign configuration. `Not available` and `Not supported` do not prove an
idle rebuild. Full-capability execution and dry-run previews establish safety from
these live reads even when no stored controller/VD snapshot exists. Legacy handlers
retain their snapshot rebuild gates. Previews execute no write; actual execution
performs the fresh prewrite checks again. No snapshot is updated to claim that import
restored a VD. Real 1.23.02 foreign/unconfigured-drive rebuild response shapes still need read-only evidence before
relying on recovery on the real controller; unsupported reads remain refusals.

Live DG headers prove only DG presence for hot-spare assignment. They never derive
replacement array/row from slot order or turn historical fixture topology into
current production approval.

## Evidence still required before activation

Capture current read-only `/c0 show all J` output (including VD/DG and topology),
plus `/c0/e252/sS show all J` for the approved slots, using the pinned wrapper path.
Record capture time, controller identity, binary hash, VD/DG and an unambiguous
EID:slot → DG/array/row map. Independently reconcile those rows with current
controller configuration and the approved drive identities. Any missing, ambiguous
or conflicting row stays unapproved. Do not substitute the historical fixture or
slot order. The action contract's evidence references must identify both the
syntax capture and the independently reviewed **current** topology for insert.
The identity-only inventory.json cannot supply this map. Exact effective sudo
permissions still require the authorization-only checks on the actual service user.

## Root-controlled contract and exact sudoers

`CONTROLLER_COMMAND_POLICY` names a JSON file described by
`storcli.policy.CommandPolicy`: `service_user`, `controller_serial`, `virtual_drive`,
`disk_group`, explicit `replacements` objects (`slot`, `dg`, `array`, `row`), and
`commands` objects (`command`, `evidence`). Each command is the complete literal
argv including `J`; its evidence references the reviewed 1.23.02 read-only help
capture. An evidence reference is an operator attestation, not machine-verifiable
proof. The reviewer must inspect the referenced capture. Reject stale/absent help.
No command for an unavailable action belongs in this contract.

Full capability validates root ownership, non-writable permissions and absence of
symlinks for the file and every ancestor. Use a root-controlled location such as
`/etc/megaraid-dashboard-rollout/actions.json` with read/traverse access for the
actual service account. Recorded production uses `megaraid`; installer defaults
use `raid-monitor`. Verify the installed unit, then set the account explicitly.
Never broaden directory permissions to make a failed preflight pass.

On a private operator workstation/root shell, after approval, render without
installing or executing any controller operation:

```sh
python scripts/storcli_rollout.py full-policy --inventory /etc/megaraid-dashboard-rollout/actions.json > /root/megaraid-approved.sudoers
python scripts/storcli_rollout.py authorization-checks --inventory /etc/megaraid-dashboard-rollout/actions.json > /root/megaraid-authorization-checks.sh
visudo -c -f /root/megaraid-approved.sudoers
```

The generator includes every approved write and all 25 existing exact read
commands, on only `/usr/local/libexec/megaraid-dashboard/storcli-1.23.02/storcli64`.
Its SHA-256 is
`642fff8a3976dbc85406d9f7e8f5f5ce4ceab2753ac68cab5245888b281c8547`.
No binary-wide grant, shell, sudo argument wildcard or unavailable action is emitted.
The generated command list is checked against the wrapper allowlist. Real-route
fake-boundary tests independently compare emitted argv with the command matrix.

Review the complete effective sudo policy for existing wider grants. After a later,
separately authorized root installation at root:root 0440 and complete `visudo -c`,
run the generated checks **as the actual service user**. Every check uses
`sudo -n -l -- BINARY ARGS`: it lists authorization and never executes the named
write. Positives cover every generated command. Negatives cover wrong binary,
controller, slot, VD, DG, topology, extra arguments and unavailable Mark UBad.
A negative unexpectedly allowed means broader grants remain: stop activation.
Positive denial means policy is incomplete: correct it before activation. No write
is used to test permission. Repeat checks after any contract/sudoers change.

## Single activation and rollback

1. Keep the running production release unchanged while reviewing this PR, exact
   help, baseline, command contract, sudoers and evidence limits. Run full CI and
   Chromium with isolated data/fake transports. Never inject faults into production.
2. Record installed app revision, actual unit/user, effective systemd restrictions,
   environment files, sudoers and file metadata. Back up the database, configuration
   and old app artifact privately. Preserve both installed binaries and licensed
   private recovery media. Do not copy a proprietary executable into this repository.
3. Stage the pinned binary separately with the existing exclusive `stage` procedure
   if not already staged. Verify ownership/path/hash. Do not replace either original
   binary. Review systemd `NoNewPrivileges`/device restrictions; do not remove them
   implicitly. Prepare the final app artifact and environment together.
4. Install the reviewed exact final policy, run complete syntax and authorization-only
   checks, and perform the existing wrapper-based read-only `probe` against the
   independently approved **inventory baseline** (a separate file from actions.json).
   Capture evidence to a new exclusive private filename. Review identity, counts,
   per-slot serials, CV, patrol, CC and rebuild status. Do not test failure on a healthy array.
5. In one operator-authorized activation, select the reviewed app revision, pinned
   binary, `STORCLI_USE_SUDO=true`, `CONTROLLER_ACTION_CAPABILITY=full-supported-actions`,
   `CONTROLLER_COMMAND_POLICY` and explicit `CONTROLLER_READ_ONLY=false`. Keep
   `MAINTENANCE_MODE=false` and `DESTRUCTIVE_MODE=false`. Preserve existing auth/proxy,
   TLS, workers/logging and environment-file permissions. Restart only after all
   prerequisites pass; this is the single production release, not a staged sequence
   of deployed read-only/locate-only releases.
6. Verify process revision/effective environment, authenticated HTTPS GETs, disabled
   action reasons, fresh collector data, audit readability and notification health.
   Real hardware incidents can now alert during maintenance; old maintenance skips
   may deliver if still relevant. Do not send a live Telegram test or perform writes
   as part of this task. A later real operator action uses the normal UI gates.
7. Rollback: stop accepting new actions; inspect uncertain hardware outcomes first
   (use exactly approved Stop Locate recovery if necessary under separate action
   authorization). Restore recorded app/environment/sudoers/unit metadata and restart
   the recorded prior release. Validate complete sudoers and read-only health. Keep
   audits and delivery rows; do not downgrade/delete the database or pretend rollback
   reverses a controller write. Preserve unrelated systemd drop-ins and original files.
   Restoring an old Telegram implementation restores its maintenance-skip limitation.

## Physical verification limits

All controller effects remain unverified: intended-bay LED start/stop, audible
buzzer output, patrol/CC execution and scheduling, offline/missing transitions,
UGood conversion, motor spin down, hot-spare assignment, foreign metadata changes,
replacement insertion and rebuild initiation/completion. Fault/replacement-only
paths require a suitable spare/test controller or a naturally occurring, separately
authorized incident. Software tests never manufacture such evidence from a healthy
production array. Missing current approved VD/DG/replacement topology (and any remaining family-specific
exact-binary help) is an **authorization blocker**, distinct from these physical-effect limits; keep the affected control
unavailable until reviewed evidence exists.
