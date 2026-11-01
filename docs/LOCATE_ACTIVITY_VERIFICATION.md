# Locate activity: supervised hardware acceptance

## Open production defect

On 2026-11-01 the owner reported that Locate Start was accepted by the controller
but no visible LED changed. This is a failed physical acceptance check, even though
the UI already labels LED state unverified. Do not mark Locate operational or the
whole product hardware-verified from a successful command or passing fake tests.

The owner describes eight SATA drives in cages, connected through two four-way
breakout cables. Blue LEDs remain on and green LEDs indicate activity. This does
not establish SGPIO/SES wiring or remotely controllable identification LEDs.

One identity-verified, read-only controller inspection reported:

| Property | Observed value |
| --- | --- |
| Auto detect BackPlane | SGPIO/i2c SEP |
| Use drive activity for locate | Off |
| Enable LED Header | No |
| LED Show Drive Activity | Yes |
| Dirty LED Shows Drive Activity | No |

The owner authorized enabling only `activityforlocate`. The pinned installed
StorCLI 1.23.02 global help explicitly lists `set activityforlocate=<on|off>`.
[Broadcom's reference](https://docs.broadcom.com/doc/12352476) describes this mode
for identifying drives through activity where SGPIO/SES is unavailable. That
documents the setting, not compatibility of the installed cages. Do not infer
that enabling LED Header or changing backplane detection is necessary.

## Reviewed one-property operation

`scripts/locate_activity.py` is a root operator tool, separate from application
startup and HTTP routes. Use a root-controlled copy of the reviewed source and
its imports, verified against the reviewed archive hash before execution.
Running a mutable user-owned staging script as root is not this procedure.

The CLI actions are `prepare`, `plan`, `apply`, `status` and `restore`:

1. `prepare` validates the running #141 service profile, pinned binary, protected
   help hash and existing controller identity policy. It creates an isolated,
   root-only approval containing just the property On/Off commands. It does not
   extend the application's 79-command policy or sudoers permissions.
2. `plan` reads identity, the explicit property and the eight-member optimal array.
   It does not write. Missing, malformed or changed evidence refuses execution.
3. The owner opens a time-limited maintenance window in the existing UI.
4. `apply` requires that persisted window twice, including immediately before
   dispatch. It commits audit intent first, sends only Off -> On, and reads back
   the result. A fingerprint checks that array membership/state and other
   Policies/Defaults stayed unchanged. SQL audit access uses the service identity
   so it does not leave root-owned SQLite sidecar files.
5. Inspect `/var/lib/megaraid-dashboard-led-maintenance/status.json`. The protected
   receipt and per-attempt history live under its `private` directory. A timeout,
   lost response, verification failure or incomplete audit must not become success
   or trigger an automatic retry. `status` reads without rewriting that history.
6. `restore` can undo a matching, verified Off -> On change during maintenance;
   it cannot blindly disable an unmanaged setting. No automatic rollback writes.

This tool never starts/stops Locate, Patrol, CC or rebuild, changes array
membership, restarts the application, changes schedules/clocks or migrates the DB.
The application remains on #141 until a separately authorized release.

## Physical acceptance still required

After `apply` finishes verified, use a single known drive in the existing UI:

1. Send Locate Stop to clear the previously accepted Start.
2. Send Locate Start and have the owner identify the visible LED change on that
   exact cage. Record the slot and observed pattern privately.
3. Send Locate Stop and have the owner confirm the identification pattern ends.
4. Check persisted action audit, the next normal Collector sample and application
   health. Do not force array operations to make the blink pattern easier to see.

If there is no visible effect, Locate remains unsupported/unverified for this
wiring. Do not experiment with unrelated controller flags or disk faults. Report
the limitation and review the cage/backplane/cable specifications before any
further hardware configuration. A command response cannot replace observation.

## Readiness boundaries

| Scenario | Software evidence | Real SERVER evidence / remaining acceptance |
| --- | --- | --- |
| Views, charts, Observed and normal Collector refresh | Browser/API regressions | #141 production read-only checks passed |
| Telegram transport | Delivery contract and fake incidents | One neutral test received by owner; no fault induced |
| Buzzer Disable -> sample `Disable` -> Enable | Full fake UI/API/wrapper/SQL round trip in this candidate | Disable observed; live Enable on fixed candidate and acoustic effect still unverified |
| Locate Start -> visible identification -> Stop | Exact commands, identity and persisted audits checked in software | Failed physical Start; activity mode and Start/Stop observation pending |
| Patrol and CC status/schedules | Saved/live response parsing and browser scenarios | Natural operation observed; no start/stop drill on healthy array |
| Maintenance, expiry and refusals | Persisted-window and late-revocation tests | Required for the approved one-property operation |
| Replacement, UGood, spin down, spare and foreign operations | Fake-hardware success/refusal/recovery scenarios | No destructive physical drill; not hardware-certified |
| Versioned install and rollback | Isolated filesystem, permissions and failure tests | Existing release adopted; managed update/rollback not yet exercised on SERVER |

These are separate acceptance boundaries. Keep unresolved physical behavior visible
in release notes and handoff; do not describe the dashboard as all buttons fully
working on this hardware.
