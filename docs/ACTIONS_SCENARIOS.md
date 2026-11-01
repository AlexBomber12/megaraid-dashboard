# Actions: operator scenarios and review

Scope: Controller Actions, its Overview summary, maintenance, drive Actions and their
confirmation/recovery paths. This is a software acceptance checklist, not permission to
exercise a healthy RAID. Production checks are GET-only. Controller mutations below run
only against FakeStorcli, through the real application, database, HTTP and browser gates.

## Information hierarchy

Each row answers: what operation/setting, what has been observed, what can I do?
Running/Stopped/Paused/Unknown are compact state badges. Start/Stop are commands, not
state indicators: maintenance can disable both while an operation is still running.
Auto/Manual and Enabled/Disabled show the observed setting with the selected segment;
no duplicate mode sentence. Unknown selects neither segment. Silence does not turn off
future alarms. The buzzer's Enabled setting does not prove it is physically sounding.

Patrol's interval and controller-reported schedule date belong beside Patrol controls.
The date is labelled Reported schedule, not Next: even a future reported date is not an
independent prediction of when firmware will start. Manual explicitly says
automatic scheduling is disabled; an Auto setting with missing schedule data explicitly
says the schedule is unknown. There is no separate Scheduled Tasks panel containing the
same operation. Last-run information and explanations are behind accessible keyboard
operable disclosure controls. Foreign configuration has one state, including Unknown.
Common authorization reasons appear once at the gate; row-specific refusals stay visible.

The page header timestamps the observed sample, not the current page request. Sending a
command disables that row, announces Applying, and prevents an opposing command or live
replacement during the request. Success announces acceptance and waits for a newer saved
sample; it does not optimistically change state/mode. HTTP 5xx/network failures announce
uncertainty, retain the observed state and block immediate resubmission until a new sample.
Explicit client-side refusals preserve the original button availability. No automatic retry.

## Buzzer firmware spellings and command certainty

The observed `Alarm` value is stored verbatim. UI selection and live prewrite checks use
the same explicit aliases: On/Enable/Enabled, Off/Disable/Disabled, and the known silenced
spellings. In particular, a normal sample containing `Disable` must still allow Enable.
Unknown or conflicting state continues to refuse the write; command approval and all
authorization/identity/maintenance checks remain required.

Operator event text describes the request and its target, followed by the actual
outcome. Failed or rejected entries must not start by claiming the setting changed
or the physical buzzer was silenced. Existing historical events are not rewritten.

When the wrapper proves that preflight stopped before write dispatch, the audited buzzer
response is HTTP 409 with `execution: not_sent`. The UI says Command not sent, keeps the
saved selection and restores button availability. Failures after dispatch, lost responses
and audit persistence failures retain the uncertain-result path; no retry is automatic.

The fake-hardware regression follows Disable -> Collector/parse/persist `Disable` ->
Enable -> Collector/parse/persist `On`, then checks the observed segments and audit.
This software test does not claim another physical buzzer test on SERVER.

## Schedule root cause and time semantics

The preserved SERVER inspection from 2026-10-29 reports `PR Execution Delay: 168 hours`,
`PR Next Start time: 10/31/2026, 09:00:00`, Auto and Stopped. This is historical evidence,
not a claim that the same next-launch date is current.

The parser discarded both schedule properties from the already-issued read-only
`/c0 show patrolread` response. The UI instead searched the full controller response for
`Patrol Read Reoccurrence` / `Next Patrol Read launch`, but default compact snapshot
storage discarded that full response. The fix retains the parsed interval and next-start
fields inside the existing compact Patrol state. Old compact samples remain unknown until
the next normal collector pass. No raw-storage configuration change or new command is needed.

[Broadcom's StorCLI reference](https://docs.broadcom.com/doc/12352476), Patrol Read section,
specifies controller time as the scheduling reference; delay is in hours. Unzoned firmware
dates are labelled **controller time**, never falsely converted from UTC to browser time.
The UI neither infers a next launch from the last reported run nor advances an old launch
date by a guessed recurrence. Missing/malformed intervals and timestamps remain unknown.
A reported schedule date is retained even if its interval is unknown.

### Follow-up: old firmware schedule date and stale Observed header

Read-only diagnosis compares fresh `/c0 show all` and `/c0 show patrolread` responses,
controller/system clocks, iteration counts and saved operation-state transitions. Detailed
production telemetry stays in the private diagnostic report, outside repository artifacts.
The observed old marker came from fresh controller responses; it was not caused by browser
staleness or a clock difference large enough to explain the age. Unzoned controller output
still provides no timezone contract that would justify conversion to browser time.

An active Patrol state does not supply an actual start timestamp. A state-transition
history also does not prove firmware preemption or whether later activation resumes or
restarts work. [IBM's MegaRAID support note](https://www.ibm.com/support/pages/node/861188)
documents that CC can prevent Patrol from starting; this is a possible interaction to
investigate, not a sufficient explanation for every old schedule marker.

The confirmed cause of the misleading Next claim is treating the firmware's retained
schedule marker as a future prediction while a pass is active. The actual next launch is
not established by that marker. When this
firmware advances that marker after a normal completion remains unverified. No clock,
schedule, CC or Patrol setting is changed to make the displayed date look current.

The old UI labelled this marker Next, presenting it as a confirmed future start. It now
uses Reported schedule and explains the limit inside the existing information disclosure.
The header was also outside the live-refresh regions: operations could reflect a new saved
sample while Observed still showed the initial page-load sample. Including the header in
the existing refresh mechanism makes both follow the fetched sample without a full reload.

Four Chromium cases cover past/future controller dates with manual/automatic refresh:
`test_observed_header_and_operations_refresh_together_without_forecasting`. They update
the saved sample and operation state, exercise the real Refresh now button or 30-second
timer, and assert the new Observed timestamp, unchanged reported date and no invented Next
claim. The UI-region inventory also includes the header. All controller calls are mocked.
Follow-up validation on 2026-11-01 passed `scripts/ci.sh` (3151 default tests, one existing
skip, 100% statement/branch coverage, 515 safety cases) and all 232 Chromium cases.

The PR #140 scenario review also found that missing/failed foreign evidence was
displayed as absent; compact storage did not retain its normalized result; Overview's
Patrol/CC loaders were stubs returning None. These caused false reassurance and contradictory
screens. Compact foreign evidence and real validated operation loaders fix these cases.

## Acceptance scenarios

Software acceptance A01–A32 was exercised on 2026-10-31. Local `scripts/ci.sh` passed:
3151 default-suite tests (one existing skip), 100% statement/branch coverage and 515 isolated
safety integration cases. The full Chromium panel baseline passed 228 cases; after final
schedule wording, keyboard and server-clock refinements, all 93 affected browser cases
passed again. GitHub CI validates the entire committed head; its results and the fresh
review are recorded with the PR. Ignored local artifacts contain exact logs and screenshots.
These are software passes; a fake test never proves a physical effect.

| ID | User scenario | Required behavior | Executable coverage |
| --- | --- | --- | --- |
| A01 | Open Controller before the first collection | Waiting/Unknown; no invented healthy operation state | `test_controller_detail` empty DB; `test_controller_detail_page` |
| A02 | Open Controller long after collection | Header shows sample time and follows automatic/manual refresh; footer shows collector age | `test_actions_scenarios` live header; `test_observed_status` |
| A03 | Read active CC while management is locked | Running remains visible; Start/Stop unavailable | `test_actions_scenarios`; `test_actions_panel` |
| A04 | Inspect CC with one, multiple or missing VDs | Mode switch hidden only for exactly one VD; Start/Stop preserved | `test_actions_panel` unit/browser inventory transitions |
| A05 | Read Patrol Auto schedule with compact storage | Real interval and reported schedule date survive collector → DAO → view → page | `test_actions_schedule`; `test_actions_scenarios` |
| A06 | Auto but interval, next date or both missing | Show each known field; explicitly mark missing information | `test_actions_schedule`; `test_observed_status` |
| A07 | Manual with an old stored recurrence | No claim that automatic scheduling is enabled | `test_actions_scenarios`; `test_controller_detail` |
| A08 | Unknown/Disabled operation mode | No guessed selected button; keep explanatory state | `test_actions_panel`; `test_observed_status` |
| A09 | Read malformed, zero, negative or wrong-unit delay | No false interval; collection remains available | `test_actions_schedule` validation matrix |
| A10 | Read a past firmware next-start date | Label Reported schedule in controller time; no invented future occurrence | `test_actions_schedule`; `test_actions_scenarios` past/future dates |
| A11 | Compare active operations on Overview and Controller | Both consume the same saved validated CC/Patrol evidence | `test_actions_scenarios`; `test_main_page_view_model` |
| A12 | No operation evidence on Overview | Unknown, rather than "No active operations" | `test_actions_scenarios`; `test_main_page_view_model` |
| A13 | Start timed maintenance with reason | Persist actor/expiry; authorize only reviewed capabilities; no operation starts itself | `test_maintenance_flow`; `test_operator_chain_audit` |
| A14 | Submit blank maintenance reason | Do not open maintenance | `test_maintenance_flow` |
| A15 | End maintenance or let it expire | Gate/controls relock; narrowly approved Locate Stop recovery remains | `test_actions_panel`; `test_operator_safety` |
| A16 | Read-only, absent capability or incomplete policy | No write becomes available through a UI change | `test_read_only_rollout`; `test_full_actions`; integration safety matrix |
| A17 | Start/stop CC or Patrol in eligible state | Exact approved argv, real Success required, audit recorded | `test_controller_operations`; `test_full_actions` |
| A18 | Submit while pending, or before a fresh sample | One mutation; old observation retained; visible pending message | `test_actions_scenarios` |
| A19 | Receive a new normal collector sample after command | Pending clears; state and selected mode follow observation | `test_actions_scenarios`; `test_actions_panel` refresh |
| A20 | Controller failure, verification failure, lost response | Error/uncertainty visible; no success state or automatic retry | `test_actions_scenarios`; `test_controller_operations`; safety matrix |
| A21 | Buzzer Enabled, Disabled, Silenced or Unknown | Setting unambiguous; Silence distinct; unknown selects neither | `test_actions_panel`; `test_control_availability`; `test_actions_scenarios` |
| A22 | Use Silence/Enable/Disable | Exact command, gate and audit; remain on Controller with result feedback | `test_buzzer_control_flow`; `test_full_actions` |
| A23 | Foreign absent, present or probe unavailable | None detected / Detected / Unknown are distinct; Review only when present | `test_actions_schedule`; `test_actions_scenarios`; `test_foreign_configuration` |
| A24 | Review foreign Import/Clear and confirm | Review current digest/identity; destructive gates and audit; stale metadata refused | `test_foreign_configuration`; `test_operator_chain_audit`; safety matrix |
| A25 | Locate selected disk, then stop after expiry/uncertain start | Identity/CSRF/exact grants checked; Stop recovery exception remains narrow | `test_locate_flow`; `test_locate_rollout`; safety matrix |
| A26 | Mark Good / Spin Down / assign spare on eligible vs online disk | Correct eligibility, confirmation and audit; online member protected | `test_drive_detail_page`; `test_full_actions`; safety matrix |
| A27 | Look for Mark Bad | No panel control; retained unsupported API still refuses | `test_full_actions`; safety matrix |
| A28 | Replace disk through the complete workflow | Serial, explicit DG/array/row and fresh state checked at each step | `test_replace_wizard_flow`; `test_operator_chain_audit`; safety matrix |
| A29 | Authentication, CSRF, rate-limit or audit persistence fails | Fail closed; no UI path bypasses server gates | `test_auth_flow`; `test_csrf_flow`; safety matrix |
| A30 | Read long/error/unknown content at different widths | No overlap/overflow; selection visible while locked; keyboard disclosures usable | `test_actions_panel` 375–1440px; `test_actions_scenarios` |
| A31 | Switch chart ranges and inspect dates | All chart initializers retain readable local ticks and full tooltips | `test_chart_time_labels` |
| A32 | Maintenance while a real hardware incident occurs | Incident eligibility preserved; operator audit noise not sent | notifier/Telegram unit suite; existing one-message production receipt evidence |

No scenario above initiates CC, Patrol, a learn cycle, LED action, disk state change,
replacement, degraded array or rebuild on SERVER. Physical buzzer/LED behavior, disk removal,
replacement and media recovery remain untested on healthy production hardware.
