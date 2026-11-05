# Test results - backend

Recorded on 5 November 2026 in the build environment (Linux, 2 vCPU, Python 3.13.18). The run
used a **fresh unzip of `blast-furnace-backend.zip`** into an empty directory and a fresh virtual
environment (`pip install -r requirements-dev.txt`, 57 packages). All data are synthetic.

After this run only documentation changed (this file, comment headers of `data/examples/*.csv` and the
header writer in `scripts/make_doc_figures.py`); no application or test code changed.

## Summary

| Check | Command | Result |
|---|---|---|
| Unit, physics, data quality, observer, ML, MPC, API, safety and non-functional tests | `python -m pytest -q --cov=app` | **179 passed, 15 warnings in 604.88s (0:10:04)** |
| Line coverage of `app/` | pytest-cov 7.1.0 | 81% |
| Static analysis | `bandit -q -r app -ll` (CI gate: medium and above) | 0 high, 0 medium; 5 low (reviewed, below) |
| Dependency vulnerabilities | `pip-audit` on the installed packages | no known vulnerabilities |
| End-to-end through the desk (with the frontend) | Playwright, Chromium, production CSP | 11/11 checks passed, 0 browser errors |

## Test run

```
$ python -m pytest -q -rs --durations=15 -p no:cacheprovider --cov=app --cov-report=term --junitxml=test-results.xml
# clean room: fresh unzip of blast-furnace-backend.zip, fresh venv (pip install -r requirements-dev.txt)
# started 2026-11-05T12:43:56Z on Python 3.13.18, 2 CPU
...
179 passed, 15 warnings in 604.88s (0:10:04)
EXIT 0
# finished 2026-11-05T12:54:02Z
```

Warnings (15): 14 OSQP `PendingDeprecationWarning` (default of `raise_error` will change) and one
Starlette deprecation notice in the FastAPI test client; neither affects results.

```
============================= slowest 15 durations =============================
149.35s call     tests/test_api_contract.py::test_mpc_si_is_zone_controlled_by_default
45.08s call     tests/test_dynamics.py::test_long_horizon_numerical_stability
37.49s call     tests/test_dynamics.py::test_oxygen_enrichment_raises_raft_and_production
36.83s call     tests/test_dynamics.py::test_blast_moisture_cools_lower_zone
35.45s call     tests/test_dynamics.py::test_blast_temperature_heats_lower_zone
26.67s call     tests/test_conservation.py::test_cumulative_balance_closes
26.34s call     tests/test_limiting_cases.py::test_steady_state_consistency
22.17s call     tests/test_scenario_end_to_end.py::test_blast_temperature_scenario_conserves_and_heats
21.85s call     tests/test_conservation.py::test_hearth_tapping_conservation
21.14s call     tests/test_scenario_end_to_end.py::test_hbi_scenario_runs_and_reports_compositions
20.51s call     tests/test_api_contract.py::test_mpc_recommend_returns_advisory
17.24s call     tests/test_conservation.py::test_inventories_stay_non_negative
16.61s call     tests/test_conservation.py::test_streams_report_inputs_and_outputs
12.70s call     tests/test_conservation.py::test_every_step_closes_elements_and_energy
10.81s call     tests/test_api_contract.py::test_simulation_job_lifecycle
```

Performance (from `test_step_time_and_api_latency_under_synthetic_load`, measured without
coverage on the same machine; coverage slows the run): about 0.25 s per twin step with the live
loop competing, `GET /api/v1/twin/state` p95 about 0.5 s; bounds asserted 1.5 s and 2.0 s.

## Coverage

```
_______________ coverage: platform linux, python 3.13.18-final-0 _______________

Name                                Stmts   Miss  Cover
-------------------------------------------------------
app/__init__.py                         0      0   100%
app/api/__init__.py                     0      0   100%
app/api/v1/__init__.py                  0      0   100%
app/api/v1/control.py                  74     35    53%
app/api/v1/deps.py                     28      1    96%
app/api/v1/forecasts.py               147     59    60%
app/api/v1/physics.py                 163     80    51%
app/api/v1/reports.py                  50     15    70%
app/api/v1/simulations.py             131     47    64%
app/api/v1/status.py                  145     47    68%
app/api/v1/telemetry.py               192     90    53%
app/core/__init__.py                    0      0   100%
app/core/config.py                     59      5    92%
app/core/errors.py                     28      2    93%
app/core/safety.py                     33      7    79%
app/core/security.py                   95      5    95%
app/core/units.py                      32      4    88%
app/data/__init__.py                    0      0   100%
app/data/csv_excel.py                 104     24    77%
app/data/opcua_readonly.py             25     25     0%
app/data/quality.py                    91      4    96%
app/data/synthetic.py                 175      9    95%
app/data/tag_mapping.py                43      7    84%
app/db/__init__.py                      0      0   100%
app/db/models.py                      188      0   100%
app/db/session.py                      38      0   100%
app/estimation/__init__.py              0      0   100%
app/estimation/observer.py            154      6    96%
app/estimation/reconciliation.py       45      0   100%
app/main.py                           110     22    80%
app/ml/__init__.py                      0      0   100%
app/ml/baselines.py                    22      0   100%
app/ml/drift.py                        33     10    70%
app/ml/features.py                     83      1    99%
app/ml/hybrid.py                       83     11    87%
app/ml/registry.py                     59      6    90%
app/ml/training.py                     57      8    86%
app/mpc/__init__.py                     0      0   100%
app/mpc/constraints.py                 45      0   100%
app/mpc/optimizer.py                  270     62    77%
app/mpc/predictions.py                114     51    55%
app/mpc/recommendations.py             48      5    90%
app/mpc/verifier.py                    55      6    89%
app/physics/__init__.py                 0      0   100%
app/physics/burden.py                 175      5    97%
app/physics/defaults.py                32      7    78%
app/physics/dripping.py               143      2    99%
app/physics/energy_balance.py          65      1    98%
app/physics/gas_flow.py                54      0   100%
app/physics/hearth.py                 206      1    99%
app/physics/heat_transfer.py          154     19    88%
app/physics/integrator.py             866     46    95%
app/physics/material_balance.py        39      9    77%
app/physics/outputs.py                251     58    77%
app/physics/raceway.py                169      2    99%
app/physics/reactions.py               58      8    86%
app/physics/slag_metal.py              92      3    97%
app/physics/species.py                130      6    95%
app/physics/thermodynamics.py          73      4    95%
app/physics/zones.py                   98     22    78%
app/schemas/__init__.py                 3      0   100%
app/schemas/common.py                  39      0   100%
app/schemas/requests.py               169     20    88%
app/schemas/responses.py               58      0   100%
app/services/__init__.py                0      0   100%
app/services/alarms.py                 99     38    62%
app/services/config_validation.py      81     20    75%
app/services/economics.py              81     72    11%
app/services/jobs.py                  156     20    87%
app/services/reporting.py              80     31    61%
app/services/scenarios.py              96     18    81%
app/services/studies.py               110     63    43%
app/services/twin_service.py          828    262    68%
app/storage/__init__.py                 0      0   100%
app/workers/__init__.py                 0      0   100%
-------------------------------------------------------
TOTAL                                7424   1391    81%
```

## Static analysis and dependencies

```
$ bandit -q -r app -ll    # medium severity and above: the CI gate
# 2026-11-05T12:44:24Z, bandit 1.9.4
EXIT 0

$ bandit -q -r app -f custom --msg-template "{relpath}:{line} {test_id} {severity} {msg}"    # every finding
app/api/v1/forecasts.py:34 B110 LOW Try, Except, Pass detected.
app/api/v1/telemetry.py:235 B110 LOW Try, Except, Pass detected.
app/ml/hybrid.py:27 B110 LOW Try, Except, Pass detected.
app/ml/hybrid.py:33 B110 LOW Try, Except, Pass detected.
app/ml/registry.py:67 B112 LOW Try, Except, Continue detected.

Code scanned:
	Total lines of code: 9507
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 4

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 5
		Medium: 0
		High: 0
	Total issues (by confidence):
```

The five low findings are `try/except/pass` (or `continue`) around an optional LightGBM/XGBoost
import, a WebSocket close, the analytic interval fallback and the registry listing; each was
reviewed and kept. The one medium finding of the first scan (pickle deserialisation) was fixed by
a SHA-256 check before loading (`test_tampered_model_artefact_is_not_deserialised`).

```
$ pip-audit -r freeze-be.txt --no-deps --disable-pip -s pypi    # freeze-be.txt = pip freeze of the clean-room venv (57 packages)
# 2026-11-05T12:44:05Z, pip-audit 2.10.1
No known vulnerabilities found
EXIT 0
```

## End-to-end check

Backend started from the delivered code (`uvicorn app.main:app`, demo mode, SQLite), the desk
built with `npm run build` and served with the production Content-Security-Policy; driven with
Playwright through the real sign-in dialog.

| Check | Result |
|---|---|
| status strip shows synthetic data and no plant writes | PASS |
| all 22 pages render signed out | PASS |
| sign-in dialog works (operator) | PASS |
| advisory MPC returns a recommendation with trust gate | PASS |
| acknowledgement recorded | PASS |
| scenario S03 completes and renders | PASS |
| Load puts the earlier version into the editor | PASS |
| desk recovers by itself after the rebuild | PASS |
| roll-back saved as a new version with the earlier content | PASS |
| advisory MPC still available after the roll-back (shipped ROM kept) | PASS |
| audit trail lists acknowledgement and configuration changes | PASS |

Browser console errors, page errors and HTTP errors during the run: 0
(503 *twin initialising* answers inside the two deliberate rebuild windows are expected and
excluded).

## Not covered

Deployment on Render itself; a live OPC UA server; Firefox and Safari; concurrent load beyond one
desk session plus the background loop. CI (`.github/workflows/ci.yml`) repeats the tests with
coverage and the security checks on every push.
