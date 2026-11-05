"""Training/evaluation pipeline for the hybrid forecasters with chronological splits,
baseline comparison, interval coverage, OOD and drift diagnostics and registry provenance."""
from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd

from .baselines import ARXRidge, persistence, physics_only
from .drift import drift_report
from .features import build_features, chronological_split
from .hybrid import HybridForecaster
from .registry import ModelRegistry, dataset_hash

PROMOTE_MARGIN = 0.02

BOUNDS = {"hmt": 40.0, "si": 0.25}


def metrics(y: np.ndarray, p: np.ndarray) -> Dict[str, float]:
    ok = np.isfinite(y) & np.isfinite(p)
    y, p = y[ok], p[ok]
    if len(y) < 3:
        return {"n": int(len(y)), "MAE": None, "RMSE": None, "bias": None, "R2": None}
    e = p - y
    ss_res = float(np.sum(e**2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    return {"n": int(len(y)), "MAE": float(np.mean(np.abs(e))), "RMSE": float(np.sqrt(np.mean(e**2))),
            "bias": float(np.mean(e)), "R2": float(1 - ss_res / ss_tot) if ss_tot > 1e-12 else None}


def train_target(df: pd.DataFrame, target: str, horizon_h: float, registry: ModelRegistry, data_origin: str,
                 seed: int = 7, kind: str = "hgb") -> Dict[str, object]:
    X, y, phys = build_features(df, horizon_h, target)
    n = len(X)
    if n < 150:
        return {"status": "UNTRAINED", "reason": f"only {n} labelled rows (need >= 150)", "target": target, "horizon_h": horizon_h}
    sp = chronological_split(n, embargo=max(int(horizon_h * 30), 30))
    tr, va, te = sp["train"], sp["val"], sp["test"]
    hf = HybridForecaster(target, horizon_h, BOUNDS[target], kind, seed).fit(X.iloc[tr], y.iloc[tr], phys.iloc[tr])
    hf.calibrate(X.iloc[va], y.iloc[va], phys.iloc[va])
    arx = ARXRidge().fit(X.iloc[tr], y.iloc[tr])
    res = {}
    for name, idx in (("validation", va), ("test", te)):
        yy = y.iloc[idx].to_numpy()
        out = hf.predict(X.iloc[idx], phys.iloc[idx])
        cover = float(np.mean((yy >= out["lower95"]) & (yy <= out["upper95"]))) if len(yy) else None
        res[name] = {"hybrid": metrics(yy, out["prediction"]), "physics_only": metrics(yy, physics_only(phys.iloc[idx])),
                     "persistence": metrics(yy, persistence(X.iloc[idx], target)), "arx_ridge": metrics(yy, arx.predict(X.iloc[idx])),
                     "interval95_coverage": cover, "ood_fraction": float(np.mean(out["ood"]))}
    tst = res["test"]
    # promotion rule: at least PROMOTE_MARGIN better (RMSE) than BOTH physics + observer and persistence on the
    # held-out test block - a hair's-breadth win is noise, and the hybrid must earn its added complexity
    best_baseline = min(tst["physics_only"]["RMSE"] or 1e9, tst["persistence"]["RMSE"] or 1e9)
    better = (tst["hybrid"]["RMSE"] is not None and tst["physics_only"]["RMSE"] is not None
              and tst["hybrid"]["RMSE"] <= (1.0 - PROMOTE_MARGIN) * best_baseline)
    imp = None
    try:
        from sklearn.inspection import permutation_importance
        sub = te[: min(len(te), 400)]
        pi = permutation_importance(hf.reg, hf._prep(X.iloc[sub]), (y.iloc[sub] - phys.iloc[sub]), n_repeats=3, random_state=seed)
        order = np.argsort(-pi.importances_mean)[:12]
        imp = [{"feature": hf.cols[i], "importance": float(pi.importances_mean[i])} for i in order]
    except Exception:
        imp = None
    drift = drift_report(hf._prep(X.iloc[tr]), hf._prep(X.iloc[te]), [c for c in hf.cols if not c.startswith("age_")][:20])
    meta = {"target": target, "horizon_h": horizon_h, "algorithm": hf.algorithm, "hybrid_form": "physics + bounded ML residual",
            "correction_bound": BOUNDS[target], "correction_shrinkage_alpha": hf.alpha, "alpha_selection": hf.alpha_curve,
            "physics_base": "physics twin + EKF observer bias at forecast time", "seed": seed, "data_origin": data_origin,
            "scope": "DEMO-SYNTHETIC" if data_origin == "SYNTHETIC" else "SITE",
            "approval_status": "NOT_APPROVED_FOR_PLANT_USE",
            "dataset_rows": n, "dataset_hash": dataset_hash(pd.concat([X, y.rename("y")], axis=1)),
            "split": {"train": [int(tr[0]), int(tr[-1])], "validation": [int(va[0]), int(va[-1])],
                      "test": [int(te[0]), int(te[-1])], "method": "chronological contiguous blocks with embargo"},
            "metrics": res, "beats_baselines_on_test": bool(better), "promotion_rule": f"test RMSE <= {1 - PROMOTE_MARGIN:.2f} x best baseline", "feature_importance": imp, "drift": drift,
            "ood_threshold_mahalanobis": hf.ood_threshold, "interval_half_width_95": hf.q95,
            "label": ("TRAINED ON SIMULATOR-GENERATED SYNTHETIC DATA - demonstrates the pipeline only; "
                      "not a plant model; must be retrained and validated on site data.")}
    meta = registry.register(hf, meta)
    return {"status": "TRAINED", **meta}


def train_all(df: pd.DataFrame, registry: ModelRegistry, data_origin: str = "SYNTHETIC") -> List[Dict[str, object]]:
    out = []
    for target, hz in (("hmt", 1.0), ("hmt", 2.0), ("hmt", 4.0), ("si", 2.0)):
        out.append(train_target(df, target, hz, registry, data_origin))
    return out
