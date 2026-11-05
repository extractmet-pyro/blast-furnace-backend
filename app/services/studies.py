"""Engineering studies built on the scenario engine: multi-scenario comparison, sensitivity
(ROM gains or physics perturbations), economic Pareto sweeps of the advisory MPC, the
MPC infeasibility demonstration and the sensor-fault (data-quality) scenario.
All studies run on clones; the live twin is never modified."""
from __future__ import annotations

import copy
import time
from typing import Callable, Dict, List, Optional

import numpy as np

from ..data.quality import QualityEngine
from ..data.synthetic import FaultSpec, SyntheticPlant, iso
from ..mpc.predictions import CV_DEFS, MV_DEFS, mv_step_sizes
from ..mpc.verifier import trust_gate
from ..physics import outputs as out
from . import scenarios as scn

SENS_KPIS = ["hmt_C", "hm_si_pct", "production_t_d", "fuel_rate_kg_tHM", "eta_CO_pct", "pressure_drop_kPa", "raft_C",
             "top_gas_T_C"]


def compare(base_model, specs: List[dict], duration_h: float, dt: float, progress: Optional[Callable] = None) -> dict:
    """One shared baseline + one case per scenario (identical numerics)."""
    base_spec = {"id": "BASE", "name": "baseline", "duration_h": duration_h, "changes": []}
    t0 = time.time()
    n = len(specs) + 1
    base = scn.run_simulation(base_model, base_spec, dt=dt,
                              progress=(lambda p, m: progress(0.5 * p / n, "baseline " + m)) if progress else None)
    rows, cases = [], {}
    for i, spec in enumerate(specs):
        sp = {**spec, "duration_h": duration_h}
        r = scn.run_simulation(base_model, sp, dt=dt,
                               progress=(lambda p, m, i=i: progress((i + 1 + p) / n, f"{spec['id']} {m}")) if progress else None)
        case = r["runs"]["case"]
        b = base["runs"]["baseline"]["final"]
        c = case["final"]
        delta = {k: (c[k] - b[k]) if (b and c and b.get(k) is not None and c.get(k) is not None) else None for k in scn.KPI_KEYS}
        rows.append({"id": spec["id"], "name": spec.get("name"), "status": r["status"], "delta_final": delta,
                     "final": c, "max_rel_element_residual": case["max_rel_element_residual"]})
        cases[spec["id"]] = {"trajectory": case["trajectory"], "status": case["status"]}
    return {"status": "SUCCEEDED", "baseline": {"trajectory": base["runs"]["baseline"]["trajectory"],
                                                 "final": base["runs"]["baseline"]["final"]},
            "cases": cases, "table": rows, "seconds": time.time() - t0, "duration_h": duration_h, "data_origin": "SYNTHETIC"}


def sensitivity_rom(rom) -> dict:
    """Steady-state gains and 63 % response times of the identified step-response model."""
    G = rom.gain()
    t63 = rom.t63()
    rows = []
    for j, mv in enumerate(rom.mvs):
        step = (rom.meta or {}).get("steps", {}).get(mv)
        if step is None and rom.op_point:
            step = mv_step_sizes(rom.op_point).get(mv)
        rows.append({"mv": mv, "label": MV_DEFS[mv]["label"], "unit": MV_DEFS[mv]["unit"],
                     "gains_per_unit": {c: float(G[i, j]) for i, c in enumerate(rom.cvs)},
                     "t63_h": {c: t63[c][mv] for c in rom.cvs}, "ident_step": step})
    return {"method": "ROM step-response gains (identified on the physics twin)", "rows": rows,
            "cv_units": {c: CV_DEFS[c]["unit"] for c in rom.cvs}, "identified_utc": rom.identified_utc}


def sensitivity_physics(base_model, horizon_h: float = 8.0, dt: float = 300.0, progress: Optional[Callable] = None) -> dict:
    """Tornado data: +/- one engineering step per MV, physics response after `horizon_h`."""
    steps = mv_step_sizes(base_model.op)
    n = int(horizon_h * 3600 / dt)
    ref = base_model.clone()
    ref.run(horizon_h, dt=dt)
    k0 = out.kpis(ref)
    y0 = {c: k0[c]["value"] for c in SENS_KPIS}
    rows = []
    mvs = list(MV_DEFS)
    for j, mv in enumerate(mvs):
        res = {}
        for sgn in (+1, -1):
            m = base_model.clone()
            m.op[mv] = m.op[mv] + sgn * steps[mv]
            try:
                for _ in range(n):
                    m.step(dt)
                k = out.kpis(m)
                res["plus" if sgn > 0 else "minus"] = {c: (k[c]["value"] - y0[c]) if (k[c]["value"] is not None and y0[c] is not None)
                                                       else None for c in SENS_KPIS}
            except Exception as e:
                res["plus" if sgn > 0 else "minus"] = {"error": str(e)}
            if progress:
                progress((2 * j + (1 if sgn > 0 else 2)) / (2 * len(mvs)), f"{mv} {'+' if sgn > 0 else '-'}")
        rows.append({"mv": mv, "label": MV_DEFS[mv]["label"], "step": steps[mv], "unit": MV_DEFS[mv]["unit"], **res})
    return {"status": "SUCCEEDED", "method": f"physics +/- step, response after {horizon_h} h", "baseline": y0, "rows": rows,
            "kpi_units": {c: CV_DEFS.get(c, {}).get("unit", "") for c in SENS_KPIS}}


def pareto(svc, weights=(0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0), coke_price_mult: float = 1.2,
           progress: Optional[Callable] = None) -> dict:
    """Economic weight sweep of the linear MPC (study mode, no physics verification)."""
    pts = []
    for i, w in enumerate(weights):
        rec = svc.recommend(user="study:pareto", method="linear", persist=False, econ_weight=w,
                            price_mult={"coke": coke_price_mult}, verify=False)
        if rec.get("moves"):
            obj = rec.get("objective", {}).get("after", {})
            econ = svc._econ_gradients({"coke": coke_price_mult})
            cost_h = sum(econ.get(mv, 0.0) * float(np.sum(d)) for mv, d in rec["moves"].items())
            prod = (rec["y_now"].get("production_t_d") or 1.0) / 24.0
            hmt_end = rec["predicted_optimized"]["hmt_C"][-1]
            pts.append({"econ_weight": w, "tracking_cost": obj.get("tracking"), "move_cost": obj.get("move"),
                        "delta_variable_cost_INR_h": cost_h, "delta_variable_cost_INR_tHM": cost_h / max(prod, 1e-6),
                        "hmt_end_C": hmt_end, "moves_total": {mv: float(np.sum(d)) for mv, d in rec["moves"].items()},
                        "status": rec["status"]})
        else:
            pts.append({"econ_weight": w, "status": rec["status"], "reasons": rec.get("reasons")})
        if progress:
            progress((i + 1) / len(weights), f"weight {w}")
    return {"status": "SUCCEEDED", "coke_price_mult": coke_price_mult, "points": pts,
            "note": "Study only: each point is an unverified linear-MPC solution; an operational recommendation is "
                    "always re-verified on the physics twin before release."}


def infeasible_demo(svc) -> dict:
    """Hard bed-pressure-drop limit set BELOW the current value -> optimiser must report infeasible."""
    k = svc.kpis()
    dp = k["pressure_drop_kPa"]["value"]
    lim = dp - 25.0
    rec = svc.recommend(user="study:infeasible", method="linear", persist=True,
                        override_hard={"pressure_drop_kPa": {"high_hard": lim, "high_alarm": lim - 5.0}})
    return {"status": "SUCCEEDED", "current_dP_kPa": dp, "imposed_hard_max_kPa": lim, "recommendation_status": rec["status"],
            "reasons": rec.get("reasons"), "recommendation_id": rec["id"],
            "expected": "NO_FEASIBLE_SOLUTION (hard limits are never silently relaxed)"}


def sensor_scenario(plant_model, twin_model, cfg: dict, faults: List[dict], duration_h: float, dt: float = 120.0,
                    progress: Optional[Callable] = None) -> dict:
    """Synthetic plant clone with injected instrument faults -> quality engine -> trust gate timeline."""
    pm = plant_model.clone()
    plant = SyntheticPlant(cfg, seed=23, model=pm)
    h0 = pm.t / 3600.0
    plant.faults = [FaultSpec(f["kind"], f["tag"], float(f.get("magnitude", 0.0)), h0 + float(f.get("start_h", 0.0)),
                              h0 + float(f.get("end_h", 1e6))) for f in faults]
    qe = QualityEngine()
    if pm.n_r == 1:
        qe.not_provided = {t: "not provided by the 1-D demo plant" for t in ("ABP_T_CENTER", "ABP_T_WALL")}
    n = int(duration_h * 3600 / dt)
    timeline, flagged = [], {}
    for i in range(n):
        readings = plant.step(dt)
        now = iso(pm.t)
        assessed = qe.assess([r.as_dict() for r in readings], now)
        sc = qe.score(now)
        gate = trust_gate({"mode": "DEMO_SIMULATION", "dq_score": sc["score"], "critical_ok": sc["critical_ok"],
                           "fresh": True, "balance_ok": True, "model_ok": True, "observer_ok": True, "critical_alarm": False})
        snap = {r["tag"]: r["quality"] for r in qe.snapshot(now)}
        for tag, q in snap.items():
            if q in ("SUSPECT", "BAD", "MISSING") and any(f["tag"] == tag for f in faults):
                flagged.setdefault(tag, {"first_flag_h": round(pm.t / 3600.0 - h0, 2), "quality": q})
        if i % 5 == 0 or i == n - 1:
            vals = {r["tag"]: r["value"] for r in assessed}
            timeline.append({"t_h": round(pm.t / 3600.0 - h0, 3), "dq_score": sc["score"], "critical_ok": sc["critical_ok"],
                             "advice": "ALLOWED" if gate["passed"] else "SUPPRESSED", "failed_checks": gate["failed"],
                             **{f"q_{f['tag']}": snap.get(f["tag"]) for f in faults},
                             **{f"v_{f['tag']}": vals.get(f["tag"]) for f in faults}})
        if progress and i % 10 == 0:
            progress(i / n, f"{i * dt / 3600:.1f} h")
    return {"status": "SUCCEEDED", "faults": faults, "flagged": flagged, "timeline": timeline,
            "suppressed_fraction": float(np.mean([t["advice"] == "SUPPRESSED" for t in timeline])) if timeline else None,
            "note": "Faults are injected in the synthetic sensor layer only; raw values are preserved and flagged."}
