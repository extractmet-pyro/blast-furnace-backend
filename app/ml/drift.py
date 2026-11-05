"""Concept/data drift: population stability index (PSI) per feature, two-sample KS test and a
CUSUM on forecast residuals."""
from __future__ import annotations

from typing import Dict, List

import numpy as np
from scipy import stats


def psi(ref: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    ref = ref[np.isfinite(ref)]
    cur = cur[np.isfinite(cur)]
    if len(ref) < 20 or len(cur) < 20:
        return float("nan")
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    r = np.histogram(ref, edges)[0] / len(ref) + 1e-6
    c = np.histogram(np.clip(cur, edges[0], edges[-1]), edges)[0] / len(cur) + 1e-6
    return float(np.sum((c - r) * np.log(c / r)))


def drift_report(X_ref, X_cur, cols: List[str]) -> Dict[str, object]:
    rows = []
    for c in cols:
        a, b = X_ref[c].to_numpy(dtype=float), X_cur[c].to_numpy(dtype=float)
        p = psi(a, b)
        ks = stats.ks_2samp(a[np.isfinite(a)], b[np.isfinite(b)]) if len(a) > 5 and len(b) > 5 else None
        rows.append({"feature": c, "psi": p, "ks_p": float(ks.pvalue) if ks else None,
                     "status": "DRIFT" if (p == p and p > 0.25) else ("WATCH" if (p == p and p > 0.1) else "OK")})
    return {"features": sorted(rows, key=lambda r: -(r["psi"] if r["psi"] == r["psi"] else 0)),
            "drifted": [r["feature"] for r in rows if r["status"] == "DRIFT"],
            "watch": [r["feature"] for r in rows if r["status"] == "WATCH"],
            "rule": "PSI > 0.25 drift, 0.10-0.25 watch"}


def cusum(residuals: np.ndarray, k: float, h: float) -> Dict[str, object]:
    gp = gn = 0.0
    alarms = []
    for i, r in enumerate(residuals):
        gp = max(0.0, gp + r - k)
        gn = max(0.0, gn - r - k)
        if gp > h or gn > h:
            alarms.append(i)
            gp = gn = 0.0
    return {"alarms": alarms, "k": k, "h": h}
