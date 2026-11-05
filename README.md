# Blast Furnace Digital Twin - backend

Extractmet Private Limited. FastAPI service with a first-principles blast-furnace ironmaking
twin (liquid hot metal), process simulator, state/parameter observer, hybrid forecasts and an
**advisory** model-predictive controller.

> Demonstration build on **synthetic** data ("Industry-X"). It is not connected to any
> physical furnace and is not an approved control system. There is no plant-write path:
> recommendations are displayed for an operator and acknowledgements are audit records only.

The operator desk is in the companion repository `blast-furnace-frontend`.

## What it does

- **Physics twin** (`app/physics`): axial finite-volume model from stockline to hearth with exact
  element and energy conservation every step; 45-reaction network (indirect reduction by CO and
  H2, solution loss, water-gas and shift, carbonates, volatiles, dripping-zone and hearth
  slag-metal reactions, raceway); absolute-enthalpy thermochemistry; Ergun layered-bed
  hydraulics; raceway with PCI, oxygen, moisture, natural gas and hydrogen; cohesive zone,
  dripping zone and deadman; hearth pools with casting through tapholes; transient multi-layer
  refractory conduction with water-side convection and shell radiation; four furnace-size presets.
- **Synthetic plant** with sensor noise, lags, lab delays and injectable instrument faults.
- **Data quality, reconciliation and observer**: per-tag quality with reasons, WLS N/C/O
  reconciliation, parameter EKF with bounded, mean-reverting parameters.
- **Forecasts**: twin run forward plus observer; hybrid ML residual models with OOD gate,
  conformal intervals, baselines and a promotion rule.
- **Advisory MPC**: identified step-response model, OSQP QP with hard equipment limits and rate
  limits, optional physics NMPC, trust gate, independent physics verification.
- **Studies**: 19 what-if scenarios, comparisons, sensitivities, cost Pareto, infeasibility and
  instrument-fault demonstrations; all inputs/outputs, compositions, weights and trajectories.
- **API**: REST + WebSocket stream, JWT roles, audit trail, exports with provenance.

## Quick start (local)

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export DEMO_USER_PASSWORD=choose-a-long-password CORS_ORIGINS=http://localhost:5173
uvicorn app.main:app --reload --port 8000
# http://localhost:8000/docs   http://localhost:8000/api/v1/system/status
pytest -q                                              # about 8 min on 2 CPUs
```

Demo accounts (`viewer`, `operator`, `metallurgist`, `engineer`, `admin`) use
`DEMO_USER_PASSWORD`. Shipped artefacts in `data/` (initial states, identified ROM, 12-day
synthetic history, trained demo models) let the service start in seconds; regenerate them with
`python scripts/seed_demo.py --all`.

## Deploy on Render

`render.yaml` is a Render Blueprint: **New > Blueprint**, pick this repository, set
`CORS_ORIGINS` to the frontend URL. Full steps: [docs/BLAST_FURNACE_DEPLOYMENT_GUIDE.md](docs/BLAST_FURNACE_DEPLOYMENT_GUIDE.md).

## Repository layout

```
app/
  api/v1/        REST and WebSocket endpoints (status, telemetry, physics, simulations, forecasts, control, reports)
  core/          settings, security (JWT, roles), safety policy and modes, errors, units
  physics/       twin: integrator, reactions, thermodynamics, species, raceway, dripping, hearth, walls, burden, outputs
  estimation/    parameter EKF observer, WLS reconciliation
  mpc/           step-response ROM, constraints, QP/NMPC optimiser, verifier, recommendation lifecycle
  ml/            features, hybrid forecaster, baselines, drift, training, registry
  data/          synthetic plant, data quality, tag catalogue and mapping, CSV/XLSX import, read-only OPC UA collector
  services/      twin service (live loop), scenarios, studies, alarms, economics, reporting, jobs
  db/            SQLAlchemy models and session
config/          JSON schema and example plant configuration (YAML)
data/demo        shipped initial states, ROM, synthetic history      data/models  demo forecast models
data/examples    example outputs: preset state table, scenario trajectories, streams
docs/            documentation, diagrams, figures, screenshots
scripts/         seed_demo, make_doc_figures, gen_api_reference, gen_data_dictionary
tests/           pytest suite (verification, physics, data quality, observer, ML, MPC, API, safety)
alembic/         database migrations
```

## Documentation

| Document | Content |
|---|---|
| [BLAST_FURNACE_DEPLOYMENT_GUIDE.md](docs/BLAST_FURNACE_DEPLOYMENT_GUIDE.md) | GitHub and Render deployment, local run, troubleshooting |
| [SYSTEM_ARCHITECTURE.md](docs/SYSTEM_ARCHITECTURE.md) | components, data flow, observer, ML governance, modes |
| [MODEL_EQUATIONS.md](docs/MODEL_EQUATIONS.md) | balances, thermochemistry, kinetics, heat and mass transfer, hearth, walls |
| [MODEL_VALIDATION.md](docs/MODEL_VALIDATION.md) | verification, plausibility, scenarios, observer, forecasts, MPC; plant validation plan |
| [COMMISSIONING_PLAN.md](docs/COMMISSIONING_PLAN.md) | stage gates G0-G4, calibration protocol, acceptance metrics, qualification matrix, SOP, roll-back, incidents, sign-offs |
| [MPC_CONTROL_SPECIFICATION.md](docs/MPC_CONTROL_SPECIFICATION.md) | variables, model, QP, trust gate, verification, lifecycle |
| [DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) + [tag_mapping_template.csv](docs/tag_mapping_template.csv) | tag catalogue and mapping |
| [API_REFERENCE.md](docs/API_REFERENCE.md) | every endpoint with its minimum role |
| [ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | environment, accounts, configuration, models, monitoring |
| [OT_SECURITY.md](docs/OT_SECURITY.md) | security design and site architecture |
| [LIMITATIONS.md](docs/LIMITATIONS.md) | what this build does not do |
| [TEST_RESULTS.md](docs/TEST_RESULTS.md) | test commands and captured results |
| [CHANGELOG.md](CHANGELOG.md), [LICENSE](LICENSE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) | |

The user guide with screenshots and the manual acceptance checklist are in the frontend
repository (`docs/USER_GUIDE.md`, `docs/UAT_CHECKLIST.md`).
