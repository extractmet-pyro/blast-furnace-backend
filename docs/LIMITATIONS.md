# Limitations

Read this before showing the software to anyone as more than a demonstration.

## Status

- **Not connected to any physical furnace.** All shipped data are synthetic ("Industry-X").
- **Not an approved control system.** The MPC is advisory; there is no plant-write path, and the
  software has not been through HAZOP, LOPA, SIL determination or any site acceptance test.
- **No standards compliance is claimed** (IEC 62443, IEC 61511, ISA-101, ISA-18.2 and similar
  were used as design references only; no assessment has been made).
- Presets are illustrative designs, not any specific plant. Calibration is provisional.

## Physics model

- 1-D axial fast model (one radial sector). Radial phenomena (centre/wall gas distribution,
  burden distribution effects, radial cohesive-zone shape) need the hi-fi 2-D mode, which runs
  only as a background job with a warm start inside the job and has had far less testing.
- Kinetic constants, heat-transfer coefficients, softening/melting temperatures and burden
  properties are provisional calibrations to typical operating values, not plant-identified.
- The cohesive zone is a temperature-window proxy (low confidence) and the raceway depth a
  provisional correlation. RAFT is the conventional estimate, never a measurement.
- At constant inputs the model shows slow oscillations (a few % in production, about 10 degC in
  HMT over tens of hours) from the layer structure, cohesive zone and casting cycle.
- 4-h specific consumptions carry a sawtooth of about +/-1 % from discrete charging at step
  boundaries (charge times are quantised to the 120 s step).
- The short-horizon response of HMT to the coke rate is weak, so the coke lever is opt-in for
  the optimiser. PCI at constant oxygen cools in this calibration on a horizon of hours, and the
  apparent coke replacement ratio at 14 h is about 0.5 (plants report 0.8-0.9 at steady state).
- Scenario S14 (coke -8 %) leaves the validated envelope; its late-time details are unreliable.
- The wall-heat multiplier is weakly identifiable from stave heat alone; the observer bounds and
  mean reversion keep it plausible, not proven.
- Slag liquidus is an estimate; slag viscosity uses the Urbain model without plant calibration.

## Estimation, forecasting and control

- Forecast models are trained on synthetic data and labelled NOT_APPROVED_FOR_PLANT_USE. On the
  shipped data the ML residual does not beat the physics + observer baseline, so no model is
  promoted. Si interval coverage on the test block is 0.80 (nominal 0.95).
- The linear MPC uses a ROM identified at one operating point; it is trusted only near that
  point (trust gate plus physics verification). NMPC is slow and runs only as a job.
- Si and HMT are nearly collinear in every lever's response; Si is zone-controlled by default.

## Software and deployment

- The job manager is an in-process thread pool. A Redis/RQ worker tier is **not implemented**;
  a restart interrupts running jobs (they are then reported FAILED, *interrupted by a service
  restart*). Finished jobs and their results are kept in the database, so they survive a
  restart only where the database does (not on the free plan's temporary disk).
- On the Render free plan the service sleeps after 15 minutes without traffic (about a minute to
  wake), has an ephemeral filesystem (SQLite history, configuration versions and audit records
  are lost on redeploy or restart unless `DATABASE_URL` points to Postgres) and limited CPU.
- The OPC UA collector (`app/data/opcua_readonly.py`) has unit tests for its read-only design but
  has **not** been tested against a live OPC UA server.
- Demo accounts share one password from `DEMO_USER_PASSWORD`; there is no SSO, no MFA, no
  password reset flow and no per-user lockout beyond rate limiting. Production use needs the
  site's identity provider.
- Browser support tested: current Chromium (automated). Firefox and Safari were not tested.
- Mobile layout works for monitoring; the 3-D view and wide tables are designed for desktop.
