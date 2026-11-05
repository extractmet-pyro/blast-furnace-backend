"""Feature engineering with strict causality.

The history frame has one row per model step (UTC timestamp) with measured tags (as received),
virtual-sensor values of the physics twin, and laboratory results placed at their RECEIPT time
(ts_ingest) - never at their sampling time - so no future information leaks into features.
Targets are future plant measurements at t + horizon (HMT dip, lab Si), aligned by their
SAMPLING time within a tolerance.

`feature_frame` is shared by training and live inference, so the live model sees exactly the
same transformation it was trained on.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd

MV_COLS = ["BL_FLOW", "BL_TEMP", "BL_MOIST", "BL_O2", "PCI_RATE", "COKE_BATCH"]
MEAS_COLS = ["BL_PRESS", "TG_CO", "TG_CO2", "TG_H2", "TG_TEMP", "DP_TOTAL", "ST_Q_BOSH", "ST_Q_BELLY", "STOCKLINE"]
PHYS_COLS = ["phys_hmt_C", "phys_si_pct", "phys_hmt_corrected_C", "phys_si_corrected_pct", "phys_raft_C", "phys_eta_CO_pct",
             "phys_active_coke_T_C", "phys_dripping_zone_height_m", "phys_production_t_d", "phys_fuel_rate_kg_tHM"]
LAB_COLS = ["HMT_DIP", "HM_SI", "HM_S", "SLAG_B2"]
# (measurement, physics+observer base, uncorrected physics fallback for data without observer columns)
TARGETS = {"hmt": ("HMT_DIP", "phys_hmt_corrected_C", "phys_hmt_C"), "si": ("HM_SI", "phys_si_corrected_pct", "phys_si_pct")}
TOLERANCE_S = 1200.0


def _step_s(d: pd.DataFrame) -> float:
    return float(np.median(np.diff(d["t_s"]))) if len(d) > 2 else 120.0


def feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Causal features for every row (no use of any value received after the row time)."""
    d = df.sort_values("t_s").reset_index(drop=True)
    step_s = _step_s(d)
    X = pd.DataFrame(index=d.index)
    X["t_s"] = d["t_s"]
    for c in MV_COLS + MEAS_COLS + PHYS_COLS:
        if c in d:
            X[c] = pd.to_numeric(d[c], errors="coerce").ffill()
        else:
            X[c] = np.nan
    for c in MV_COLS:
        s = X[c]
        for lag_h in (1, 2, 4, 8):
            n = max(int(round(lag_h * 3600 / step_s)), 1)
            X[f"d_{c}_{lag_h}h"] = s - s.shift(n)
    for c in LAB_COLS:
        if c in d:
            v = pd.to_numeric(d[c], errors="coerce")
            X[f"last_{c}"] = v.ffill()
            t_last = d["t_s"].where(v.notna()).ffill()
            X[f"age_{c}_h"] = (d["t_s"] - t_last) / 3600.0
        else:
            X[f"last_{c}"] = np.nan
            X[f"age_{c}_h"] = np.nan
    w = max(int(3600 / step_s), 1)
    for c in ("TG_CO", "TG_CO2"):
        X[f"{c}_mean_1h"] = X[c].rolling(w, min_periods=1).mean()
    X["eta_meas"] = X["TG_CO2"] / (X["TG_CO"] + X["TG_CO2"])
    return X.replace([np.inf, -np.inf], np.nan)


def physics_base(X: pd.DataFrame, target: str) -> pd.Series:
    """Physics twin + observer bias at the row time (falls back to the uncorrected twin value)."""
    _, corr, raw = TARGETS[target]
    base = X[corr] if corr in X else pd.Series(np.nan, index=X.index)
    return base.where(base.notna(), X[raw] if raw in X else np.nan)


def target_series(df: pd.DataFrame, horizon_h: float, target: str) -> pd.Series:
    """Future measurement whose SAMPLING time lies within +/-20 min of t + horizon."""
    meas_col = TARGETS[target][0]
    d = df.sort_values("t_s").reset_index(drop=True)
    y = pd.Series(np.nan, index=d.index)
    samp_col = f"{meas_col}__t_sample"
    if samp_col not in d or meas_col not in d:
        return y
    obs = d[[samp_col, meas_col]].dropna()
    ts = obs[samp_col].to_numpy(dtype=float)
    vals = obs[meas_col].to_numpy(dtype=float)
    order = np.argsort(ts)
    ts, vals = ts[order], vals[order]
    tgt_t = d["t_s"].to_numpy(dtype=float) + horizon_h * 3600
    idx = np.searchsorted(ts, tgt_t)
    out = np.full(len(d), np.nan)
    for i, (ti, k) in enumerate(zip(tgt_t, idx)):
        best, bd = None, TOLERANCE_S
        for kk in (k - 1, k):
            if 0 <= kk < len(ts) and abs(ts[kk] - ti) <= bd:
                best, bd = vals[kk], abs(ts[kk] - ti)
        if best is not None:
            out[i] = best
    return pd.Series(out, index=d.index)


def model_columns(X: pd.DataFrame) -> list:
    return [c for c in X.columns if c != "t_s"]


def build_features(df: pd.DataFrame, horizon_h: float = 2.0, target: str = "hmt") -> Tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return (X, y, physics_baseline) for rows where the target exists at t + horizon."""
    X = feature_frame(df)
    y = target_series(df, horizon_h, target)
    phys = physics_base(X, target)
    ok = y.notna() & phys.notna()
    Xo = X.loc[ok, model_columns(X)]
    # imputation with TRAINING-SET statistics happens inside the forecaster (stored with the model)
    return Xo, y.loc[ok], phys.loc[ok]


def live_row(df: pd.DataFrame, target: str) -> Tuple[pd.DataFrame, pd.Series]:
    """Features of the most recent row for online inference (same transformation as training)."""
    X = feature_frame(df)
    return X[model_columns(X)].iloc[[-1]], physics_base(X, target).iloc[[-1]]


def chronological_split(n: int, fractions=(0.6, 0.2, 0.2), embargo: int = 60) -> Dict[str, np.ndarray]:
    """Contiguous train/validation/test blocks separated by an embargo gap (no shuffling)."""
    a = int(n * fractions[0])
    b = int(n * (fractions[0] + fractions[1]))
    idx = np.arange(n)
    return {"train": idx[: max(a - embargo, 1)], "val": idx[a: max(b - embargo, a + 1)], "test": idx[b:]}
