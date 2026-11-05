# API reference

Generated from the running application (`python scripts/gen_api_reference.py`), API version 1.0.0. Interactive documentation: `/docs` (Swagger UI) and `/redoc` on the backend.

Conventions
- Base path `/api/v1`; JSON bodies; UTC ISO-8601 timestamps with `Z`.
- Units are in the key name (`hmt_C`, `fuel_rate_kg_tHM`) or in a `unit` field next to the value.
- Every value carries a `provenance` of MEASURED, MODEL, HYBRID or DERIVED where it matters.
- Errors: `{"error": {"code", "message", "details", "request_id"}}` with HTTP 4xx/5xx.
- Long work (simulations, studies, identification, training, NMPC) returns **202** with a job id; poll `GET /api/v1/simulations/{id}` and fetch `GET /api/v1/simulations/{id}/results`. A full queue answers **503** `QUEUE_FULL`; a job that waits or runs too long ends `FAILED` with a timeout message (`JOB_QUEUE_TIMEOUT_S`, `JOB_TIMEOUT_S`).
- Too many requests from one address: **429** `RATE_LIMITED` with `Retry-After` (`RATE_LIMIT_PER_MIN`).
- Authentication: `POST /api/v1/auth/login` returns a bearer token (JWT). Roles are hierarchical: VIEWER < OPERATOR < METALLURGIST < PROCESS_ENGINEER < ADMIN.
- There is no endpoint that writes to a plant system. Acknowledging a recommendation changes audit state only.


## alarms

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/alarms` | VIEWER | 200 | Alarms |
| POST | `/api/v1/alarms/{alarm_id}/acknowledge` | OPERATOR | 200 | Ack Alarm |

## auth

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/auth/demo-accounts` | public | 200 | Demo usernames and roles (never passwords) |
| POST | `/api/v1/auth/login` | public | 200 | Login |
| GET | `/api/v1/auth/me` | public | 200 | Me |
| POST | `/api/v1/auth/token` | public | 200 | OAuth2 password flow (form) - for the OpenAPI 'Authorize' button |

## config

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/config/plant` | VIEWER | 200 | Get Config |
| PUT | `/api/v1/config/plant` | PROCESS_ENGINEER | 202 | Put Config |
| GET | `/api/v1/config/plant/versions` | VIEWER | 200 | Config Versions |
| GET | `/api/v1/config/plant/versions/{version}` | VIEWER | 200 | Config Version |
| POST | `/api/v1/config/preset` | PROCESS_ENGINEER | 202 | Switch furnace size/capacity preset (+ overrides) |
| GET | `/api/v1/config/presets` | public | 200 | Presets |
| GET | `/api/v1/config/schema` | public | 200 | Config Schema |

## data

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| POST | `/api/v1/data/ingest` | SERVICE_INGEST (X-Edge-Token) | 200 | Read-only edge gateway push (service identity, LIVE_MONITOR_ONLY) |
| GET | `/api/v1/data/quality` | VIEWER | 200 | Per-tag quality state with reasons, score, reconciliation |
| POST | `/api/v1/data/upload` | METALLURGIST | 200 | Upload |
| GET | `/api/v1/tags` | VIEWER | 200 | Tag catalogue (data dictionary) with installation and mapping status |
| POST | `/api/v1/tags/map` | PROCESS_ENGINEER | 200 | Map Tags |

## forecasts

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| POST | `/api/v1/forecast` | VIEWER | 200 | HMT and Si forecasts: physics twin (open loop at current setpoints) + observer + hybrid ML |
| GET | `/api/v1/forecasts/live` | VIEWER | 200 | Latest cyclic hybrid forecasts (computed every 8 twin steps) |

## models

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/models/diagnostics` | VIEWER | 200 | Chronological test metrics vs baselines, coverage, drift, importance |
| GET | `/api/v1/models/registry` | VIEWER | 200 | Registry |
| POST | `/api/v1/models/train` | PROCESS_ENGINEER | 202 | Retrain hybrid models (chronological split) as a job |

## mpc

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/mpc/constraints` | VIEWER | 200 | Mpc Constraints |
| POST | `/api/v1/mpc/identify` | PROCESS_ENGINEER | 202 | Re-identify the ROM by step tests on twin clones (job) |
| GET | `/api/v1/mpc/model` | VIEWER | 200 | Identified step-response model (gains, t63 delays, coefficients) |
| POST | `/api/v1/mpc/recommend` | OPERATOR | 200 | Compute an ADVISORY recommendation (trust gate -> QP -> physics verification) |
| GET | `/api/v1/mpc/recommendations` | VIEWER | 200 | List Recs |
| GET | `/api/v1/mpc/recommendations/{rid}` | VIEWER | 200 | Get Rec |
| POST | `/api/v1/mpc/recommendations/{rid}/acknowledge` | OPERATOR | 200 | Operator decision - changes AUDIT STATE ONLY (no plant write) |
| GET | `/api/v1/mpc/safety-case` | public | 200 | Safety Case |

## reports

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/reports/balance` | VIEWER | 200 | Elemental + energy balance, streams (compositions & weights) |
| GET | `/api/v1/reports/carbon` | VIEWER | 200 | Carbon |
| GET | `/api/v1/reports/economics` | VIEWER | 200 | Economics |
| GET | `/api/v1/reports/energy` | VIEWER | 200 | Energy |
| GET | `/api/v1/reports/export` | VIEWER | 200 | Download balance / KPI / telemetry data as CSV, XLSX or JSON |

## service

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/` | public | 200 |  |

## simulations

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| POST | `/api/v1/scenarios/compare` | METALLURGIST | 202 | Compare |
| GET | `/api/v1/scenarios/library` | public | 200 | Predefined what-if scenarios (S01-S14) |
| POST | `/api/v1/scenarios/sensitivity` | METALLURGIST | 200 | Sensitivity |
| GET | `/api/v1/simulations` | VIEWER | 200 | List Simulations |
| POST | `/api/v1/simulations` | METALLURGIST | 202 | Create Simulation |
| DELETE | `/api/v1/simulations/{job_id}` | METALLURGIST | 200 | Cancel |
| GET | `/api/v1/simulations/{job_id}` | VIEWER | 200 | Get Simulation |
| GET | `/api/v1/simulations/{job_id}/results` | VIEWER | 200 | Get Results |

## system

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/system/audit` | ADMIN | 200 | Audit trail (ADMIN) |
| GET | `/api/v1/system/events` | VIEWER | 200 | Events |
| GET | `/api/v1/system/jobs` | VIEWER | 200 | Jobs |
| POST | `/api/v1/system/mode` | PROCESS_ENGINEER | 200 | Set Mode |
| GET | `/api/v1/system/modes` | public | 200 | Modes |
| POST | `/api/v1/system/speed` | PROCESS_ENGINEER | 200 | Demo simulation speed (simulated s per wall s) |
| GET | `/api/v1/system/status` | public | 200 | Mode, data source, freshness, model health |
| GET | `/health` | public | 200 | Liveness (process up) |
| GET | `/metrics` | public | 200 | Prometheus text metrics (no secrets) |
| GET | `/ready` | public | 200 | Readiness: twin initialised and DB reachable |

## telemetry

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| WS | `/api/v1/stream` | VIEWER (token query, or anonymous viewer if enabled) | 101 | Live event stream: tick / event / heartbeat / resync, with sequence numbers |
| GET | `/api/v1/stream/poll` | VIEWER | 200 | Polling fallback for the event stream |
| GET | `/api/v1/telemetry/history` | VIEWER | 200 | Telemetry History |
| GET | `/api/v1/telemetry/latest` | VIEWER | 200 | Telemetry Latest |

## thermochemistry

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/thermo/baur-glaessner` | public | 200 | Thermo Bg |
| GET | `/api/v1/thermo/ellingham` | public | 200 | Thermo Ellingham |
| GET | `/api/v1/thermo/kinetics` | public | 200 | Thermo Kinetics |
| GET | `/api/v1/thermo/reactions` | public | 200 | Reaction catalogue: dH, dG, log K vs T, kinetics forms |
| GET | `/api/v1/thermo/refractories` | public | 200 | Refractory and stave material properties used by the wall model |
| GET | `/api/v1/thermo/species` | public | 200 | Thermo Species |

## twin

| Method | Path | Minimum role | Success | Summary |
|---|---|---|---|---|
| GET | `/api/v1/twin/balances` | VIEWER | 200 | Per-step elemental and energy balance with residuals |
| DELETE | `/api/v1/twin/faults` | OPERATOR | 200 | Clear Faults |
| POST | `/api/v1/twin/faults` | OPERATOR | 200 | DEMO ONLY: inject a sensor fault into the synthetic sensor layer |
| GET | `/api/v1/twin/fields` | VIEWER | 200 | Model-computed (r,z) fields for the 3D scene |
| GET | `/api/v1/twin/geometry` | VIEWER | 200 | Furnace geometry, wall zones and model grid (for 2D/3D drawing) |
| GET | `/api/v1/twin/hearth` | VIEWER | 200 | Twin Hearth |
| GET | `/api/v1/twin/heat-balance` | VIEWER | 200 | Twin Heat Balance |
| GET | `/api/v1/twin/history` | VIEWER | 200 | Twin KPI trajectories (virtual sensors) with residuals |
| GET | `/api/v1/twin/layers` | VIEWER | 200 | Twin Layers |
| GET | `/api/v1/twin/observer` | VIEWER | 200 | Parameter EKF status + data reconciliation |
| GET | `/api/v1/twin/profiles` | VIEWER | 200 | Axial profiles (sector-averaged) incl. reaction rates and zones |
| GET | `/api/v1/twin/rist` | VIEWER | 200 | Twin Rist |
| GET | `/api/v1/twin/setpoints` | VIEWER | 200 | Get Setpoints |
| POST | `/api/v1/twin/setpoints` | OPERATOR | 200 | DEMO ONLY: change synthetic-plant setpoints (units converted, envelope enforced) |
| GET | `/api/v1/twin/state` | VIEWER | 200 | Current twin KPIs (with units/provenance), hearth, cohesive zone, top gas |
| GET | `/api/v1/twin/streams` | VIEWER | 200 | Inputs/outputs: rates, compositions, cumulative weights |
| GET | `/api/v1/twin/walls` | VIEWER | 200 | Refractory/cooling: layer temperatures, heat flux, lining proxy |

## Examples

```bash
API=https://<backend>.onrender.com
# sign in (demo account; the password is the backend's DEMO_USER_PASSWORD environment variable)
TOKEN=$(curl -s -X POST $API/api/v1/auth/login -H 'Content-Type: application/json' \
        -d '{"username":"operator","password":"'$DEMO_USER_PASSWORD'"}' | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
curl -s $API/api/v1/system/status
curl -s $API/api/v1/twin/state -H "Authorization: Bearer $TOKEN"
# advisory recommendation (audit-only, never transmitted)
curl -s -X POST $API/api/v1/mpc/recommend -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"targets":{"hmt_C":1515}}'
# what-if scenario as a job (METALLURGIST or higher)
curl -s -X POST $API/api/v1/simulations -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     -d '{"scenario_id":"S03"}'
# export the current balance with provenance
curl -s "$API/api/v1/reports/export?kind=balance&format=xlsx" -H "Authorization: Bearer $TOKEN" -o balance.xlsx
```
