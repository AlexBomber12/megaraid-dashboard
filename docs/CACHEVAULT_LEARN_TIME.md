# CacheVault next-learn timestamp compatibility

Deployed through [PR #137](https://github.com/AlexBomber12/megaraid-dashboard/pull/137)
as `cfad744` after fresh review and green CI. Normal collection persisted
`2026-11-07T17:50:43+00:00`; eight observed new-process polls logged INFO recovery
without the former warning. No controller writes or forced Learn Cycle were used.
See [CURRENT_STATE.md](CURRENT_STATE.md) for the dated verification boundary.

## Cause and evidence

The observed StorCLI 1.23.02 response contains
`2026/13/07  17:50:43 (847389043 seconds)`. Its calendar text is impossible,
but the numeric next-learn value decodes to 2026-11-07 17:50:43 UTC. The old
parser discarded the parenthesized value, then rejected month 13. Every normal
collection therefore repeated a warning and stored an unknown next-learn date.

[IBM's ServeRAID guidance](https://www.ibm.com/support/pages/additional-information-autolearnmode-and-delay-next-learn-cycle-properties-msm-ibm-serveraid)
defines the next-learn seconds relative to 2000-01-01 UTC. The
[FreeBSD MegaRAID implementation](https://github.com/freebsd/freebsd-src/blob/main/usr.sbin/mfiutil/mfi_bbu.c)
also exposes this next-learn counter as a uint32 value. The epoch interpretation
also agrees with the operator's earlier newer-StorCLI observation; that
observation is supporting evidence, not a value hardcoded into the parser.

## Parsing and operational limits

Valid calendar strings and explicit missing values keep their existing behavior.
Only an unparseable calendar in the known year/month/day + time + `(N seconds)`
shape can use recovery. The counter must contain at most ten ASCII decimal
digits and satisfy `0 < N < 0xffffffff`. Zero and the all-ones boundary are
conservatively unavailable for recovery. Negative, signed, fractional,
oversized, ambiguous, trailing-junk and unknown-unit inputs remain unknown
and continue to warn. No month is swapped, clamped or inferred from the clock.

Recovery returns an explicit UTC timestamp and logs
`cachevault_next_learn_time_recovered` at INFO with the original value, source
and decoded timestamp. It does not rewrite the raw response. Unrecoverable
data retains the existing WARNING. Other CacheVault health validation remains
strict; a repaired optional timestamp cannot make a bad health response valid.

This changes application interpretation and the existing stored next-learn
field only. It does not change the controller's clock, firmware, StorCLI binary,
learn schedule, command policy or database schema, and does not start a learn
cycle. Tests replay saved JSON through parser, collector and database persistence
using fake StorCLI. Deployment verification waits for normal collection and
checks the recovered timestamp and journal without controller writes.
