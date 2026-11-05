# Commissioning, calibration and qualification plan

How this demonstration build would become a qualified tool at a specific blast furnace. Every
threshold marked *site value* is agreed with the plant; this document deliberately sets no
universal pass marks. Only stage G0 has been carried out, and only on synthetic data.

## 1. Stage gates

| Gate | Purpose | Software mode | Entry | Exit evidence | Status |
|---|---|---|---|---|---|
| **G0** Offline verification | code does what the equations say; behaviour plausible | `DEMO_SIMULATION`, `OFFLINE_SCENARIO` | this release | `TEST_RESULTS.md` (conservation, stoichiometry, dynamics, ML, MPC, safety, API, non-functional), `MODEL_VALIDATION.md` sections 1-6 | **done on synthetic data** |
| **G1** Historical replay and backtesting | model and forecasts against quality-checked plant records | `HISTORICAL_REPLAY` (upload on *Data and sensors*), offline calibration | data-sharing agreement; 3-6 months of historian, lab, casting and charging records mapped with `tag_mapping_template.csv`; on-premises install | calibration report, hindcast scores, data-coverage and identifiability report (section 2) | not started |
| **G2** Live read-only shadow | model health and drift on live data; nobody acts on it | `LIVE_MONITOR_ONLY` via the read-only edge path (`/api/v1/data/ingest`, service token) | G1 accepted; OT security review (`OT_SECURITY.md`); data diode or DMZ broker | 4-8 weeks of shadow operation: forecast scores by horizon, conservation bias, false-alert rate, availability | not started |
| **G3** Operator advisory | operators see advice, decide, and log the decision | `ADVISORY_SHADOW_MPC` plus acknowledgement on the desk | G2 accepted; operator training; SOP (section 5); management of change approved | intervention log (acknowledge / reject with comment), controlled comparison of advised vs not-advised periods, operator feedback | not started |
| **G4** Supervised setpoint gateway | setpoints passed to the DCS under DCS ownership | `SUPERVISED_MPC` - **not implemented** and refused by the software | separate engineering project: on-premises gateway, HAZOP/LOPA, functional-safety assessment, site authorisation | outside the scope of this product release | not available |

No gate may be skipped. Each gate is signed off by the roles in section 7 before the next starts.

## 2. Calibration and validation protocol (G1)

**Data.** Independent plant records covering several burden, fuel and stove seasons; normal and
upset periods (slips, hanging, chilled hearth, tuyere losses, stove changes); tapping and lab
data with sampling times. Split chronologically: calibration months, validation months, and a
held-out test period never used for tuning. The data-quality engine (`/api/v1/data/quality`)
runs on every file; flagged periods are reviewed, not silently dropped.

**Parameters to identify** (provisional in this build, see `MODEL_EQUATIONS.md`):
reduction and gasification rate constants (`calibration` section), gas-solid heat-transfer
multiplier, wall hot-side heat-transfer multipliers per zone, softening/melting temperatures
of each burden material, ore and coke voidage and particle size (permeability), raceway depth
correlation, tuyere cooling loss, hearth heat-loss and casting parameters.

**Procedure.**

1. Close the plant's own shift and monthly mass and energy balances first (reconciliation,
   `app/estimation/reconciliation.py`); the twin is calibrated to reconciled data, not raw tags.
2. Identifiability: finite-difference sensitivities of the measured outputs to every parameter
   (the same machinery the observer uses); parameters with near-collinear sensitivities are
   fixed at literature values and documented. The wall-heat multiplier is already known to be
   weakly identifiable from stave heat alone.
3. Fit in stages: steady periods (top gas, eta CO, fuel rate, HMT, slag rate), then dynamics
   (step-like events: blast temperature, moisture and oxygen changes with known times).
4. Uncertainty: parameter covariance from the fit and from the EKF; forecast intervals by split
   conformal calibration on the validation months, coverage checked on the test period.
5. Data coverage: the PCA/Mahalanobis out-of-distribution score of the ML layer is reported for
   the test period; operating points outside the calibration range are listed.
6. Re-identify the MPC step-response model on the calibrated twin (`/api/v1/mpc/identify`)
   and have the plant's process engineers review gains, signs and dead times.
7. Retrain the hybrid models on plant data (`/api/v1/models/train`); a model is promoted only
   if it beats persistence and physics + observer on the held-out block (rule in
   `MODEL_VALIDATION.md`). Models stay labelled not approved for control.

## 3. Acceptance metrics (thresholds are site values)

| Metric | How it is measured | Where in the software | Site threshold | Evidence owner |
|---|---|---|---|---|
| HMT forecast error by horizon (1, 2, 4, 8 h) | RMSE and MAE on the test period, repeated rolling origins; versus persistence and the plant's current indicator | forecast registry metrics, `/api/v1/forecast` | *site value* | process metallurgist |
| Si forecast error and interval coverage | as above; 95 % interval coverage | registry, conformal intervals | *site value* | process metallurgist |
| Conservation bias | element and energy residual of the reconciled plant balance vs the twin | Mass and energy balance page, `/api/v1/twin/balances` | *site value* | process metallurgist |
| Top gas, dP, stave heat loads | error on the test period | trends, profiles | *site value* | process engineer |
| False-alert rate | alarms raised without a confirmed process cause, per shift | alarm log, operator review | *site value* | operations |
| Advice availability and suppression causes | share of requests answered vs suppressed, by trust-gate check | recommendation history, audit | *site value* | automation engineer |
| MPC feasibility | share of PROPOSED vs NO_FEASIBLE_SOLUTION; physics-verification disagreements | recommendation history | *site value* | automation engineer |
| Stress behaviour | response to replayed upsets and injected instrument faults (G1 replay, demo faults) | scenario library, fault injection | qualitative review | process engineer |
| Operator feedback | structured interviews, acknowledgement comments | audit trail export | qualitative review | operations |

## 4. Qualification matrix

| Requirement | Evidence | Owner | Sign-off |
|---|---|---|---|
| Element and energy conservation every step | `test_conservation.py`, live residual chip | software | metallurgist |
| Correct thermochemistry and units | `test_thermodynamics.py`, `test_units.py`, `test_stoichiometry.py` | software | metallurgist |
| Plausible directional responses | `test_dynamics.py`, scenario library | software | metallurgist, operator |
| Calibrated to this furnace | G1 calibration report | Extractmet + plant | metallurgist |
| Forecasts better than baselines | G1/G2 scores (section 3) | Extractmet | metallurgist |
| Data quality and fault handling | `test_data_quality.py`, G2 shadow log | software + plant IT | automation engineer |
| Advice suppressed when untrustworthy | `test_mpc.py`, `test_safety.py`, trust gate log | software | automation engineer, safety authority |
| No plant-write path | `test_safety.py` source scan, `bf_plant_writes_enabled 0`, refusal of `ALLOW_PLANT_WRITES=true` | software | OT cybersecurity, safety authority |
| Secure deployment | `OT_SECURITY.md` controls, security review, penetration test | plant IT/OT | OT cybersecurity |
| Reliability of the service | availability during G2, restart and backup drills | plant IT | reliability/maintenance |
| Operator understanding | training record, SOP sign-off | operations | plant operator |

## 5. Operation: SOP, fallback, freeze, roll-back, incidents

**Operator SOP (G3).** Read the trust-gate table first; if advice is suppressed, follow normal
practice. If advice is proposed: compare the predicted trajectories and the independent physics
re-simulation, check the moves against current knowledge of the furnace (tuyere condition,
burden changes, casting), decide, make any change on the DCS under site procedures, then
acknowledge or reject on the desk with a comment. Advice expires after 30 minutes.

**Fallback on data loss.** Stale or bad critical tags fail the trust gate, so advice is
suppressed with the reason shown; forecasts fall back to physics + observer when a model is
missing, out of distribution or tampered with; the data clock on the status strip turns amber or
red, and advice stays suppressed until fresh, good data return.

**Fallback on solver loss.** An infeasible or failed optimisation returns NO_FEASIBLE_SOLUTION or
TIMEOUT with no moves; a physics-verification breach marks the advice; a job that exceeds its
time limit ends FAILED. None of these has any effect on the plant.

**Model freeze and version pinning.** For each gate, record and freeze: the release tag of both
repositories (Git commit), `MODEL_VERSION` (`bf-physics-1.0.0` here), the active configuration
version and its SHA-256, the physics hash of the shipped state and ROM, the identified ROM
timestamp, and the model registry IDs with their dataset hashes. Changes during a gate go
through management of change.

**Roll-back.** Software: redeploy the previous Git tag (Render: *Manual deploy* of that commit;
on premises: the previous container image). Configuration: *Load* the previous version on the
configuration page and save it (it becomes a new version; versions are append-only). Models:
models are files in the registry directory; remove the newer model directory and restart the
service, then confirm in the registry view which model is active.

**Incident response.** If advice was wrong or the service misbehaved: (1) stop using advice (switch
the mode back to `LIVE_MONITOR_ONLY` or `DEMO_SIMULATION`; an engineer can do this from the
desk); (2) export the audit trail and the recommendation record (inputs, trust gate, moves,
trajectories, versions); (3) preserve logs and `/metrics`; (4) root-cause review with the
metallurgist and automation engineer; (5) corrective action through management of change; (6)
re-run the relevant G-gate evidence before resuming.

**Change management and audit.** Every login (and failed login), mode change, configuration
version and preset switch, tag mapping, data upload, ROM identification, model training,
simulation request, recommendation, acknowledgement or rejection, alarm acknowledgement and demo
action is in the audit trail (`/api/v1/system/audit`, CSV export on the Administration page). Configuration
saves use optimistic concurrency and carry author and comment.

## 6. What is not covered by this release

HAZOP/LOPA, SIL determination, IEC 62443 assessment, a supervised setpoint gateway, site SSO,
a separate worker tier and plant-specific calibration. These are site-project deliverables.

## 7. Sign-off roles

| Role | Signs off |
|---|---|
| Process metallurgist | calibration, forecasts, balances, scenario plausibility |
| Plant operator (shift representative) | displays, SOP, advice usefulness |
| Automation / control engineer | MPC configuration, ROM, trust gate, data path |
| OT cybersecurity | deployment, network path, accounts, secrets |
| Reliability / maintenance | refractory and cooling displays, service availability |
| Plant safety authority | confirmation that no write path exists (G0-G3); any G4 decision |
