"""Elemental and mass bookkeeping helpers and balance reports."""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from .species import ELEMENTS, ATOMIC_MASS, GAS, SOLID, LIQ, E_GAS, E_SOLID, E_LIQ, MM_GAS, MM_SOLID, MM_LIQ, \
    TRUE_GAS_MASK, SPECIES

AM = np.array([ATOMIC_MASS[e] for e in ELEMENTS]) / 1000.0  # kg/mol
BALANCE_ELEMENTS = ["Fe", "C", "O", "H", "N", "Si", "Al", "Ca", "Mg", "Mn", "P", "S", "K", "Na", "Zn"]


def el_gas(F):
    return E_GAS @ F


def el_solid(n):
    return E_SOLID @ n


def el_liq(n):
    return E_LIQ @ n


def balance_table(inp: np.ndarray, out: np.ndarray, acc: np.ndarray, tol_pct: float = 0.5) -> List[dict]:
    """Per-element balance in kg with residual (in - out - acc) and status vs tolerance."""
    rows = []
    for i, e in enumerate(ELEMENTS):
        m_in, m_out, m_acc = inp[i] * AM[i], out[i] * AM[i], acc[i] * AM[i]
        res = m_in - m_out - m_acc
        denom = max(abs(m_in), abs(m_out), 1e-12)
        pct = 100.0 * res / denom
        rows.append({"element": e, "in_kg": m_in, "out_kg": m_out, "accumulation_kg": m_acc, "residual_kg": res,
                     "residual_pct_of_throughput": pct, "denominator_kg": denom,
                     "status": "OK" if abs(pct) <= tol_pct or denom < 1e-6 else "FAIL"})
    return rows


def gas_composition(F: np.ndarray, dry: bool = True) -> Dict[str, float]:
    keys = [k for k, m in zip(GAS, TRUE_GAS_MASK) if m and (k != "H2O" or not dry)]
    vals = np.array([F[GAS.index(k)] for k in keys])
    tot = max(vals.sum(), 1e-12)
    return {k: float(100 * v / tot) for k, v in zip(keys, vals)}


def species_mass(n: np.ndarray, phase: str) -> float:
    mm = {"gas": MM_GAS, "solid": MM_SOLID, "liq": MM_LIQ}[phase]
    return float(np.sum(n * mm))


def solid_breakdown(n: np.ndarray) -> Dict[str, float]:
    """kg of each solid species."""
    return {k: float(n[i] * MM_SOLID[i]) for i, k in enumerate(SOLID) if n[i] * MM_SOLID[i] > 1e-9}


def solid_element_wt(n: np.ndarray) -> Dict[str, float]:
    el = el_solid(n) * AM
    tot = max(float(np.sum(n * MM_SOLID)), 1e-12)
    return {e: float(100 * el[i] / tot) for i, e in enumerate(ELEMENTS) if el[i] > 0}


def max_rel_residual(rec) -> float:
    """Largest per-element relative residual |in - out - acc| / max(|in| + |out| + |acc|, 1 mol) of one
    step record. The 1-mol floor keeps round-off (~1e-11 mol) on trace elements with zero flow in a
    given step from appearing as a large *relative* error; real violations are orders larger."""
    res = rec.el_in - rec.el_out - rec.el_acc
    den = np.maximum(np.abs(rec.el_in) + np.abs(rec.el_out) + np.abs(rec.el_acc), 1.0)
    return float(np.max(np.abs(res) / den))
