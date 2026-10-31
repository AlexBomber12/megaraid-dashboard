# Observed status corrections — 2026-10-30

Deployed through [PR #136](https://github.com/AlexBomber12/megaraid-dashboard/pull/136)
as `8b166c6`; retained in current `cfad744`. Production GET-only checks confirmed
eight observed rows, shared Collector ages and Auto/Enabled scheduling without an
invented interval. Full evidence and physical verification limits:
[CURRENT_STATE.md](CURRENT_STATE.md).

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

## Follow-up: mode duplication and readable chart times

Prepared after the `cfad744` production checkpoint; not yet deployed.

Known CC/Patrol mode descriptions no longer repeat the pressed mode button. The
selection still comes from the latest saved controller state. Unknown/disabled
mode descriptions, automatic rebuild information and reported Patrol intervals remain.
Refresh changes the selected buttons when new saved evidence changes; clicking a mode
does not optimistically claim that it is already active. See [action meanings](ACTIONS_REFERENCE.md).

All RoC, drive-temperature and error-history charts share browser-local date formatting:
time for 1h; date/time on two lines for short spans; short dates for week/month spans;
date/year for longer spans. Sparse long-range views retain useful times. Horizontal
labels are unrotated, automatically spaced and capped at eight. Hover retains full
local date/time with seconds and timezone; the axis names the browser's timezone.

UTC bucket labels in older drive range partials are explicitly read as UTC, not as
local wall-clock time. Raw labels/series, thresholds and replacement markers are
unchanged. `tests/e2e/test_chart_time_labels.py` verifies actual tick spacing and
pointer tooltips through initial rendering and HTMX range swaps at 375/650/1440 px,
Europe/Rome and America/New_York, including the DST fold. Hardware remains mocked.
