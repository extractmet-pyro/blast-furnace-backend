"""Data reconciliation by constrained weighted least squares (WLS) with gross-error detection.

Measured stream flows x (blast, oxygen, PCI, coke, top gas, hot metal) are adjusted minimally,
weighted by their variances, so that the steady-state N, C, O and H balances close:

    min (x - m)' V^-1 (x - m)   s.t.  A x = b         =>   x* = m - V A' (A V A')^-1 (A m - b)

Gross errors: global chi-square test on the constraint residual and maximum-power normalised
residual (measurement) test -> suspected sensor-bias flags. Raw values, adjustments and weights
are returned for the immutable audit trail.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np
from scipy import stats

MOLC = 12.011


def reconcile(meas: Dict[str, float], sd: Dict[str, float], comp: Dict[str, Dict[str, float]],
              alpha: float = 0.05) -> Dict[str, object]:
    """
    meas: measured flows in kmol/h (gases) or t/h (solids) for streams in `comp`.
    comp: element content per unit flow, e.g. comp['blast'] = {'N': 1.58, 'O': 0.42}.
    Constraint: sum_in - sum_out = 0 for each element (outputs negative coefficients).
    """
    names = list(meas.keys())
    elements = sorted({e for c in comp.values() for e in c})
    sign = {k: (-1.0 if comp[k].get("_out", False) else 1.0) for k in names}
    A = np.array([[sign[n] * comp[n].get(e, 0.0) for n in names] for e in elements])
    m = np.array([meas[n] for n in names], dtype=float)
    V = np.diag([max(sd[n], 1e-9) ** 2 for n in names])
    r = A @ m
    S = A @ V @ A.T
    S_inv = np.linalg.pinv(S)
    x = m - V @ A.T @ S_inv @ r
    adj = x - m
    chi2 = float(r @ S_inv @ r)
    dof = int(np.linalg.matrix_rank(A))
    crit = float(stats.chi2.ppf(1 - alpha, max(dof, 1)))
    Va = V @ A.T @ S_inv @ A @ V
    z = np.array([abs(adj[i]) / np.sqrt(max(Va[i, i], 1e-18)) for i in range(len(names))])
    z_crit = float(stats.norm.ppf(1 - alpha / (2 * len(names))))
    flags = [names[i] for i in range(len(names)) if z[i] > z_crit and chi2 > crit]
    return {"streams": [{"stream": n, "measured": float(m[i]), "reconciled": float(x[i]), "adjustment": float(adj[i]),
                         "adjustment_pct": float(100 * adj[i] / max(abs(m[i]), 1e-9)), "sd": float(sd[n]),
                         "normalised_residual": float(z[i])} for i, n in enumerate(names)],
            "elements": elements, "constraint_residual_before": dict(zip(elements, r.round(6).tolist())),
            "constraint_residual_after": dict(zip(elements, (A @ x).round(9).tolist())),
            "chi2": chi2, "chi2_critical": crit, "dof": dof, "gross_error_suspected": chi2 > crit,
            "suspect_streams": flags, "method": "linear WLS (Kuehn & Davidson 1961) + global/measurement tests"}


def bf_reconciliation(tags: Dict[str, float], model_streams: Dict[str, float]) -> Dict[str, object]:
    """Build the BF N/C/O balance from tag values (latest GOOD values) and run WLS reconciliation.

    Streams (kmol/h): blast_air (N2/O2), oxygen, blast_H2O, pci_C, coke_C (from charging rate),
    top_gas (dry) with measured composition, hot metal C (t/h * C%)."""
    V_b = tags["BL_FLOW"] * 60.0 / 22.414  # kmol/h dry blast
    y = 0.2095 + tags["BL_O2"] / 100.0
    pur = 0.995
    V_ox = V_b * (y - 0.2095) / (pur - 0.2095)
    V_air = V_b - V_ox
    co, co2, h2 = tags["TG_CO"] / 100, tags["TG_CO2"] / 100, tags["TG_H2"] / 100
    n2 = max(1 - co - co2 - h2, 0.3)
    pci_C = tags["PCI_RATE"] * 1000 * model_streams.get("pci_C_frac", 0.78) / MOLC  # kmol/h
    coke_C = model_streams["coke_C_kmol_h"]
    hm_C = model_streams["hm_C_kmol_h"]
    tg = model_streams.get("top_gas_dry_kmol_h_guess", V_air * 0.7905 / n2)
    meas = {"air": V_air, "oxygen": V_ox, "pci_C": pci_C, "coke_C": coke_C, "top_gas": tg, "hm_C": hm_C}
    sd = {"air": 0.015 * V_air, "oxygen": 0.02 * max(V_ox, 1.0), "pci_C": 0.03 * pci_C, "coke_C": 0.04 * coke_C,
          "top_gas": 0.05 * tg, "hm_C": 0.05 * hm_C}
    n_add = model_streams.get("other_N_kmol_h", 0.0)
    comp = {"air": {"N": 2 * 0.7905, "O": 2 * 0.2095}, "oxygen": {"O": 2 * pur, "N": 2 * (1 - pur)},
            "pci_C": {"C": 1.0}, "coke_C": {"C": 1.0},
            "top_gas": {"N": 2 * n2, "C": co + co2, "_out": True},
            "hm_C": {"C": 1.0, "_out": True}}
    res = reconcile(meas, sd, comp)
    res["note"] = ("N and C balances only (O and H involve unmeasured burden oxygen and water). Top gas flow is "
                   "weakly measured and is chiefly determined by the N2 tie-element.")
    res["other_N_kmol_h_ignored"] = n_add
    return res
