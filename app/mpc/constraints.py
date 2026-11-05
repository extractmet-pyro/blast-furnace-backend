"""MPC constraint set built from the versioned plant configuration.

* MV bounds = equipment envelope (hard, never softened); availability flags remove an MV.
* MV rate limits per 15 min (scaled to each move block).
* CV limits: 'soft' constraints only where the configuration explicitly marks them soft;
  hard limits (e.g. maximum bed pressure drop) are enforced without slack - if they cannot be
  met the optimiser reports NO_FEASIBLE_SOLUTION instead of silently relaxing them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MVLimit:
    name: str
    lo: float
    hi: float
    rate_per_15min: float
    available: bool = True
    locked_reason: Optional[str] = None


@dataclass
class CVLimit:
    name: str
    lo: Optional[float]
    hi: Optional[float]
    soft: bool
    unit: str
    lo_hard: Optional[float] = None
    hi_hard: Optional[float] = None


@dataclass
class ConstraintSet:
    mvs: Dict[str, MVLimit] = field(default_factory=dict)
    cvs: Dict[str, CVLimit] = field(default_factory=dict)

    def as_dict(self):
        return {"mvs": {k: vars(v) for k, v in self.mvs.items()}, "cvs": {k: vars(v) for k, v in self.cvs.items()}}


def build_constraints(cfg: dict, op: dict, availability: Optional[Dict[str, bool]] = None,
                      locks: Optional[Dict[str, str]] = None) -> ConstraintSet:
    lim = cfg["equipment_limits"]
    rl = cfg["rate_limits_per_15min"]
    availability = availability or {}
    locks = locks or {}
    cs = ConstraintSet()
    oc_lo, oc_hi = lim.get("ore_coke_ratio", [3.2, 6.0])
    ore = op["ore_batch_t"]
    mv_map = {
        "blast_temp_C": (lim["blast_temp_C"], rl["blast_temp_C"]),
        "blast_moisture_g_Nm3": (lim["blast_moisture_g_Nm3"], rl["blast_moisture_g_Nm3"]),
        "o2_enrichment_pct": (lim["o2_enrichment_pct"], rl["o2_enrichment_pct"]),
        "pci_t_h": (lim["pci_t_h"], rl["pci_t_h"]),
        "blast_volume_Nm3_min": (lim["blast_volume_Nm3_min"], rl["blast_volume_Nm3_min"]),
        "coke_batch_t": ([ore / oc_hi, ore / oc_lo], op["coke_batch_t"] * rl["ore_coke_ratio"] / (ore / op["coke_batch_t"])),
    }
    # Default MV availability comes from the plant configuration (`mpc_default_available`). The
    # coke batch (ore/coke ratio) is OFF by default: its identified short-horizon gain on HMT in the
    # fast model is weak and uncertain (documented in MODEL_ASSUMPTIONS.md); burden changes stay an
    # operator/metallurgist decision unless explicitly enabled per request.
    default_av = cfg.get("mpc_default_available", {"coke_batch_t": False})
    for k, ((lo, hi), rate) in mv_map.items():
        av = availability.get(k, default_av.get(k, True)) and k not in locks
        reason = locks.get(k) or (None if av else "not enabled for automatic advice (configuration default)")
        cs.mvs[k] = MVLimit(k, float(lo), float(hi), float(abs(rate)), av, reason)
    for c in cfg["constraints"]:
        name = c["name"]
        key = {"pressure_drop_kPa": "pressure_drop_kPa", "hmt_C": "hmt_C", "si_pct": "hm_si_pct", "raft_C": "raft_C",
               "top_gas_temp_C": "top_gas_T_C"}.get(name, name)
        cs.cvs[key] = CVLimit(key, c.get("low_alarm"), c.get("high_alarm"), bool(c.get("soft", True)), c["unit"],
                              c.get("low_hard"), c.get("high_hard"))
    return cs
