"""End-to-end what-if scenarios on twin clones: conservation, physical direction, sensor scenario,
MPC infeasibility study."""
import copy
import time

import pytest

from app.services import scenarios as scn
from app.services import studies


def _spec(sid, hours):
    s = copy.deepcopy(scn.LIB_INDEX[sid])
    s["duration_h"] = hours
    return s


def test_blast_temperature_scenario_conserves_and_heats(base_model):
    r = scn.run_simulation(base_model, _spec("S03", 6.0), dt=300.0)
    case = r["runs"]["case"]
    assert case["max_rel_element_residual"] < 1e-6
    assert r["delta_final"]["raft_C"] > 15.0 and r["delta_final"]["hm_si_pct"] > 0.0


def test_hbi_scenario_runs_and_reports_compositions(base_model):
    r = scn.run_simulation(base_model, _spec("S05", 6.0), dt=300.0)
    case = r["runs"]["case"]
    assert case["status"] == "COMPLETED" and case["max_rel_element_residual"] < 1e-6
    tr = case["trajectory"]
    for key in ("hm_c_pct", "slag_FeO_pct", "top_CO_pct", "top_CO2_pct", "top_H2_pct"):
        assert key in tr and any(v is not None for v in tr[key])
    assert any(k.startswith("in.") for k in case["cumulative_t"]) and any(k.startswith("out.") for k in case["cumulative_t"])


def test_sensor_fault_scenario_flags_and_suppresses(base_model):
    faults = [{"kind": "STUCK", "tag": "TG_TEMP", "start_h": 0.0, "end_h": 3.0},
              {"kind": "DROPOUT", "tag": "BL_FLOW", "start_h": 0.5, "end_h": 2.5}]
    r = studies.sensor_scenario(base_model, base_model, base_model.cfg, faults, 3.0, dt=240.0)
    assert "TG_TEMP" in r["flagged"] or "BL_FLOW" in r["flagged"]
    assert r["suppressed_fraction"] > 0.2          # advice suppressed while critical tags are not GOOD


def test_infeasible_study_via_api(client, auth):
    r = client.post("/api/v1/simulations", json={"scenario_id": "S13"}, headers=auth["metallurgist"])
    assert r.status_code == 202
    jid = r.json()["id"]
    for _ in range(240):
        j = client.get(f"/api/v1/simulations/{jid}", headers=auth["viewer"]).json()
        if j["status"] not in ("PENDING", "RUNNING"):
            break
        time.sleep(0.5)
    res = client.get(f"/api/v1/simulations/{jid}/results", headers=auth["viewer"]).json()
    assert res["recommendation_status"] in ("NO_FEASIBLE_SOLUTION", "ADVICE_SUPPRESSED")
