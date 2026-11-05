# OT security

This document describes the security design of the software and the architecture required
before it is used with real plant data. It is not a security certification; no IEC 62443
assessment has been made.

## 1. Principles

1. **No write path.** The software contains no code that writes to a DCS, PLC, SIS or historian.
   This is enforced by tests (`tests/test_safety.py` scans the source for write calls and
   industrial-protocol write libraries) and by start-up refusal when `ALLOW_PLANT_WRITES=true`.
2. **Cloud is demo-only.** A public-cloud deployment (Render) may hold synthetic or anonymised
   data only. It must never be connected, directly or through a tunnel, to plant control networks.
3. **Push, never pull, from the plant.** Real data reach the backend through an on-premises
   read-only edge collector that pushes over HTTPS to an on-premises backend. Nothing on the
   backend opens connections into the OT network.
4. **Advice is displayed, not transmitted.** Recommendations are read by an operator who acts on
   the DCS under site procedures; the SIS and interlocks are never bypassed.

## 2. Reference architecture for a site pilot

```
 Level 3.5 / industrial DMZ                Level 4 (site IT)                 Operators
 +---------------------------+  HTTPS push +------------------------+  HTTPS  +-----------+
 | Edge collector (read-only)| ----------> | Backend container      | <------ | Browser   |
 | OPC UA client / historian |  X-Edge-    | (LIVE_MONITOR_ONLY)    |         | desk      |
 | export                    |  Token      | + Postgres             |         +-----------+
 +-------------^-------------+             +------------------------+
               | read only (OPC UA subscription / historian query)
 Level 2/1     |
 +-------------+-------------+
 | DCS / PLC / SIS           |   <- owns all control; no connection from the backend
 +---------------------------+
```

- The collector runs in the industrial DMZ with a read-only OPC UA account (or reads historian
  exports), uses `opcua_readonly.build_payload` and pushes batches to `/api/v1/data/ingest`.
- Firewall: allow collector -> backend HTTPS only; deny backend -> OT; deny internet -> backend
  except through the site reverse proxy.
- The backend runs in site IT (or a site-controlled VM) behind the site reverse proxy with TLS,
  SSO and logging.

## 3. Controls in the software

| Area | Control |
|---|---|
| Authentication | JWT bearer tokens (HS256); with `DEMO_MODE=false` the service refuses to start unless `JWT_SECRET` has at least 32 characters; 8-hour expiry (`JWT_EXPIRE_MIN`); edge ingest uses a separate service token compared in constant time |
| Authorisation | five hierarchical roles; every mutating endpoint declares its minimum role; anonymous viewer access can be disabled |
| Input validation | Pydantic schemas, unit-checked setpoints inside the equipment envelope (demo plant only), configuration JSON-schema plus engineering checks plus a trial model step |
| Uploads | size cap, CSV/XLSX only, macro workbooks refused, formula-injection cells rejected, timestamps validated |
| Transport | HTTPS on Render; HSTS, X-Content-Type-Options, X-Frame-Options DENY, Referrer-Policy, Permissions-Policy and a strict CSP on the frontend (`script-src 'self'`, tested with the built bundle) |
| CORS | explicit origin allow-list (`CORS_ORIGINS`); WebSocket origin check |
| Abuse | per-IP rate limiting; 5 WebSocket connections per IP |
| Secrets | none in the repositories; Render generates `JWT_SECRET` and `DEMO_USER_PASSWORD`; `.env` files are git-ignored |
| Audit | logins, mode changes, setpoint changes (demo), fault injections, configuration versions, tag mappings, recommendations, acknowledgements, alarm acknowledgements |
| Artefacts | pickled initial states and joblib models are loaded only from the service's own `data/` folder and only when their SHA-256 matches the sidecar written at creation (tampered files are refused and the twin warm-starts or falls back to physics); no endpoint accepts pickles or model files |
| Background jobs | queued-job cap (503 `QUEUE_FULL`), queue-wait and run-time limits, bounded in-memory history; results persisted in the database |
| Dependencies | exact pins in `requirements.txt` and `package-lock.json`; CI runs the tests, `bandit` (static analysis) and `pip-audit` on the backend, `npm audit` on the frontend. Results of the last run are in `docs/TEST_RESULTS.md` |

## 4. Before using real data

1. Deploy on premises (Docker image from this repository) with Postgres; never on a public demo service.
2. Replace demo accounts with the site identity provider (reverse-proxy SSO) and disable anonymous viewer access.
3. Set `DEMO_MODE=false`, a strong `JWT_SECRET`, `EDGE_INGEST_TOKEN`, exact `CORS_ORIGINS`.
4. Review the tag mapping and data dictionary with the plant's instrumentation team.
5. Perform a security review (threat model, penetration test) and an OT risk assessment with the site.
6. Agree data retention, backup and incident response with site IT.

## 5. Threats considered

| Threat | Mitigation |
|---|---|
| Attacker uses the twin to move the furnace | no write path exists; advice only |
| Stolen operator token | short expiry, roles limit damage to audit records and demo actions |
| Malicious upload (formula injection, macro) | parser rejects |
| Poisoned training data | models are retrained only by engineers, carry dataset hashes, and are promoted only if they beat baselines; never used for control |
| Spoofed edge data | service token, LIVE_MONITOR_ONLY gate, data-quality engine and trust gate |
| Denial of service | rate limits; job-queue cap and timeouts; advice suppression when data are stale; DCS unaffected by design |
| Tampered model or snapshot file | SHA-256 check before deserialisation; files only from `DATA_DIR` |
