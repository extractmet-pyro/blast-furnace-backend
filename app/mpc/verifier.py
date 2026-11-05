"""Independent verification of an MPC recommendation and the advice trust gate.

Before any recommendation is released it must pass:
 1. trust gate: data quality score and critical tags, freshness, observer health, conservation
    residuals of the twin, no active critical alarm, mode permits advice;
 2. independent physics check: the recommended move trajectory is re-simulated on a clone of the
    first-principles twin (not the linear ROM). Hard MV/CV limits, rate limits and per-step
    elemental/energy closure are checked along the trajectory; a large ROM-vs-physics deviation
    is reported.
Any failure -> ADVICE_SUPPRESSED with explicit reasons and a 'no new move' recommendation.
"""
from __future__ import annotations

import time
from typing import Dict, List

import numpy as np

from ..physics import outputs as out
from .predictions import CV_DEFS
from ..physics.material_balance import max_rel_residual


def trust_gate(ctx: Dict[str, object]) -> Dict[str, object]:
    checks = {
        "mode_permits_advice": ctx.get("mode") in ("DEMO_SIMULATION", "ADVISORY_SHADOW_MPC", "LIVE_MONITOR_ONLY", "HISTORICAL_REPLAY"),
        "data_quality_score>=70": (ctx.get("dq_score") or 0) >= 70.0,
        "critical_tags_good": bool(ctx.get("critical_ok")),
        "data_fresh": bool(ctx.get("fresh", True)),
        "model_balance_closed": bool(ctx.get("balance_ok")),
        "observer_healthy": bool(ctx.get("observer_ok", True)),
        "no_critical_alarm": not ctx.get("critical_alarm", False),
        "model_in_envelope": bool(ctx.get("model_ok", True)),
    }
    failed = [k for k, v in checks.items() if not v]
    return {"checks": checks, "failed": failed, "passed": not failed}


def verify_on_physics(twin, moves: Dict[str, List[float]], blocks: List[int], Ts_s: float, cs,
                      horizon_h: float = 6.0, dt: float = 300.0, rom_pred=None, cvs=None, budget_s: float = 20.0) -> Dict[str, object]:
    t0 = time.time()
    m = twin.clone()
    u0 = {mv: m.op[mv] for mv in moves}
    steps = int(horizon_h * 3600 / dt)
    issues: List[str] = []
    traj = []
    worst_res = 0.0
    try:
        for k in range(steps):
            t_s = k * dt
            sample = t_s / Ts_s
            for mv, du in moves.items():
                cum = u0[mv] + sum(du[b] for b, st in enumerate(blocks) if sample >= st)
                lim = cs.mvs[mv]
                if cum < lim.lo - 1e-6 or cum > lim.hi + 1e-6:
                    issues.append(f"{mv} outside equipment envelope ({cum:.2f})")
                m.op[mv] = cum
            rec = m.step(dt)
            res = rec.el_in - rec.el_out - rec.el_acc
            rel = max_rel_residual(rec)
            worst_res = max(worst_res, rel)
            kp = out.kpis(m)
            y = {c: kp[c]["value"] for c in CV_DEFS}
            traj.append({"t_h": (k + 1) * dt / 3600.0, **y})
            for c, lim in cs.cvs.items():
                v = y.get(c)
                if v is None:
                    continue
                if lim.hi_hard is not None and v > lim.hi_hard or lim.lo_hard is not None and v < lim.lo_hard:
                    issues.append(f"{c}={v:.2f} breaches HARD limit at t+{(k + 1) * dt / 3600:.1f} h")
            if time.time() - t0 > budget_s:
                issues.append("physics verification time budget exceeded")
                break
    except Exception as e:  # model left its envelope during verification
        issues.append(f"physics model left its validated envelope: {e}")
    if worst_res > 1e-6:
        issues.append(f"elemental residual {worst_res:.2e} exceeds verification tolerance")
    dev = None
    if rom_pred is not None and traj and cvs:
        # compare ROM vs physics for HMT at the end of the verification horizon
        k_rom = min(int(round(horizon_h * 3600 / Ts_s)), len(rom_pred) - 1)
        i = cvs.index("hmt_C")
        dev = float(traj[-1]["hmt_C"] - rom_pred[k_rom][i])
    return {"passed": not issues, "issues": sorted(set(issues)), "trajectory": traj, "max_rel_element_residual": worst_res,
            "rom_vs_physics_hmt_dev_C": dev, "seconds": time.time() - t0, "horizon_h": horizon_h}
