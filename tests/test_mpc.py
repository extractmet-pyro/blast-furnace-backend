"""Advisory MPC: feasible improving solution, hard limits, rate limits, infeasibility, suppression,
audit-only acknowledgement."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest

from app.mpc.constraints import build_constraints
from app.mpc.optimizer import MPCProblem, solve_linear_mpc
from app.mpc.predictions import DMCPredictor, StepResponseModel, read_cvs
from app.mpc.recommendations import acknowledge, build_recommendation
from app.mpc.verifier import trust_gate, verify_on_physics

ROM = Path(__file__).resolve().parents[1] / "data" / "demo" / "rom_large_4000.json"


@pytest.fixture(scope="module")
def rom():
    d = json.loads(ROM.read_text())
    S = np.array([[[d["step_response"][c][mv][k] for mv in d["mvs"]] for c in d["cvs"]] for k in range(d["N"] + 1)])
    return StepResponseModel(d["Ts_min"] * 60, d["N"], d["mvs"], d["cvs"], S, d["op_point"], d["identified_utc"])


def _solve(rom, model, targets=None, override=None, availability=None):
    cfg = copy.deepcopy(model.cfg)
    for c in cfg["constraints"]:
        if override and c["name"] in override:
            c.update(override[c["name"]])
    cs = build_constraints(cfg, model.op, availability)
    y = read_cvs(model)
    u = {mv: float(model.op[mv]) for mv in rom.mvs}
    tgt = {"hmt_C": y["hmt_C"] - 20.0, "hm_si_pct": y["hm_si_pct"] - 0.06, "fuel_rate_kg_tHM": y["fuel_rate_kg_tHM"]}
    tgt.update(targets or {})
    return solve_linear_mpc(rom, DMCPredictor(rom), y, u, cs, MPCProblem(targets=tgt)), cs, u


def test_rom_gains_have_physical_signs(rom):
    G = rom.gain()
    g = lambda cv, mv: G[rom.cvs.index(cv), rom.mvs.index(mv)]
    assert g("raft_C", "blast_temp_C") > 0 and g("raft_C", "blast_moisture_g_Nm3") < 0 and g("raft_C", "o2_enrichment_pct") > 0
    assert g("hm_si_pct", "blast_temp_C") > 0 and g("hm_si_pct", "blast_moisture_g_Nm3") < 0
    assert g("production_t_d", "o2_enrichment_pct") > 0 and g("production_t_d", "blast_volume_Nm3_min") > 0
    assert g("fuel_rate_kg_tHM", "coke_batch_t") > 0


def test_feasible_solution_improves_objective(rom, model):
    res, cs, u = _solve(rom, model)
    assert res.status in ("OPTIMAL", "OPTIMAL_INACCURATE"), res.message
    assert res.objective["after"]["tracking"] < res.objective["before"]["tracking"]


def test_mv_bounds_and_rate_limits_honoured(rom, model):
    res, cs, u = _solve(rom, model, targets={"hmt_C": 1600.0, "hm_si_pct": 1.0})
    for mv, traj in res.u_traj.items():
        lim = cs.mvs[mv]
        assert all(lim.lo - 1e-6 <= v <= lim.hi + 1e-6 for v in traj), mv
    blocks = [0, 2, 4, 8]
    for mv, moves in res.moves.items():
        lim = cs.mvs[mv]
        for i, d in enumerate(moves):
            span = (blocks[i + 1] - blocks[i]) if i + 1 < len(blocks) else 4    # last block: 4 samples (1 h)
            assert abs(d) <= lim.rate_per_15min * max(span, 1) + 1e-6, (mv, i, d)


def test_unavailable_mv_is_not_moved(rom, model):
    res, cs, u = _solve(rom, model, availability={"blast_temp_C": False})
    assert all(abs(x) < 1e-9 for x in res.moves.get("blast_temp_C", [0.0]))
    assert "coke_batch_t" not in res.moves or all(abs(x) < 1e-9 for x in res.moves["coke_batch_t"])   # opt-in lever


def test_infeasible_hard_limit_reports_no_feasible_solution(rom, model):
    dp = read_cvs(model)["pressure_drop_kPa"]
    res, cs, u = _solve(rom, model, override={"pressure_drop_kPa": {"high_hard": dp - 30.0, "high_alarm": dp - 35.0, "soft": False}})
    assert res.status == "NO_FEASIBLE_SOLUTION", res.status


@pytest.mark.parametrize("ctx_change,failed", [({"fresh": False}, "data_fresh"), ({"critical_ok": False}, "critical_tags_good"),
                                               ({"model_ok": False}, "model_in_envelope"), ({"balance_ok": False}, "model_balance_closed"),
                                               ({"dq_score": 40.0}, "data_quality_score>=70")])
def test_trust_gate_suppresses(ctx_change, failed):
    ctx = {"mode": "DEMO_SIMULATION", "dq_score": 95.0, "critical_ok": True, "fresh": True, "balance_ok": True, "model_ok": True,
           "observer_ok": True, "critical_alarm": False}
    ctx.update(ctx_change)
    g = trust_gate(ctx)
    assert not g["passed"] and failed in g["failed"]


def test_physics_verifier_runs_and_checks_limits(rom, model):
    res, cs, u = _solve(rom, model)
    ver = verify_on_physics(model, res.moves, [0, 2, 4, 8], rom.Ts_s, cs, horizon_h=2.0, rom_pred=res.y_opt, cvs=rom.cvs)
    assert "passed" in ver and "issues" in ver and ver["max_rel_element_residual"] < 1e-6


def test_acknowledgement_changes_audit_state_only(model):
    rec = build_recommendation("PROPOSED", "DEMO_SIMULATION", {"moves": {"blast_temp_C": [10.0]}}, [])
    op_before = copy.deepcopy(model.op)
    acknowledge(rec, "op1", "OPERATOR", "ACKNOWLEDGED", "ok")
    assert rec["status"] == "ACKNOWLEDGED" and rec["audit"][-1]["user"] == "op1"
    assert model.op == op_before
    assert rec["plant_write"].upper().startswith("NONE")
