# Shared drive health assessment

Overview tiles, Drives rows, drive-detail health cards and position diagrams use
one presentation assessment. Firmware state, serial identity, S labels, controller
addresses and all command/action eligibility checks remain unchanged.

| Observed evidence | Assessment | Explanation |
| --- | --- | --- |
| Failed, Offline/Offln/OfLn, Missing/Msng, UBad | Critical | Controller failure, offline, absent drive or controller-marked unusable |
| SMART alert or positive predictive-failure count, including with Onln | Critical | Explicit SMART alert or predictive count |
| Critical configured drive temperature | Critical | Critical temperature |
| Rebuild, JBOD or unrecognized firmware state | Attention | Rebuild in progress, outside RAID or unknown raw state |
| Elevated configured temperature | Attention | Elevated temperature |
| Historical media/other error count without a stronger signal | Attention | Historical count; no automatic failed-drive/replacement verdict |
| UGood without reported issues | Ready | Raw UGood remains visible; no surface scan or compatibility certification |
| Online without reported issues | Healthy | Raw controller state remains visible |

Critical signals take precedence over warnings; all reported reasons stay visible.
Onln does not override SMART/predictive alerts. Both Overview and Controller summary
aggregation include these drive faults, counters and configured drive temperature
grades. Missing temperature stays explicitly unknown and does not invent a
physical failure. Unrecognized state spellings are warnings.

This changes presentation only: event severity, Telegram delivery, persistence,
wrapper/schema/grants, maintenance/destructive gates and replacement eligibility
are unchanged. No automatic controller operation follows a health badge.
Healthy/Ready describes reported evidence, not guaranteed absence of defects.

## Verification

- `tests/test_services/test_drive_health.py`: state aliases, SMART/predictive
  precedence, historical counters, temperature, mixed faults and identical saved
  assessments across Overview, Drives, detail and the selected backplane slot.
- `tests/e2e/test_drive_health.py`: isolated persisted samples for Onln with SMART
  or predictive alert, UBad, historical media errors, UGood, elevated/critical
  temperature and unknown firmware state at 1440/1024/390 pixels. Refresh/navigation
  recovery returns all surfaces to Healthy. S0 retains the actual 252:4 target;
  fake StorCLI receives no calls.
- Existing safety suites remain required. No production fault or hardware mutation
  is used for acceptance. Deployment requires separate authorization.
