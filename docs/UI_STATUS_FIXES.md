# Observed status corrections — 2026-10-30

The drive position card displays DG / Span / Row from the saved physical-drive
response for the same enclosure, slot, device ID and serial number. Row is not
inferred from slot order. Missing, malformed, ambiguous or mismatched evidence
displays N/A. Normal collection retains compact position and identity evidence
even when full raw snapshot storage is disabled. Existing snapshots without
position evidence display N/A until the next successful scheduled collection.
This presentation value does not authorize replacement commands.

The drive footer uses the same last successful collector run state and current
clock as Overview and Controller. The snapshot timestamp remains the age of the
displayed drive data.

Patrol Read automatic scheduling is Enabled only for an observed Auto mode,
Disabled for Manual or Disabled mode, and Unknown when no recognized mode is
available. A missing recurrence does not imply Disabled. The live operation card
shows an interval only when the controller actually supplied it.

Validation uses saved redacted responses and fake StorCLI. Healthy production
RAID is not a test fixture. These changes issue no controller commands and do
not change command policy, permissions or the database schema.
