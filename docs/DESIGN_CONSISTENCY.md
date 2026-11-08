# Operator display rules and design-audit disposition

This records the accepted design rules following the November 2026 audit of release #150.
It is a presentation policy, not authorization to change controller settings or array state.

## Signals

- Healthy, Attention, Critical and Unknown are the shared assessment vocabulary. Native
  firmware states remain available as technical detail and retain their service semantics.
- Normal measurements use neutral text. Elevated temperature and nonzero fault counters
  retain their own warning/critical colour, even if the containing row is already Critical.
- Error-count badges follow their containing drive's assessed severity. A historical media
  error must not acquire the visual meaning of a critical predictive failure.
- Unknown values use gray. Missing/conflicting evidence that requires operator action keeps
  its explicit warning/error and disabled command gates. Unrecognized physical-drive firmware
  states still assess as Attention; they are counted, not silently downgraded to Unknown.
- The shared drive summary counts all entries, includes unknown presentation grades, hides
  zero-count badges, and shows the total. Empty inventory never claims Healthy.
- Text labels carry the meaning alongside colour. An additional icon is useful where it
  helps scanning, but is not required on every duplicate aggregate badge.

## Dates, measurements and charts

- Observed means the controller snapshot's timestamp, never the time a page was rendered.
  Overview, Drives, Drive and Controller use the same local-time mechanism. UTC remains in
  the timestamp attribute/tooltip and the JavaScript-free fallback.
- Browser dates use the operator's locale and time zone with 24-hour hours. Firmware dates
  without a known time zone retain the explicit “controller time” qualifier. Never convert
  Patrol Read's reported schedule to UTC or promise that a past date is the next actual run.
- UI Celsius values and threshold tooltips use the common Celsius formatter. Persisted
  historical event text and notification-channel copy are not rewritten by these UI PRs.
- The initial Drive history and range responses share one fragment, thresholds and headings.
  Charts use a shared CSS-token palette. Ordinary error series use accent/info/gray colours;
  warning/critical colours belong to threshold lines. A single series needs no legend.
- Connected segmented controls change controller settings; separate pill controls filter
  views. Read-only range selection must never imply or dispatch a controller write.
- Shared warning/critical border tokens align cards, alerts and disclosures. Container shape
  still determines whether the severity stripe is at the side or the top.
- Human-readable models and measurements use the normal font; addresses, serial numbers,
  firmware and raw controller output retain monospaced formatting.

## Capacity requires evidence before semantic conversion

Decimal TB is the preferred main operator display; binary TiB can remain in technical details.
The implementation must first establish reliable canonical byte counts. Existing fixtures
contain vendor `Size: "2.728 TB"`, while the current parser treats the suffix as decimal.
Changing a formatter alone cannot prove that this represents a nominal 3 TB drive.

Do not infer capacity from a model name, multiply historical values to fit a label, or change
StorCLI parsing in a cosmetic PR. A follow-up must compare documented/raw byte or sector
counts, include exact unit fixtures and replacement-size checks, and explicitly document
wrapper-contract and persisted-data risks if that perimeter changes. No hardware write is
needed to investigate the read-only source evidence.

## Cleanup boundaries

Remove CSS rules/tokens only after checking templates, JavaScript and generated class names.
The audit's claimed dead-selector count is not itself deletion evidence. Keep legacy severity
aliases still used by mappings. Internal `-v2` names and the six existing responsive breakpoints
are not operator defects; bulk renaming or merging breakpoints is deferred unless a measured
problem justifies it. These changes are not release acceptance prerequisites.

Action help/disclosure affordances and destructive-confirmation styling should be evaluated
by operation: navigation to a preview, progression through a workflow, and executing a write
have different meanings. A blanket colour/icon rewrite must not blur those distinctions.

The second audit PR therefore addresses confirmed consistency defects and verified cleanup,
preserving intentional differences rather than mechanically applying every B/C suggestion.

## Acceptance

Use disposable database samples and the fake StorCLI boundary. Cover simultaneous Offline,
SMART and predictive failures, hot drives, historical media errors, unknown firmware states,
recovery, stale/missing observations, and zero inventory. Check computed colours, containment,
summary counts and error badges at 1440/1024/390 px, chart swaps and instance counts, local
times across time zones, and action refusals. Keep the project's 100% statement/branch gate,
isolated safety integration, full Chromium suite, green remote CI and fresh-head review.
Passing these tests does not claim physical LED or buzzer verification beyond owner evidence.

### Accepted deployment — 2026-11-08

Installed #153 / `211dfb2` includes #151-152 after fresh review and green CI. #152's
service verification was followed by real UI findings, not final acceptance. The
corrective release passed both drawn charts on 7d/365d/30d, automatic/manual Drive
Observed, two normal Collector runs, mapping/sort and 8 Healthy, without RAID writes.
[CURRENT_STATE.md](CURRENT_STATE.md) records dated results and per-PR test counts.

### Post-install browser regression checks

Keep history titles and range controls outside the HTMX swap target. Moving a sibling during
an outerHTML swap can detach lifecycle-event targets; do not relocate range controls during
that transaction. Create swapped Chart.js canvases only after `htmx:afterSettle`, after HTMX
has finished restoring style/width/height attributes. Otherwise a valid Chart instance can
remain while its canvas bitmap is blank. Regression tests must inspect painted pixels and
native canvas dimensions after settling, without calling resize/update to repair the result.

Drive Observed must refresh in the same live cycle as its health values, using the saved
snapshot timestamp. The drive header is therefore an identified live region, subject to the
same identity, focus, dialog and workflow pause rules as the other drive regions.
