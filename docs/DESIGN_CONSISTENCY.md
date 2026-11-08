# Operator display rules and design-audit disposition

This records the accepted design rules following the November 2026 audit of release #150.
It is a presentation policy, not authorization to change controller settings or array state.

## Signals

- Healthy, Attention, Critical and Unknown are the shared assessment vocabulary. Native
  firmware states remain available as technical detail and retain their service semantics.
- Normal measurements use neutral text. Elevated temperature and nonzero fault counters
  retain their own warning/critical colour, even if the containing row is already Critical.
- Drive tiles carry no separate error-count badge: the tile's reason text already names each
  counter, and a bare number cannot say whether it is a media error or a predictive failure.
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

The delivered second audit PR (#152) addresses confirmed consistency defects and verified cleanup,
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

Current #155 / `8da1a93` includes the #151-153 fixes and #154 noise pass. #152's
service receipt missed real UI failures, corrected by #153. #155 additionally closes
stale filtered Events navigation and the pre-existing close-threshold overlap.
Production normal collection, drawn charts, Events quiet filtering and automatic/manual
Observed passed; excluded arrivals and near-edge threshold pairs stay isolated tests.
[Release #155 acceptance](RELEASE_155_ACCEPTANCE.md) records the exact boundary/counts.

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

## Noise pass (November 2026, after #153)

Every fact appears once per screen, where it is acted on. Healthy is quiet; problems are loud.

- In collections (backplane tiles, table rows) Healthy is calm text with a green stripe or
  icon. The single summary states (controller card, Health Snapshot grade, summary chips)
  keep green as the one reassurance.
- Event importance (Info / Warning / Critical) is an icon and a word, never a status pill.
  Pills are reserved for the Healthy / Attention / Critical / Unknown assessment. Info uses
  the info colour, never the optimal green. Category names are display labels; stored
  categories and filter query values are unchanged. Only categories with events, or a
  selected one, are offered as filters. A subject the message already names is omitted.
- A health card says a sentence only for a fault: the grade already says Healthy. The
  controller summary leads with the cause and the drive count; VD states live in the RAID
  Configuration table. Firmware states are fields or an inline raw value, not one-value
  disclosures. "Member of VD n. RAID" stays; the DG number belongs to Position, whose
  DG / Span / Row remains evidence-bound.
- The Error Trend sparkline is removed. The Error Counter History chart below shows the same
  counters, and the sparkline's caption relabelled predictive failures as "Shield" next to a
  constant "BBM 0", neither of which the collector stores.
- Technical detail cards (Identity, Connection, Hardware Identity, CacheVault) list values
  the controller did not report once, as "Not Reported", instead of a row per "Unknown".
  Health cards keep Unknown, because there the absence is the signal. Hardware Identity does
  not repeat the page title, subtitle or the buzzer's Actions row; Position does not repeat
  the enclosure and slots from the page title.
- An action the drive's current state can never accept is not a row: one "Other Actions"
  row names it, as the single-VD CC mode switch is already omitted. Gates and API refusals
  are unchanged. A reason shared by several Actions rows is stated once above them.
- Threshold labels sit inside the plot at the right edge and never overlap. Chart series
  differ in lightness and dash, not hue alone.

### Live filter and threshold regression checks

Events category/count navigation follows a presentation revision of the global category
counts, independently of the filtered event-list cursor. A new excluded category or an
excluded severity must still refresh the navigation; an unchanged revision leaves its
DOM node intact. Polling retains selected filters and does not consume hidden event rows.
The opaque revision is presentation metadata, not an authorization token.

Temperature threshold labels reserve their measured text boxes, with spacing and plot
bounds applied together. Browser tests cover close thresholds near both plot edges on
desktop and narrow views; instance counts alone do not prove that labels remain legible.
