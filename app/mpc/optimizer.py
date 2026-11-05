"""Constrained multivariable MPC.

Linear MPC (default): quadratic programme solved with OSQP (fallback: SciPy SLSQP)

  min_{dU, s}  sum_k (y_k - r)' Q (y_k - r) + dU' R dU + c_econ' u_N + rho' s
  s.t.         y = y_free + G dU                     (DMC step-response prediction)
               u_min <= u_now + C dU <= u_max         (equipment envelope, hard)
               |dU_b| <= rate * block_length          (rate limits, hard)
               y_lo - s <= y_k <= y_hi + s,  s >= 0  (only for configuration-approved SOFT limits)
               y_k <= y_hi_hard                       (hard output limits, no slack)

All quantities are normalised by engineering scales (unit-free objective). Objective components
are reported before (no new moves) and after optimisation.
NMPC (optional, background job): single-shooting on the physics twin with move blocking,
SLSQP and a strict wall-clock budget.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import scipy.sparse as sp
from scipy.optimize import minimize

from .predictions import DMCPredictor, StepResponseModel, CV_DEFS, MV_DEFS, mv_scales
from .constraints import ConstraintSet

try:
    import osqp  # type: ignore
    HAVE_OSQP = True
except Exception:  # pragma: no cover
    HAVE_OSQP = False

DEFAULT_WEIGHTS = {"hmt_C": 1.0, "hm_si_pct": 0.6, "raft_C": 0.05, "eta_CO_pct": 0.0, "pressure_drop_kPa": 0.0,
                   "top_gas_T_C": 0.0, "production_t_d": 0.0, "fuel_rate_kg_tHM": 0.15}
DEFAULT_MOVE_WEIGHTS = {"blast_temp_C": 0.3, "blast_moisture_g_Nm3": 0.5, "o2_enrichment_pct": 0.6, "pci_t_h": 0.4,
                        "blast_volume_Nm3_min": 1.5, "coke_batch_t": 2.0}


@dataclass
class MPCProblem:
    targets: Dict[str, float]
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    move_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_MOVE_WEIGHTS))
    econ_INR_per_unit_h: Dict[str, float] = field(default_factory=dict)
    econ_weight: float = 0.0
    blocks: List[int] = field(default_factory=lambda: [0, 2, 4, 8])
    soft_penalty: float = 200.0
    soft_quad: float = 20.0
    time_limit_s: float = 5.0


@dataclass
class MPCResult:
    status: str
    solver: str
    moves: Dict[str, List[float]]
    u_now: Dict[str, float]
    u_traj: Dict[str, List[float]]
    y_free: np.ndarray
    y_opt: np.ndarray
    objective: Dict[str, Dict[str, float]]
    active: List[str]
    solve_time_s: float
    message: str = ""


def _objective_parts(y, r, Wq, du, Wr, econ, slack, rho):
    e = (y - r) * np.sqrt(Wq)
    return {"tracking": float(np.sum(e * e)), "move": float(np.sum(Wr * du * du)), "economic": float(econ),
            "soft_violation": float(np.sum(rho * slack))}


def solve_linear_mpc(srm: StepResponseModel, pred: DMCPredictor, y_now: Dict[str, float], u_now: Dict[str, float],
                     cs: ConstraintSet, prob: MPCProblem, disturbance: Optional[Dict[str, float]] = None) -> MPCResult:
    t0 = time.time()
    cvs, mvs_all = srm.cvs, srm.mvs
    active = [mv for mv in mvs_all if cs.mvs.get(mv) and cs.mvs[mv].available]
    if not active:
        return MPCResult("NO_ACTIVE_MV", "none", {}, u_now, {}, np.zeros(0), np.zeros(0), {}, [], 0.0,
                         "No manipulated variable is available (all locked or unavailable).")
    N, ncv = srm.N, len(cvs)
    jidx = [mvs_all.index(mv) for mv in active]
    nmv = len(active)
    nb = len(prob.blocks)
    ysc = np.array([CV_DEFS[c]["scale"] for c in cvs])
    usc_all = mv_scales(u_now)
    usc = np.array([usc_all[mv] for mv in active])
    yv = np.array([y_now.get(c) if y_now.get(c) is not None else np.nan for c in cvs], dtype=float)
    finite = np.isfinite(yv)
    yv = np.nan_to_num(yv)
    d = np.array([(disturbance or {}).get(c, 0.0) for c in cvs])
    Yf = pred.free_response(yv, d)                         # (N+1, ncv)
    Gfull = pred.dynamic_matrix(prob.blocks)               # ((N+1)*ncv, nb*nmv_all)
    cols = [b * len(mvs_all) + j for b in range(nb) for j in jidx]
    G = Gfull[:, cols]
    # normalise: y' = y/ysc, du' = du/usc
    Ysc = np.tile(ysc, N + 1)
    Usc = np.tile(usc, nb)
    Gn = G / Ysc[:, None] * Usc[None, :]
    yf_n = (Yf.reshape(-1)) / Ysc
    r = np.array([prob.targets.get(c, np.nan) for c in cvs])
    w = np.array([prob.weights.get(c, 0.0) if np.isfinite(r[i]) and finite[i] else 0.0 for i, c in enumerate(cvs)])
    r_n = np.tile(np.nan_to_num(r) / ysc, N + 1)
    Wq = np.tile(w, N + 1)
    Wq[:ncv] = 0.0  # no penalty on the current sample
    Wr = np.tile(np.array([prob.move_weights.get(mv, 1.0) for mv in active]), nb)
    # economic linear term on final input (INR/h per unit) scaled
    econ = np.zeros(nb * nmv)
    if prob.econ_weight > 0:
        for b in range(nb):
            for jj, mv in enumerate(active):
                econ[b * nmv + jj] = prob.econ_weight * prob.econ_INR_per_unit_h.get(mv, 0.0) * usc[jj] / 1e5
    # soft CV constraints (one slack per soft CV side)
    soft_rows, hard_rows = [], []
    for i, c in enumerate(cvs):
        lim = cs.cvs.get(c)
        if lim is None or not finite[i]:
            continue
        if lim.soft:
            if lim.lo is not None:
                soft_rows.append((i, "lo", lim.lo))
            if lim.hi is not None:
                soft_rows.append((i, "hi", lim.hi))
        for side, val in (("lo", lim.lo_hard), ("hi", lim.hi_hard)):
            if val is not None:
                hard_rows.append((i, side, val))
        if not lim.soft:
            for side, val in (("lo", lim.lo), ("hi", lim.hi)):
                if val is not None:
                    hard_rows.append((i, side, val))
    ns = len(soft_rows)
    nx = nb * nmv + ns
    # Hessian & gradient
    WG = Gn * Wq[:, None]
    P = np.zeros((nx, nx))
    P[:nb * nmv, :nb * nmv] = 2 * (Gn.T @ WG + np.diag(Wr))
    P[nb * nmv:, nb * nmv:] = np.eye(ns) * 2.0 * prob.soft_quad   # quadratic + linear exact-penalty slack (well conditioned)
    qv = np.zeros(nx)
    qv[:nb * nmv] = 2 * Gn.T @ (Wq * (yf_n - r_n)) + econ
    qv[nb * nmv:] = prob.soft_penalty
    A_rows, lo_b, hi_b = [], [], []
    # cumulative MV bounds after each block & rate limits
    for b in range(nb):
        for jj, mv in enumerate(active):
            row = np.zeros(nx)
            for bb in range(b + 1):
                row[bb * nmv + jj] = usc[jj]
            lim = cs.mvs[mv]
            A_rows.append(row)
            lo_b.append(lim.lo - u_now[mv])
            hi_b.append(lim.hi - u_now[mv])
            blen = (prob.blocks[b + 1] - prob.blocks[b]) if b + 1 < nb else 4
            rate = lim.rate_per_15min * max(blen, 1)
            row2 = np.zeros(nx)
            row2[b * nmv + jj] = usc[jj]
            A_rows.append(row2)
            lo_b.append(-rate)
            hi_b.append(rate)
    # output constraints over the horizon (every sample after the first)
    for k in range(1, N + 1):
        base = k * ncv
        # all CV rows in normalised units (y/ysc) - slack also normalised - for solver conditioning
        for si, (i, side, val) in enumerate(soft_rows):
            row = np.zeros(nx)
            row[:nb * nmv] = Gn[base + i]
            y0 = Yf[k, i]
            if side == "lo":
                row[nb * nmv + si] = 1.0
                A_rows.append(row)
                lo_b.append((val - y0) / ysc[i])
                hi_b.append(np.inf)
            else:
                row[nb * nmv + si] = -1.0
                A_rows.append(row)
                lo_b.append(-np.inf)
                hi_b.append((val - y0) / ysc[i])
        for (i, side, val) in hard_rows:
            row = np.zeros(nx)
            row[:nb * nmv] = Gn[base + i]
            y0 = Yf[k, i]
            A_rows.append(row)
            if side == "lo":
                lo_b.append((val - y0) / ysc[i])
                hi_b.append(np.inf)
            else:
                lo_b.append(-np.inf)
                hi_b.append((val - y0) / ysc[i])
    for si in range(ns):
        row = np.zeros(nx)
        row[nb * nmv + si] = 1.0
        A_rows.append(row)
        lo_b.append(0.0)
        hi_b.append(np.inf)
    A = np.array(A_rows)
    lo_b = np.array(lo_b, dtype=float)
    hi_b = np.array(hi_b, dtype=float)
    status, solver, x = "SOLVER_ERROR", "none", None
    if HAVE_OSQP:
        solver = "OSQP"
        mdl = osqp.OSQP()
        kw = dict(verbose=False, eps_abs=1e-6, eps_rel=1e-6, max_iter=60000, time_limit=prob.time_limit_s)
        try:
            mdl.setup(sp.csc_matrix(np.triu(P)), qv, sp.csc_matrix(A), lo_b, hi_b, polishing=True, **kw)
        except TypeError:  # older OSQP releases
            mdl.setup(sp.csc_matrix(np.triu(P)), qv, sp.csc_matrix(A), lo_b, hi_b, polish=True, **kw)
        res = mdl.solve()
        st = str(res.info.status).lower()
        if "solved" in st and "inaccurate" not in st:
            status, x = "OPTIMAL", res.x
        elif "inaccurate" in st:
            status, x = "OPTIMAL_INACCURATE", res.x
        elif "infeasible" in st:
            status = "NO_FEASIBLE_SOLUTION"
        elif "time" in st or "iteration" in st or "max_iter" in st:
            # accept the iterate only if it satisfies every constraint (checked below); otherwise TIMEOUT
            status, x = ("OPTIMAL_INACCURATE", res.x) if res.x is not None and np.all(np.isfinite(res.x)) else ("TIMEOUT", None)
        else:
            status = f"SOLVER_{st.upper().replace(' ', '_')}"
    else:  # pragma: no cover - fallback path
        solver = "SLSQP"
        cons = [{"type": "ineq", "fun": lambda z, i=i: A[i] @ z - lo_b[i]} for i in range(len(lo_b)) if np.isfinite(lo_b[i])]
        cons += [{"type": "ineq", "fun": lambda z, i=i: hi_b[i] - A[i] @ z} for i in range(len(hi_b)) if np.isfinite(hi_b[i])]
        rr = minimize(lambda z: 0.5 * z @ P @ z + qv @ z, np.zeros(nx), jac=lambda z: P @ z + qv, constraints=cons,
                      method="SLSQP", options={"maxiter": 300})
        status, x = ("OPTIMAL", rr.x) if rr.success else ("NO_FEASIBLE_SOLUTION", None)
    if x is not None:
        viol = float(np.max(np.concatenate([lo_b - A @ x, A @ x - hi_b, [0.0]])))
        if viol > 1e-3:
            status, x = ("NO_FEASIBLE_SOLUTION" if status in ("OPTIMAL", "OPTIMAL_INACCURATE") and "iteration" not in str(status).lower()
                         else "TIMEOUT"), None
    dt = time.time() - t0
    zero = np.zeros(nx)
    y_free = Yf
    obj_before = _objective_parts(yf_n, r_n, Wq, np.zeros(nb * nmv), Wr, 0.0, np.zeros(ns), prob.soft_penalty)
    if x is None:
        return MPCResult(status, solver, {}, u_now, {}, y_free, y_free, {"before": obj_before}, [], dt,
                         "No admissible move set satisfies the hard constraints." if status == "NO_FEASIBLE_SOLUTION" else status)
    du_n = x[:nb * nmv]
    slack = x[nb * nmv:]
    y_opt_n = yf_n + Gn @ du_n
    obj_after = _objective_parts(y_opt_n, r_n, Wq, du_n, Wr, float(econ @ du_n), slack, prob.soft_penalty)
    du = du_n * Usc
    moves = {mv: [float(du[b * nmv + jj]) for b in range(nb)] for jj, mv in enumerate(active)}
    u_traj = {mv: list(np.cumsum(moves[mv]) + u_now[mv]) for mv in active}
    y_opt = (y_opt_n * Ysc).reshape(N + 1, ncv)
    active_c = []
    Ax = A @ x
    for i in range(len(lo_b)):
        if np.isfinite(lo_b[i]) and abs(Ax[i] - lo_b[i]) < 1e-4 or np.isfinite(hi_b[i]) and abs(Ax[i] - hi_b[i]) < 1e-4:
            active_c.append(i)
    active_names = _name_active(active_c, nb, nmv, active, soft_rows, hard_rows, cvs, N)
    return MPCResult(status, solver, moves, u_now, u_traj, y_free, y_opt, {"before": obj_before, "after": obj_after},
                     active_names, dt)


def _name_active(idx, nb, nmv, active, soft_rows, hard_rows, cvs, N):
    names = set()
    per_block = 2 * nmv
    mv_rows = nb * per_block
    for i in idx:
        if i < mv_rows:
            b, rem = divmod(i, per_block)
            jj, kind = divmod(rem, 2)
            names.add(f"{active[jj]}: {'equipment bound' if kind == 0 else 'rate limit'}")
        else:
            j = i - mv_rows
            per_k = len(soft_rows) + len(hard_rows)
            if per_k and j < N * per_k:
                r = j % per_k
                if r < len(soft_rows):
                    ii, side, _ = soft_rows[r]
                    names.add(f"{cvs[ii]}: soft {side} limit")
                else:
                    ii, side, _ = hard_rows[r - len(soft_rows)]
                    names.add(f"{cvs[ii]}: HARD {side} limit")
    return sorted(names)


def solve_nmpc_physics(twin, mvs: List[str], targets: Dict[str, float], cs: ConstraintSet, horizon_h: float = 8.0,
                       dt: float = 600.0, n_blocks: int = 2, budget_s: float = 45.0, weights=None) -> Dict[str, object]:
    """Single-shooting NMPC on the physics twin (move blocking, SLSQP, wall-clock budget)."""
    from ..physics import outputs as out
    weights = weights or DEFAULT_WEIGHTS
    t_start = time.time()
    u0 = np.array([twin.op[mv] for mv in mvs])
    sc = mv_scales(twin.op)
    usc = np.array([sc[mv] for mv in mvs])
    lo = np.array([cs.mvs[mv].lo for mv in mvs])
    hi = np.array([cs.mvs[mv].hi for mv in mvs])
    rate = np.array([cs.mvs[mv].rate_per_15min for mv in mvs]) * 4  # per hour block
    n_eval = {"n": 0}

    class Budget(Exception):
        pass

    def sim(z):
        if time.time() - t_start > budget_s:
            raise Budget()
        n_eval["n"] += 1
        U = u0 + np.cumsum(z.reshape(n_blocks, len(mvs)) * usc, axis=0)
        m = twin.clone()
        steps = int(horizon_h * 3600 / dt)
        per_block = max(steps // n_blocks, 1)
        cost = 0.0
        traj = []
        for k in range(steps):
            b = min(k // per_block, n_blocks - 1)
            for jj, mv in enumerate(mvs):
                m.op[mv] = float(np.clip(U[b, jj], lo[jj], hi[jj]))
            m.step(dt)
            kp = out.kpis(m)
            yk = {c: kp[c]["value"] for c in targets}
            traj.append(yk)
            for c, rv in targets.items():
                if yk.get(c) is not None:
                    cost += weights.get(c, 0.0) * ((yk[c] - rv) / CV_DEFS[c]["scale"]) ** 2
        return cost / steps + 0.05 * float(np.sum(z * z)), traj

    z0 = np.zeros(n_blocks * len(mvs))
    bounds = []
    for b in range(n_blocks):
        for jj in range(len(mvs)):
            bounds.append((-rate[jj] / usc[jj], rate[jj] / usc[jj]))
    try:
        res = minimize(lambda z: sim(z)[0], z0, method="SLSQP", bounds=bounds,
                       options={"maxiter": 15, "eps": 0.25, "ftol": 1e-4})
        status = "OPTIMAL" if res.success else "MAXITER_OR_NOT_CONVERGED"
        z = res.x
    except Budget:
        return {"status": "TIMEOUT", "message": f"NMPC exceeded the {budget_s:.0f} s budget after {n_eval['n']} simulations",
                "evaluations": n_eval["n"]}
    try:
        cost, traj = sim(z)
    except Budget:
        return {"status": "TIMEOUT", "message": "budget exhausted in final evaluation", "evaluations": n_eval["n"]}
    U = u0 + np.cumsum(z.reshape(n_blocks, len(mvs)) * usc, axis=0)
    return {"status": status, "solver": "SLSQP single-shooting on physics twin", "evaluations": n_eval["n"],
            "seconds": time.time() - t_start, "u_blocks": {mv: U[:, jj].tolist() for jj, mv in enumerate(mvs)},
            "cost": cost, "trajectory": traj, "horizon_h": horizon_h, "dt_s": dt}
