"""Reduced-order prediction model for MPC: step-response (DMC) model identified from the physics
twin by step tests, plus an output-disturbance estimate (offset-free control).

Identification: from a clone of the twin at the current operating point, two runs per manipulated
variable (MV) with symmetric steps +du_j and -du_j are simulated over the horizon; the CVs are
averaged over each sample interval (suppresses discrete charging/casting ripple) and the
step-response coefficients are the central differences S_j(k) = (y+(k) - y-(k)) / (2 du_j), so drifts
common to both runs and even-order nonlinearity cancel. The result is the linearised multivariable
response including all burden-travel and thermal delays the physics model contains.

Prediction (DMC):
  y(k) = y_meas + d + sum_j sum_l [S_j(k+l) - S_j(l)] du_j(t-l)      (free response of past moves)
                   + sum_j sum_m S_j(k-m) du_j(m)                     (forced response of future moves)
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

MV_DEFS = {
    "blast_temp_C": {"unit": "degC", "step": 25.0, "scale": 20.0, "label": "Hot blast temperature"},
    "blast_moisture_g_Nm3": {"unit": "g/Nm3", "step": 4.0, "scale": 3.0, "label": "Blast humidity"},
    "o2_enrichment_pct": {"unit": "%", "step": 0.75, "scale": 0.5, "label": "Oxygen enrichment"},
    "pci_t_h": {"unit": "t/h", "step": None, "scale": None, "label": "PCI rate"},
    "blast_volume_Nm3_min": {"unit": "Nm3/min", "step": None, "scale": None, "label": "Blast volume"},
    "coke_batch_t": {"unit": "t/charge", "step": None, "scale": None, "label": "Coke batch (ore/coke ratio)"},
}
CV_DEFS = {
    "hmt_C": {"unit": "degC", "scale": 15.0, "label": "Hot metal temperature"},
    "hm_si_pct": {"unit": "wt%", "scale": 0.08, "label": "Hot metal Si"},
    "raft_C": {"unit": "degC", "scale": 40.0, "label": "RAFT (estimate)"},
    "eta_CO_pct": {"unit": "%", "scale": 1.0, "label": "Top gas CO utilisation"},
    "pressure_drop_kPa": {"unit": "kPa", "scale": 8.0, "label": "Bed pressure drop"},
    "top_gas_T_C": {"unit": "degC", "scale": 30.0, "label": "Top gas temperature"},
    "production_t_d": {"unit": "tHM/d", "scale": 250.0, "label": "Production"},
    "fuel_rate_kg_tHM": {"unit": "kg/tHM", "scale": 10.0, "label": "Fuel rate"},
}


def mv_step_sizes(op: dict) -> Dict[str, float]:
    out = {}
    for k, d in MV_DEFS.items():
        if d["step"] is not None:
            out[k] = d["step"]
        elif k == "pci_t_h":
            out[k] = max(0.06 * op[k], 0.5)
        elif k == "blast_volume_Nm3_min":
            out[k] = 0.03 * op[k]
        elif k == "coke_batch_t":
            out[k] = 0.02 * op[k]
    return out


def mv_scales(op: dict) -> Dict[str, float]:
    st = mv_step_sizes(op)
    return {k: (d["scale"] if d["scale"] is not None else st[k]) for k, d in MV_DEFS.items()}


def read_cvs(m) -> Dict[str, float]:
    from ..physics import outputs as out
    k = out.kpis(m)
    return {c: k[c]["value"] for c in CV_DEFS}


@dataclass
class StepResponseModel:
    Ts_s: float
    N: int
    mvs: List[str]
    cvs: List[str]
    S: np.ndarray                 # (N+1, n_cv, n_mv), S[0] = 0
    op_point: Dict[str, float]
    identified_utc: str
    source: str = "physics-twin step tests"
    ident_seconds: float = 0.0
    meta: Dict[str, object] = field(default_factory=dict)

    def gain(self) -> np.ndarray:
        return self.S[-1]

    def t63(self) -> Dict[str, Dict[str, Optional[float]]]:
        """Time (h) to 63 % of the final response (delay indicator per MV->CV pair)."""
        out = {}
        for i, c in enumerate(self.cvs):
            out[c] = {}
            for j, mv in enumerate(self.mvs):
                s = self.S[:, i, j]
                fin = s[-1]
                if abs(fin) < 1e-12:
                    out[c][mv] = None
                    continue
                idx = np.where(np.abs(s) >= 0.63 * abs(fin))[0]
                out[c][mv] = float(idx[0] * self.Ts_s / 3600.0) if idx.size else None
        return out

    def as_dict(self):
        return {"Ts_min": self.Ts_s / 60, "N": self.N, "mvs": self.mvs, "cvs": self.cvs,
                "gain": self.gain().round(6).tolist(), "t63_h": self.t63(), "op_point": self.op_point,
                "identified_utc": self.identified_utc, "source": self.source, "ident_seconds": self.ident_seconds,
                "step_response": {c: {mv: self.S[:, i, j].round(6).tolist() for j, mv in enumerate(self.mvs)}
                                  for i, c in enumerate(self.cvs)}}


def identify(twin, Ts_s: float = 900.0, horizon_h: float = 12.0, mvs: Optional[List[str]] = None,
             dt: float = 240.0, progress=None) -> StepResponseModel:
    from datetime import datetime, timezone
    t0 = time.time()
    mvs = mvs or list(MV_DEFS.keys())
    cvs = list(CV_DEFS.keys())
    N = int(round(horizon_h * 3600 / Ts_s))
    steps = mv_step_sizes(twin.op)
    per = int(round(Ts_s / dt))

    def simulate(model, mv=None, du=0.0):
        mod = model.clone()
        y0 = read_cvs(mod)
        if mv:
            mod.op[mv] = mod.op[mv] + du
        Y = [[y0.get(c) if y0.get(c) is not None else np.nan for c in cvs]]
        for _ in range(N):
            acc = []
            for _ in range(max(per, 1)):
                mod.step(dt)
                y = read_cvs(mod)
                acc.append([y.get(c) if y.get(c) is not None else np.nan for c in cvs])
            Y.append(np.nanmean(np.array(acc, dtype=float), axis=0).tolist())
        return np.array(Y, dtype=float)

    S = np.zeros((N + 1, len(cvs), len(mvs)))
    for j, mv in enumerate(mvs):
        if progress:
            progress(j / max(len(mvs), 1), f"step test {mv} (+/-)")
        Yp = simulate(twin, mv, +steps[mv])
        Ym = simulate(twin, mv, -steps[mv])
        S[:, :, j] = np.nan_to_num((Yp - Ym) / (2.0 * steps[mv]))
    S[0] = 0.0
    # light smoothing of residual ripple (centred 3-sample moving average)
    Ss = S.copy()
    Ss[1:-1] = (S[:-2] + S[1:-1] + S[2:]) / 3.0
    return StepResponseModel(Ts_s, N, mvs, cvs, Ss, {k: twin.op[k] for k in mvs},
                             datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                             ident_seconds=time.time() - t0, meta={"steps": steps, "dt_s": dt, "method": "symmetric +/- steps, interval-averaged CVs"})


class DMCPredictor:
    def __init__(self, srm: StepResponseModel):
        self.m = srm
        self.past_moves: List[np.ndarray] = []   # most recent last, length <= N

    def register_move(self, du: np.ndarray):
        self.past_moves.append(np.asarray(du, dtype=float))
        self.past_moves = self.past_moves[-self.m.N:]

    def free_response(self, y_now: np.ndarray, d: Optional[np.ndarray] = None) -> np.ndarray:
        N = self.m.N
        S = self.m.S
        y = np.tile(y_now + (0.0 if d is None else d), (N + 1, 1))
        for l, du in enumerate(reversed(self.past_moves), start=1):
            for k in range(N + 1):
                kk = min(k + l, N)
                ll = min(l, N)
                y[k] += (S[kk] - S[ll]) @ du
        return y

    def dynamic_matrix(self, block_starts: List[int]) -> np.ndarray:
        """G[(k, cv), (block, mv)] = S(k - start) for k >= start."""
        N, ncv, nmv = self.m.N, len(self.m.cvs), len(self.m.mvs)
        G = np.zeros(((N + 1) * ncv, len(block_starts) * nmv))
        for b, st in enumerate(block_starts):
            for k in range(N + 1):
                if k >= st:
                    G[k * ncv:(k + 1) * ncv, b * nmv:(b + 1) * nmv] = self.m.S[k - st]
        return G
