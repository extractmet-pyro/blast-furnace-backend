"""API contract: documented endpoints exist, structured errors, auth/roles, CORS, upload cap,
job lifecycle, stream sequence & resync, provenance on exports, no silent zeros."""
import io
import json
import time

import pytest

from app.services.twin_service import cfg_hash

DOCUMENTED = ["/api/v1/system/status", "/api/v1/system/mode", "/api/v1/auth/login", "/api/v1/config/plant", "/api/v1/tags",
              "/api/v1/tags/map", "/api/v1/data/upload", "/api/v1/data/quality", "/api/v1/data/ingest", "/api/v1/telemetry/latest",
              "/api/v1/telemetry/history", "/api/v1/twin/state", "/api/v1/twin/fields", "/api/v1/twin/balances",
              "/api/v1/twin/profiles", "/api/v1/simulations", "/api/v1/simulations/{job_id}", "/api/v1/simulations/{job_id}/results",
              "/api/v1/scenarios/compare", "/api/v1/forecast", "/api/v1/mpc/recommend", "/api/v1/mpc/recommendations/{rid}",
              "/api/v1/mpc/recommendations/{rid}/acknowledge", "/api/v1/alarms", "/api/v1/reports/balance", "/api/v1/reports/export",
              "/api/v1/models/registry", "/health", "/ready", "/metrics"]


def test_openapi_lists_documented_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    missing = [p for p in DOCUMENTED if p not in paths]
    assert not missing, missing


def test_status_reports_freshness_and_health(client):
    st = client.get("/api/v1/system/status").json()
    for k in ("mode", "data_origin", "data_fresh", "model_health", "health_reasons", "last_good_data_utc", "write_disabled",
              "balance_status", "label"):
        assert k in st
    assert st["write_disabled"] is True and st["data_origin"] == "SYNTHETIC"


def test_validation_error_is_structured(client, auth):
    r = client.post("/api/v1/forecast", json={"horizons_min": [5]}, headers=auth["viewer"])
    assert r.status_code == 422
    e = r.json()["error"]
    assert e["code"] and e["message"] and "request_id" in e


def test_unauthenticated_write_rejected(client):
    r = client.post("/api/v1/simulations", json={"scenario_id": "S03"})
    assert r.status_code in (401, 403)


def test_role_enforcement(client, auth):
    assert client.post("/api/v1/simulations", json={"scenario_id": "S03"}, headers=auth["viewer"]).status_code == 403
    assert client.post("/api/v1/system/mode", json={"mode": "OFFLINE_SCENARIO"}, headers=auth["operator"]).status_code == 403
    assert client.get("/api/v1/system/audit", headers=auth["engineer"]).status_code == 403
    assert client.get("/api/v1/system/audit", headers=auth["admin"]).status_code == 200


def test_bad_login_and_bad_token(client):
    assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401


def test_cors_allowed_and_disallowed_origin(client):
    ok = client.options("/api/v1/system/status", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/v1/system/status", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert bad.headers.get("access-control-allow-origin") is None


def test_security_headers(client):
    h = client.get("/health").headers
    assert h.get("x-content-type-options") == "nosniff" and "x-frame-options" in h


def test_setpoint_outside_envelope_rejected_and_units_converted(client, auth):
    r = client.post("/api/v1/twin/setpoints", json={"changes": {"blast_temp_C": 1500}}, headers=auth["operator"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "OUTSIDE_ENVELOPE"
    r = client.post("/api/v1/twin/setpoints", json={"changes": {"blast_temp_C": {"value": 1453.15, "unit": "K"}}},
                    headers=auth["operator"])
    assert r.status_code == 200 and abs(r.json()["setpoints"]["blast_temp_C"] - 1180.0) < 1e-6
    r = client.post("/api/v1/twin/setpoints", content=b'{"changes": {"blast_temp_C": NaN}}',
                    headers={**auth["operator"], "Content-Type": "application/json"})
    assert r.status_code == 422                          # non-finite numbers are rejected, never propagated


def test_upload_size_cap(client, auth, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0.001)
    big = ("t_utc,BL_FLOW\n" + "".join(f"2026-11-01T00:{i // 60:02d}:{i % 60:02d}Z,6600\n" for i in range(3000))).encode()
    r = client.post("/api/v1/data/upload", files={"file": ("h.csv", io.BytesIO(big), "text/csv")}, headers=auth["metallurgist"])
    assert r.status_code == 413


def test_upload_formula_injection_rejected(client, auth):
    bad = b"t_utc,BL_FLOW\n2026-11-01T00:00:00Z,=cmd|' /C calc'!A0\n"
    r = client.post("/api/v1/data/upload", files={"file": ("h.csv", io.BytesIO(bad), "text/csv")}, headers=auth["metallurgist"])
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_UPLOAD"


def test_missing_sensor_never_zero(client, auth):
    tags = client.get("/api/v1/telemetry/latest", headers=auth["viewer"]).json()["tags"]
    for t in tags:
        if t["quality"] in ("MISSING", "UNAVAILABLE"):
            assert t.get("value") is None, t


def test_simulation_job_lifecycle(client, auth):
    r = client.post("/api/v1/simulations", json={"scenario_id": "S03", "duration_h": 2, "dt_s": 300},
                    headers={**auth["metallurgist"], "Idempotency-Key": "lifecycle-1"})
    assert r.status_code == 202
    jid = r.json()["id"]
    r2 = client.post("/api/v1/simulations", json={"scenario_id": "S03", "duration_h": 2, "dt_s": 300},
                     headers={**auth["metallurgist"], "Idempotency-Key": "lifecycle-1"})
    assert r2.json()["id"] == jid                         # idempotent
    early = client.get(f"/api/v1/simulations/{jid}/results", headers=auth["viewer"])
    assert early.status_code in (200, 409)
    for _ in range(240):
        j = client.get(f"/api/v1/simulations/{jid}", headers=auth["viewer"]).json()
        if j["status"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(0.5)
    assert j["status"] == "SUCCEEDED", j
    res = client.get(f"/api/v1/simulations/{jid}/results", headers=auth["viewer"]).json()
    assert res["runs"]["case"]["max_rel_element_residual"] < 1e-6
    assert "cumulative_t" in res["runs"]["case"] and res["runs"]["case"]["trajectory"]["t_h"]


def test_unknown_job_is_404(client, auth):
    assert client.get("/api/v1/simulations/doesnotexist", headers=auth["viewer"]).status_code == 404


def test_stream_poll_sequence_and_resync(client, auth):
    a = client.get("/api/v1/stream/poll?since_seq=0", headers=auth["viewer"]).json()
    assert a["latest_seq"] >= a["oldest_seq"]
    b = client.get(f"/api/v1/stream/poll?since_seq={a['latest_seq']}", headers=auth["viewer"]).json()
    assert all(e["seq"] > a["latest_seq"] for e in b["events"])
    if a["oldest_seq"] > 2:
        c = client.get("/api/v1/stream/poll?since_seq=1", headers=auth["viewer"]).json()
        assert c["resync_required"] is True


def test_websocket_hello_and_resync(client, auth):
    tok = auth["viewer"]["Authorization"].split()[1]
    with client.websocket_connect(f"/api/v1/stream?token={tok}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and "seq" in hello
        ws.send_json({"type": "ping"})
        got = [ws.receive_json() for _ in range(3)]
        assert any(m.get("type") in ("pong", "tick", "event", "heartbeat") for m in got)


def test_websocket_rejects_bad_token(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/stream?token=bad") as ws:
            ws.receive_json()


def test_export_carries_provenance(client, auth):
    r = client.get("/api/v1/reports/export?kind=balance&format=csv", headers=auth["viewer"])
    head = r.text.splitlines()[:12]
    joined = "\n".join(head)
    for key in ("config_version", "config_sha256", "model_version", "data_time_utc", "label"):
        assert key in joined, key


def test_forecast_contract_and_eligibility(client, auth):
    r = client.post("/api/v1/forecast", json={"horizons_min": [60, 120]}, headers=auth["viewer"])
    assert r.status_code == 200
    f = r.json()
    p = f["horizons"][0]["predictions"]["hot_metal_temperature"]
    assert p["unit"] == "degC" and p["status"] in ("HYBRID", "PHYSICS_PLUS_OBSERVER", "OOD_FALLBACK_TO_PHYSICS",
                                               "PHYSICS_PLUS_OBSERVER_ML_SHADOW")
    assert p["lower95"] < p["value"] < p["upper95"]
    assert isinstance(f["recommendation_eligible"], bool)


def test_mpc_recommend_returns_advisory(client, auth):
    r = client.post("/api/v1/mpc/recommend", json={}, headers=auth["operator"])
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["status"] in ("PROPOSED", "ADVICE_SUPPRESSED", "NO_FEASIBLE_SOLUTION")
    assert "NONE" in rec["plant_write"].upper()
    if rec["status"] == "PROPOSED":
        a = client.post(f"/api/v1/mpc/recommendations/{rec['id']}/acknowledge", json={"decision": "ACKNOWLEDGED", "comment": "t"},
                        headers=auth["operator"])
        assert a.status_code == 200 and "NONE" in a.json()["plant_write"].upper()


def test_edge_ingest_requires_token_and_live_mode(client):
    r = client.post("/api/v1/data/ingest", json={"readings": [{"tag": "BL_FLOW", "value": 6600, "ts_utc": "2026-11-04T00:00:00Z"}]})
    assert r.status_code == 401
    r = client.post("/api/v1/data/ingest", json={"readings": [{"tag": "BL_FLOW", "value": 6600, "ts_utc": "2026-11-04T00:00:00Z"}]},
                    headers={"X-Edge-Token": "edge-test-token-0123456789"})
    assert r.status_code == 409               # demo mode: edge data refused (no mixing of sources)


def test_metrics_endpoint(client):
    t = client.get("/metrics").text
    assert "bf_plant_writes_enabled 0" in t and "bf_twin_ready" in t


def test_refractory_library_contract(client):
    r = client.get("/api/v1/thermo/refractories")
    assert r.status_code == 200
    mats = {m["key"]: m for m in r.json()["materials"]}
    assert mats["copper_stave"]["k_W_mK"] > mats["carbon_micropore"]["k_W_mK"] > mats["chamotte"]["k_W_mK"]
    assert all(m["diffusivity_m2_s"] > 0 for m in mats.values())


def test_config_edit_round_trip_with_optimistic_concurrency(client, auth):
    """A targets-only edit keeps the physics hash, so the shipped state reloads quickly; a stale
    expected_version is refused with 409; the original configuration is restored afterwards."""
    import time
    def wait_running():
        for _ in range(240):
            if client.get("/api/v1/system/status").json()["state"] == "RUNNING":
                return
            time.sleep(0.5)
        raise AssertionError("twin did not return to RUNNING")
    g = client.get("/api/v1/config/plant", headers=auth["viewer"]).json()
    cfg, v0 = g["config"], g["version"]
    original = cfg["targets"]["hmt_C"]
    cfg["targets"]["hmt_C"] = [original[0] - 5.0, original[1]]
    r = client.put("/api/v1/config/plant", json={"config": cfg, "expected_version": v0, "comment": "test"}, headers=auth["engineer"])
    assert r.status_code == 202, r.text
    v1 = r.json()["version"]
    assert v1 > v0
    wait_running()
    stale = client.put("/api/v1/config/plant", json={"config": cfg, "expected_version": v0, "comment": "stale"}, headers=auth["engineer"])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert client.put("/api/v1/config/plant", json={"config": cfg, "expected_version": v1}, headers=auth["operator"]).status_code == 403
    cfg["targets"]["hmt_C"] = original
    r = client.put("/api/v1/config/plant", json={"config": cfg, "expected_version": v1, "comment": "restore"}, headers=auth["engineer"])
    assert r.status_code == 202
    wait_running()
    assert client.get("/api/v1/config/plant", headers=auth["viewer"]).json()["config"]["targets"]["hmt_C"] == original
    # every stored version can be fetched in full (for export or roll-back); versions are append-only
    old = client.get(f"/api/v1/config/plant/versions/{v1}", headers=auth["viewer"])
    assert old.status_code == 200 and old.json()["config"]["targets"]["hmt_C"] == [original[0] - 5.0, original[1]]
    assert old.json()["sha256"] == cfg_hash(old.json()["config"])
    assert client.get("/api/v1/config/plant/versions/999999", headers=auth["viewer"]).status_code == 404


def test_mpc_si_is_zone_controlled_by_default(client, auth):
    # the preceding config round trip rebuilds the twin: wait until fresh data re-open the trust gate
    for _ in range(90):
        r = client.post("/api/v1/mpc/recommend", json={"targets": {"hmt_C": 1515.0}}, headers=auth["operator"])
        assert r.status_code in (200, 503), r.text
        if r.status_code == 200 and r.json()["status"] != "ADVICE_SUPPRESSED":
            break
        time.sleep(1.0)
    rec = r.json()
    lo, hi = client.get("/api/v1/config/plant", headers=auth["viewer"]).json()["config"]["targets"]["si_pct"]
    assert rec["status"] != "ADVICE_SUPPRESSED", rec.get("reasons")
    si_now, si_t = rec["y_now"]["hm_si_pct"], rec["targets"]["hm_si_pct"]
    assert rec["target_basis"]["hm_si_pct"].startswith("zone")
    assert si_t == pytest.approx(min(max(si_now, lo), hi))
