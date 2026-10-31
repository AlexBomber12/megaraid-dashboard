# Independent operator action audit — 2026-10-29–30

Scope: source baseline `c2d7826d77b99e2597b4f9f59933a9868720638a` (merged #133),
plus the fixes in `fix/operator-action-chain-audit`. This report supplements the
full inventory in [OPERATOR_SAFETY.md](OPERATOR_SAFETY.md) and the production
constraints in [STORCLI_FULL_ROLLOUT.md](STORCLI_FULL_ROLLOUT.md).

The reviewed source inventory contains 96 control declarations in 22 files.
Declarations include template loops and conditional controls; this is not a count
of buttons visible at once. All 20 controller POST scenarios are accounted for,
including the intentionally unavailable Mark UBad operation. Maintenance adds two
database-only POSTs. Navigation, filters, pagination, charts, refresh, disclosure,
confirmation, cancellation and wizard progression are included.

Historical audit boundary: no production deployment or controller mutation was
performed during the audit itself, and the healthy array was not a test fixture.
The withdrawn pre-audit helper must not be reused. The audit fixes subsequently
shipped in #134 (`25c2b74`) and remain in the deployed `cfad744` release.
See [CURRENT_STATE.md](CURRENT_STATE.md) for the 2026-10-31 production evidence.
The test counts below belong to the audit revision, not the current release.

## Findings and fixes

| Finding | Corrected behavior | Independent regression evidence |
| --- | --- | --- |
| Replacement dry run displayed instructions to pull a drive while leaving it online. | Preview ends with an explicit prohibition on removal; Missing is not sent and the swap stage stays hidden. | `tests/e2e/test_replace_wizard_flow.py` |
| Offline/Missing trusted controller Success without observing the new state. | Full capability requires another exact-slot live read with the same serial and expected state. Lost, wrong or unchanged evidence produces an uncertain result, failed audit and no swap authorization. | `tests/test_operator_chain_audit.py`, `tests/e2e/test_operator_chain_audit.py` |
| A drive response could carry the correct serial under another controller/slot. | Live identity proof requires exactly one controller 0, exactly one matching drive header and exact EID:Slt. | `tests/test_operator_chain_audit.py` |
| Clear Foreign was not bound to the reviewed configuration; equal-sized foreign members could share the old digest. | Import and Clear bind confirmation to a SHA-256 fingerprint of the complete foreign response metadata. A missing/stale Clear token or changed membership is refused before mutation. | Both independent audit modules; foreign parser tests |
| Maintenance/destructive/capability approval could be revoked while preflight reads were running. | Every full-capability mutation rechecks current persisted maintenance and current modes immediately before process launch. Stop Locate retains only its documented recovery exception. | `tests/test_operator_chain_audit.py` |
| A root-approved policy or pinned binary could change during preflight. | Wrapper rechecks the policy and binary after preflight; a change, missing file or failed hash check cancels execution. | `tests/test_operator_chain_audit.py` |
| Reload after a verified Missing lost the replacement workflow; closing/reopening after Insert could return to swap instructions. | Server-owned verified Missing evidence resumes at swap or insertion according to the snapshot serial; execution reconfirms the live serial. Same-page reopening after accepted Insert returns to rebuild monitoring. Locate does not revoke the verified replacement cycle. | Both independent audit modules |
| Locate feedback could enable the independently unapproved Start or Stop command. | Feedback preserves each exact command's availability. | `tests/e2e/test_operator_chain_audit.py` |
| Successful buzzer/advanced actions dropped a reverse proxy URL prefix. | Success redirects retain the application prefix. | `tests/test_operator_chain_audit.py` |

## Readiness table

“Software verified” below means browser/API/command/audit behavior against isolated
fake StorCLI, including relevant refusal and failure paths. It never certifies a
physical action on the target controller. Controller writes remain conditional on
root-reviewed exact commands, pinned binary, current identity/topology, maintenance
and the action-specific state gates.

| Control | Software readiness | Controller/hardware boundary |
| --- | --- | --- |
| Navigation, breadcrumbs, cards, slots, Previous/Next | Software verified | GET only; no controller mutation |
| Table sort; Events filters and Load More | Software verified | GET/local presentation only |
| ROC and drive chart ranges; Refresh now/live refresh | Software verified | Read-only; identity changes block stale actions |
| Disclosure panels and confirmation Cancel | Software verified | No mutation on cancellation |
| Start Maintenance, duration/reason; both Stop buttons | Software verified | Persisted window and audit only; no StorCLI |
| Locate Start | Software verified | Controller acceptance checked; physical LED unverified |
| Locate Stop, including expired-maintenance recovery | Software verified | Exact Stop approval still required; physical LED unverified |
| Buzzer Enable, Disable, Silence | Software verified | Correct exact command/result/audit; audible effect unverified |
| Patrol Read Start, Stop, Auto/Manual (API Disable) | Software verified | Fresh operation-state gates; scheduling/pass on hardware unverified |
| CC Start, Stop, Concurrent/Sequential; API selected VD | Software verified | Exact selected target/mode approval; actual pass unverified |
| Mark UGood | Software verified | Matching live UBad identity required; physical state change unverified |
| Spin Down | Software verified | Online members refused; motor behavior unverified |
| Dedicated Hot Spare | Software verified | Matching UGood identity and approved live DG required; hardware assignment unverified |
| Mark UBad | Intentionally unavailable | No established 1.23.02 command; UI disabled and API/wrapper refuse |
| Replacement Continue/Cancel, serials, dry run | Software verified | Preview never authorizes drive removal |
| Replacement Offline → Missing → physical swap | Software verified | Requires observed same-drive state after each command; no physical swap tested |
| New Drive Inserted; topology; Insert | Software verified | Different confirmed live UGood serial, successful Missing history and explicit approved DG/array/row; real insert/rebuild initiation unverified |
| Rebuild polling; Close and reopen | Software verified | Acceptance is not rebuild initiation; only explicit completed + 100% means complete |
| Foreign review, Import and Clear, dry run | Software verified | Changed metadata, active/unknown rebuild and incomplete inventory refused; real recovery response shapes/physical effects remain unverified |

## Validation evidence

Final validation results are recorded in the audit PR and local ignored logs:
`artifacts/audit-final-linux-ci.log` and `artifacts/audit-all-browser-final.log`.

| Check | Result |
| --- | --- |
| Ruff, formatting, strict mypy, bash syntax and shellcheck | Passed |
| Default unit suite | 3,046 passed; existing legacy status-tile module skipped |
| Statement and branch coverage | 100%; zero missing statements or partial branches |
| Isolated HTTPS operator-safety integration | 515 passed |
| Chromium browser suite | 197 passed |
| Production deployment / physical controller writes | Not performed |

The complete commands are:

```sh
PYTEST_ADDOPTS="-n 4" bash scripts/ci.sh
pytest -o addopts= tests/e2e/ --browser chromium --no-cov -q
```

The CI script includes Ruff, formatting, strict mypy, shell checks, the default
unit suite with the existing 100% statement/branch gate, and the isolated HTTPS
operator-safety integration matrix. Tests execute only temporary fake binaries
and temporary databases. Full-capability browser scenarios exercise the actual
authentication, CSRF, authorization, policy, wrapper, result and audit path.
The separate legacy live-refresh race tests use intercepted responses solely to
control frontend timing; they are not physical/controller acceptance evidence.

Local execution uses Python 3.12.3 in a dedicated WSL virtual environment and an
isolated Linux copy of the reviewed sources. `pytest-xdist` is installed only in
that temporary environment to run the complete CI suites in four processes; no
project dependency was added. The existing skipped legacy status-tile module
remains skipped because that UI was removed in #88a.

## Remaining limits

Physical LED, buzzer, spin-down, foreign recovery, replacement and rebuild behavior
on this firmware cannot be certified without exercising the hardware. Such tests
were explicitly excluded on the healthy array. Foreign/unconfigured-drive rebuild
response shapes still require real read-only evidence; unsupported or ambiguous
responses fail closed. Mark UBad remains unavailable. Readiness of this code does
not activate controller writes or replace the production approval procedure.
