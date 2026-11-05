"""State/parameter estimation.

ParameterEKF - augmented-parameter extended Kalman filter for the slowly varying, partially
unmeasured furnace properties that dominate model-plant mismatch:

  theta = [wall_heat_mult, coke_reactivity_mult, reduction_mult, permeability_mult,
           hmt_bias_C, si_bias_pct]

  process model:      theta_k = theta_{k-1} + w,   w ~ N(0, Q)       (bounded random walk)
  measurement model:  y = y_twin(theta_applied) + S (theta - theta_applied) + v,  v ~ N(0, R)

S is a finite-difference sensitivity matrix of the physics twin (dy/dtheta over a 3 h horizon,
recomputed in a background job). Updates use the Joseph form (covariance stays symmetric
positive-definite), chi-square innovation gating rejects outliers, and parameters are clipped
to physical bounds (constrained EKF). Laboratory results are applied as out-of-sequence
measurements against the twin output stored for the SAMPLING time, so no future information
leaks into an earlier estimate. Estimated parameters are written back into the twin's
calibration with rate limits.
"""
from __future__ import annotations

import copy
import math
from collections import deque
from typing import Dict, List, Optional

import numpy as np
from scipy import stats

PARAMS = ["wall_heat_mult", "coke_reactivity_mult", "reduction_mult", "permeability_mult", "hmt_bias_C", "si_bias_pct"]
MEAS = ["stave_heat_kW", "eta_CO_pct", "top_gas_T_C", "pressure_drop_kPa", "hmt_C", "hm_si_pct"]
BOUNDS = np.array([[0.6, 1.6], [0.5, 2.0], [0.6, 1.5], [0.6, 1.6], [-40.0, 40.0], [-0.4, 0.4]])
R_DIAG = np.array([400.0 ** 2, 0.6 ** 2, 25.0 ** 2, 3.0 ** 2, 8.0 ** 2, 0.04 ** 2])
Q_DIAG = np.array([0.002 ** 2, 0.004 ** 2, 0.002 ** 2, 0.004 ** 2, 0.8 ** 2, 0.004 ** 2])
TAU_REVERT_S = 72 * 3600.0   # physical multipliers revert slowly to the configured calibration (anti wind-up)

# prior sensitivities (rows MEAS, cols PARAMS) from finite differences of the large demo furnace
# (4 h horizon, last-hour averages). Physical-parameter updates stay FROZEN until the sensitivities
# have been recomputed for the configured furnace (background job at start-up): until then only
# the HMT/Si bias states are estimated.
S_PRIOR = np.array([
    [1500.0, -100.0, 150.0, 0.0, 0.0, 0.0],   # stave heat [kW per unit multiplier]
    [0.0, -2.4, 5.8, 0.3, 0.0, 0.0],          # eta_CO [% per unit]
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],           # top gas T: charging ripple dominates -> no parameter coupling
    [0.0, -0.5, 0.8, 120.0, 0.0, 0.0],        # dP [kPa per unit permeability multiplier]
    [-1.0, 0.3, 3.0, 0.0, 1.0, 0.0],          # HMT
    [0.0, 0.003, -0.01, 0.0, 0.0, 1.0],       # Si
])


def apply_params_to_cfg(cfg: dict, theta: np.ndarray, base: dict) -> None:
    cal = cfg["calibration"]
    cal["wall_h_in_mult"] = base["wall_h_in_mult"] * float(theta[0])
    km = dict(base["kinetic_multipliers"])
    for k in ("R_boud", "R_wg"):
        km[k] = km.get(k, 1.0) * float(theta[1])
    for k in ("R_wus_CO", "R_wus_H2", "R_mag_CO", "R_mag_H2"):
        km[k] = km.get(k, 1.0) * float(theta[2])
    cal["kinetic_multipliers"] = km
    cal["permeability_mult"] = base["permeability_mult"] * float(theta[3])


class ParameterEKF:
    def __init__(self, twin_cfg: dict):
        self.theta = np.array([1.0, 1.0, 1.0, 1.0, 0.0, 0.0])
        self.P = np.diag([0.08 ** 2, 0.15 ** 2, 0.08 ** 2, 0.08 ** 2, 10.0 ** 2, 0.08 ** 2])
        self.S = S_PRIOR.copy()
        self.S_source = "PRIOR (large demo furnace) - physical parameters frozen until recomputed"
        self.S_ready = False
        cal = twin_cfg["calibration"]
        self.base = {"wall_h_in_mult": cal.get("wall_h_in_mult", 1.0),
                     "kinetic_multipliers": dict(cal.get("kinetic_multipliers", {})),
                     "permeability_mult": cal.get("permeability_mult", 1.0)}
        self.applied = self.theta.copy()
        self.twin_hist: deque = deque(maxlen=2000)   # (t_s, {meas: value})
        self.log: deque = deque(maxlen=4000)
        self.n_updates = 0
        self.n_rejected = 0
        self.nis_hist: deque = deque(maxlen=500)

    # ----------------------------------------------------------------------------------
    def record_twin(self, t_s: float, y_twin: Dict[str, float]):
        self.twin_hist.append((t_s, dict(y_twin)))

    def twin_at(self, t_s: float) -> Optional[Dict[str, float]]:
        best = None
        for t, y in reversed(self.twin_hist):
            if t <= t_s + 1e-6:
                best = y
                break
        return best

    def predict(self, dt_s: float):
        """Bounded random walk with slow mean reversion of the physical multipliers (Ornstein-Uhlenbeck)."""
        a = np.ones(len(self.theta))
        a[:4] = math.exp(-dt_s / TAU_REVERT_S)
        self.theta[:4] = 1.0 + (self.theta[:4] - 1.0) * a[:4]
        self.P = (self.P * np.outer(a, a)) + np.diag(Q_DIAG) * (dt_s / 120.0)

    def set_sensitivities(self, S: np.ndarray, source: str):
        if S.shape == S_PRIOR.shape and np.all(np.isfinite(S)):
            S = S.copy()
            S[2, :4] = 0.0           # top-gas temperature: no parameter coupling (charging ripple)
            S[:, 4:] = S_PRIOR[:, 4:]
            self.S = S
            self.S_source = source
            self.S_ready = True

    def update(self, meas: Dict[str, float], t_meas: Dict[str, float], now_s: float) -> Dict[str, object]:
        """meas: name->value for available GOOD measurements; t_meas: sampling time (s) per name."""
        rows, yv, yt = [], [], []
        for i, name in enumerate(MEAS):
            if name not in meas or meas[name] is None:
                continue
            ref = self.twin_at(t_meas.get(name, now_s))
            if ref is None or ref.get(name) is None:
                continue
            # y = y_twin(theta_applied) + S (theta - theta_applied) for physical parameters + bias states
            pred = (ref[name] + float(self.S[i, :4] @ (self.theta[:4] - self.applied[:4]))
                    + (self.theta[4] if name == "hmt_C" else 0.0) + (self.theta[5] if name == "hm_si_pct" else 0.0))
            rows.append(i)
            yv.append(meas[name])
            yt.append(pred)
        if not rows:
            return {"updated": False, "reason": "no usable measurements"}
        H = self.S[rows, :].copy()
        if not self.S_ready:
            H[:, :4] = 0.0           # only bias states until furnace-specific sensitivities exist
        # bias states enter measurement directly; physical params via sensitivities relative to applied
        R = np.diag(R_DIAG[rows])
        nu = np.array(yv) - np.array(yt)
        Sm = H @ self.P @ H.T + R
        nis = float(nu @ np.linalg.solve(Sm, nu))
        crit = float(stats.chi2.ppf(0.999, len(rows)))
        self.nis_hist.append(nis / len(rows))
        if not hasattr(self, "consec_reject"):
            self.consec_reject = {}
        if nis > crit:
            # robust gating: reject the worst single measurement (outlier). A channel rejected on
            # many CONSECUTIVE updates is not an outlier but a persistent offset (sensor drift or
            # model mismatch): it is then admitted with a 4x inflated variance so the parameters
            # can absorb a genuine mismatch, and it is reported in status() for the drift alarm.
            z = np.abs(nu) / np.sqrt(np.diag(Sm))
            worst = int(np.argmax(z))
            name_w = MEAS[rows[worst]]
            self.consec_reject[name_w] = self.consec_reject.get(name_w, 0) + 1
            self.n_rejected += 1
            if self.consec_reject[name_w] >= 10:
                # Huber-type admission: inflate the variance so the normalised innovation is 3 sigma
                R = R.copy()
                R[worst, worst] *= max((z[worst] / 3.0) ** 2, 1.0)
                Sm = H @ self.P @ H.T + R
            else:
                keep = [k for k in range(len(rows)) if k != worst]
                if not keep:
                    return {"updated": False, "reason": "innovation gated (chi2)", "nis": nis}
                rows = [rows[k] for k in keep]
                nu = nu[keep]
                H = H[keep, :]
                R = R[np.ix_(keep, keep)]
                Sm = H @ self.P @ H.T + R
        else:
            self.consec_reject = {}
        K = self.P @ H.T @ np.linalg.inv(Sm)
        self.theta = self.theta + K @ nu
        I_KH = np.eye(len(self.theta)) - K @ H
        self.P = I_KH @ self.P @ I_KH.T + K @ R @ K.T
        self.P = 0.5 * (self.P + self.P.T) + 1e-12 * np.eye(len(self.theta))
        self.theta = np.clip(self.theta, BOUNDS[:, 0], BOUNDS[:, 1])
        self.n_updates += 1
        rec = {"t_s": now_s, "theta": self.theta.round(5).tolist(), "innovations": dict(zip([MEAS[r] for r in rows], nu.round(4).tolist())),
               "nis": nis}
        self.log.append(rec)
        return {"updated": True, **rec}

    def apply_to_twin(self, twin, max_step: float = 0.01):
        """Rate-limited write-back of physical parameters into the twin calibration."""
        tgt = self.theta.copy()
        step = np.clip(tgt[:4] - self.applied[:4], -max_step, max_step)
        self.applied[:4] = self.applied[:4] + step
        self.applied[4:] = tgt[4:]
        apply_params_to_cfg(twin.cfg, self.applied, self.base)
        for w in twin.walls:
            w.h_in_mult = float(self.applied[0])
        for w in (twin.w_sump, twin.w_upper, twin.w_bottom):
            w.h_in_mult = float(self.applied[0])

    def status(self) -> Dict[str, object]:
        sd = np.sqrt(np.diag(self.P))
        return {"method": "augmented-parameter EKF (Joseph form, chi2 gating, bounded)",
                "parameters": [{"name": n, "estimate": float(self.theta[i]), "applied": float(self.applied[i]),
                                "sd": float(sd[i]), "lower95": float(self.theta[i] - 1.96 * sd[i]),
                                "upper95": float(self.theta[i] + 1.96 * sd[i]), "bounds": BOUNDS[i].tolist()}
                               for i, n in enumerate(PARAMS)],
                "measurements": MEAS, "sensitivity_source": self.S_source, "updates": self.n_updates,
                "rejected": self.n_rejected,
                "mean_normalised_innovation": float(np.mean(self.nis_hist)) if self.nis_hist else None,
                "covariance_pd": bool(np.all(np.linalg.eigvalsh(self.P) > 0)),
                "recent": list(self.log)[-30:]}


def compute_sensitivities(model, horizon_h: float = 4.0, deltas=(0.1, 0.15, 0.1, 0.1)) -> np.ndarray:
    """Finite-difference dy/dtheta of the physics twin from its current state (expensive; background job)."""
    from ..physics import outputs as out

    def run(mod):
        mod.run(max(horizon_h - 1.0, 0.5), dt=240.0)
        acc = []
        for _ in range(15):          # last-hour average suppresses charging/casting ripple
            mod.step(240.0)
            k = out.kpis(mod)
            stave = sum(w.Q_water_last for w in mod.walls) / 1e3
            acc.append([stave, k["eta_CO_pct"]["value"], k["top_gas_T_C"]["value"], k["pressure_drop_kPa"]["value"],
                        k["hmt_C"]["value"], k["hm_si_pct"]["value"]])
        return np.nanmean(np.array(acc, dtype=float), axis=0)

    base_m = model.clone()
    y0 = run(base_m)
    S = S_PRIOR.copy()
    ekf = ParameterEKF(model.cfg)
    for j, d in enumerate(deltas):
        mm = model.clone()
        th = np.array([1.0, 1.0, 1.0, 1.0, 0.0, 0.0])
        th[j] += d
        apply_params_to_cfg(mm.cfg, th, ekf.base)
        if j == 0:
            for w in mm.walls + [mm.w_sump, mm.w_upper, mm.w_bottom]:
                w.h_in_mult = th[0]
        y1 = run(mm)
        S[:, j] = (y1 - y0) / d
    return S
