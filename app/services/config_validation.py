"""Plant-configuration validation: JSON Schema (structure, types, ranges) + engineering
consistency checks + a trial construction/step of the physics model. A configuration that
fails any check is rejected with 422 and the full list of problems."""
from __future__ import annotations

import copy
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import List

import jsonschema

from ..core.errors import DomainError
from ..physics.burden import ChemistryError, ore_species_per_kg

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "config" / "plant_config.schema.json"


@lru_cache
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _finite(o, path="") -> List[str]:
    bad = []
    if isinstance(o, dict):
        for k, v in o.items():
            bad += _finite(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            bad += _finite(v, f"{path}[{i}]")
    elif isinstance(o, float) and not math.isfinite(o):
        bad.append(path)
    return bad


def validate_config(cfg_in: dict, trial_step: bool = True) -> dict:
    cfg = copy.deepcopy(cfg_in)
    problems: List[dict] = []
    for p in _finite(cfg):
        problems.append({"path": p, "error": "non-finite number"})
    v = jsonschema.Draft202012Validator(schema())
    for e in sorted(v.iter_errors(cfg), key=lambda e: list(e.path)):
        problems.append({"path": ".".join(str(x) for x in e.path), "error": e.message[:300]})
    if problems:
        raise DomainError("INVALID_CONFIG", "Configuration failed schema validation", 422, problems[:50])
    g, op, lim = cfg["geometry"], cfg["operation"], cfg["equipment_limits"]
    if g["throat_diameter_m"] >= g["belly_diameter_m"]:
        problems.append({"path": "geometry", "error": "throat diameter must be smaller than belly diameter"})
    if g["hearth_diameter_m"] >= g["belly_diameter_m"]:
        problems.append({"path": "geometry", "error": "hearth diameter must be smaller than belly diameter"})
    mix = op["burden_mix"]
    if abs(sum(mix.values()) - 1.0) > 1e-3:
        problems.append({"path": "operation.burden_mix", "error": f"fractions sum to {sum(mix.values()):.4f}, must be 1"})
    for k in mix:
        if k not in cfg["materials"]:
            problems.append({"path": f"operation.burden_mix.{k}", "error": "unknown material"})
    for k, (lo, hi) in ((k, v) for k, v in lim.items() if isinstance(v, list)):
        if lo > hi:
            problems.append({"path": f"equipment_limits.{k}", "error": "lower limit above upper limit"})
        if k in op and isinstance(op[k], (int, float)) and not (lo <= op[k] <= hi):
            problems.append({"path": f"operation.{k}", "error": f"setpoint {op[k]} outside equipment limits [{lo}, {hi}]"})
    oc = op["ore_batch_t"] / max(op["coke_batch_t"], 1e-9)
    if not (lim["ore_coke_ratio"][0] <= oc <= lim["ore_coke_ratio"][1]):
        problems.append({"path": "operation", "error": f"ore/coke ratio {oc:.2f} outside {lim['ore_coke_ratio']}"})
    for mid, md in cfg["materials"].items():
        try:
            ore_species_per_kg(md["chem"], md["moisture_pct"], md["kind"])
        except ChemistryError as e:
            problems.append({"path": f"materials.{mid}.chem", "error": str(e)})
        if md["melting_C"] <= md["softening_C"]:
            problems.append({"path": f"materials.{mid}", "error": "melting temperature must exceed softening temperature"})
    zones = sorted(cfg["walls"]["shaft"], key=lambda z: z["frac_from"])
    if abs(zones[0]["frac_from"]) > 1e-9 or abs(zones[-1]["frac_to"] - 1.0) > 1e-9 or any(
            abs(a["frac_to"] - b["frac_from"]) > 1e-9 for a, b in zip(zones, zones[1:])):
        problems.append({"path": "walls.shaft", "error": "wall zones must tile the working height 0..1 without gaps"})
    for c in cfg["constraints"]:
        for a, b in (("low_hard", "low_alarm"), ("high_alarm", "high_hard")):
            if c.get(a) is not None and c.get(b) is not None and c[a] > c[b]:
                problems.append({"path": f"constraints.{c['name']}", "error": f"{a} must not exceed {b}"})
    if problems:
        raise DomainError("INVALID_CONFIG", "Configuration failed engineering consistency checks", 422, problems[:50])
    if trial_step:
        from ..physics.integrator import BlastFurnaceModel
        from ..physics import outputs as out
        try:
            m = BlastFurnaceModel(cfg)
            m.step()
            if out.balances(m)["step"]["status"] != "OK":
                raise RuntimeError("conservation check failed on the trial step")
        except Exception as e:
            raise DomainError("INVALID_CONFIG", f"Physics model could not be built/stepped with this configuration: {e}", 422)
    return cfg
