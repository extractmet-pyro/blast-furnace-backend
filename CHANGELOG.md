# Changelog

## 1.0.0 - 2026-11-05

First demonstration release (synthetic data, advisory only).

- Physics twin of the blast furnace (fast 1-D and hi-fi 2-D fidelity) with exact element and
  energy conservation, four furnace-size presets, editable geometry, operating point and
  refractory lining.
- Synthetic plant with sensor noise, lags, lab delays and injectable instrument faults.
- Data quality engine, WLS reconciliation, parameter EKF observer.
- Forecasts (physics + observer, hybrid ML in shadow unless promoted), model registry with
  provenance and approval labels.
- Advisory MPC (ROM + OSQP, optional physics NMPC), trust gate, physics verification,
  acknowledgement as audit record only.
- 19 what-if scenarios, comparisons, sensitivities, cost Pareto.
- Operator desk with 22 displays, 3-D furnace, to-scale schematic, day/night themes.
- Render blueprints for both repositories; documentation set; automated tests.
- Background jobs with queue cap, queue-wait and run-time limits; job rows and results persisted
  (parent row flushed first) and served from the database once pruned from memory.
- SHA-256 integrity check before any pickled snapshot or model artefact is deserialised.
- Persistence failures are counted (`bf_persistence_errors_total`) and logged, never silent.
- CI: tests with coverage, `bandit` static analysis and `pip-audit` dependency check.
- `GET /api/v1/config/plant/versions/{version}` for export and roll-back of a stored configuration.
- Physics hash insensitive to number formatting (a configuration saved from the browser keeps the
  shipped warm state and ROM when its physics is unchanged).
- Commissioning plan (stage gates G0-G4, calibration protocol, qualification matrix, SOP, roll-back).
