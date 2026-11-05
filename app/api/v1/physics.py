"""Twin state, spatial fields, balances, profiles, refractory/hearth, observer, demo setpoints
and the thermochemistry/kinetics explorer."""
from __future__ import annotations

from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, Query

from ...core.errors import DomainError
from ...core.units import INPUT_UNITS, UnitError, convert
from ...data.synthetic import iso
from ...physics import outputs as out
from ...physics import thermodynamics as th
from ...physics.reactions import kinetics_table
from ...physics.species import SPECIES
from ...physics.heat_transfer import MATERIALS as REFRACTORY
from ...schemas import FaultRequest, SetpointRequest
from ...services.jobs import _json_safe
from ...services.reporting import downsample, provenance
from ...services.twin_service import STREAM_KPIS
from .deps import ERR, operator, ready_svc, viewer

router = APIRouter()

FIELD_ALIASES = {"T_g": "T_gas_C", "T_s": "T_solid_C", "RD": "reduction_degree_pct", "CO": "y_CO_pct", "CO2": "y_CO2_pct",
                 "H2": "y_H2_pct", "u_g": "gas_velocity_m_s", "ore": "ore_volume_fraction", "soft": "softening_index",
                 "P": "pressure_kPa_g"}


@router.get("/twin/state", tags=["twin"], summary="Current twin KPIs (with units/provenance), hearth, cohesive zone, top gas")
def twin_state(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        m = s.twin
        k = s.kpis()
        walls = k.pop("_walls_MW", {})
        return _json_safe({"provenance": provenance(s), "seq": s.seq, "kpis": k, "walls_MW": walls,
                           "hearth": out.hearth_state(m), "cohesive_zone": out.cohesive_zone(m), "top_gas": out.top_gas(m),
                           "raceway": (m.last_rw.diag if m.last_rw else None), "setpoints": s.setpoints,
                           "operation_twin": {kk: m.op[kk] for kk in INPUT_UNITS if kk in m.op}, "input_units": INPUT_UNITS,
                           "observer": {"theta": s.ekf.theta.tolist(), "updates": s.ekf.n_updates},
                           "forecast_cache": s.forecasts, "casting": m.hearth.cast.casting})


@router.get("/twin/fields", tags=["twin"], summary="Model-computed (r,z) fields for the 3D scene")
def twin_fields(field: Optional[str] = None, level: Optional[int] = Query(None, ge=0), s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        f = out.fields3d(s.twin)
        f["seq"] = s.seq
        f["t_utc"] = iso(s.data_t)
    if field:
        key = FIELD_ALIASES.get(field, field)
        if key not in f["fields"]:
            raise DomainError("UNKNOWN_FIELD", f"unknown field '{field}'", 422,
                              {"available": sorted(f["fields"]) + sorted(FIELD_ALIASES)})
        f["fields"] = {key: f["fields"][key]}
        f["units"] = {key: f["units"].get(key)}
    if level is not None:
        nz = len(f["z_faces_m"]) - 1
        if level >= nz + 1:
            raise DomainError("OUT_OF_RANGE", f"level must be < {nz + 1}", 422)
        f["fields"] = {k: [row[min(level, len(row) - 1)] for row in v] for k, v in f["fields"].items()}
        f["level"] = {"index": level, "z_bottom_m": f["z_faces_m"][min(level, nz - 1)]}
    return _json_safe(f)


@router.get("/twin/profiles", tags=["twin"], summary="Axial profiles (sector-averaged) incl. reaction rates and zones")
def twin_profiles(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(out.profiles(s.twin))


@router.get("/twin/balances", tags=["twin"], summary="Per-step elemental and energy balance with residuals")
def twin_balances(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        b = out.balances(s.twin)
        hist = list(s.twin_hist)[-360:]
        b["residual_history"] = {"t_utc": [r["t_utc"] for r in hist], "max_rel_element_residual": [r.get("el_res_max") for r in hist],
                                 "energy_residual_pct": [r.get("E_res_pct") for r in hist]}
        b["provenance"] = provenance(s)
        return _json_safe(b)


@router.get("/twin/streams", tags=["twin"], summary="Inputs/outputs: rates, compositions, cumulative weights")
def twin_streams(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe({"provenance": provenance(s), **out.streams(s.twin)})


@router.get("/twin/heat-balance", tags=["twin"])
def twin_heat_balance(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(out.heat_balance(s.twin))


@router.get("/twin/walls", tags=["twin"], summary="Refractory/cooling: layer temperatures, heat flux, lining proxy")
def twin_walls(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe({"segments": out.walls_payload(s.twin), "materials": REFRACTORY,
                           "method": "implicit 1-D layered conduction + water/shell convection + radiation"})


@router.get("/twin/hearth", tags=["twin"])
def twin_hearth(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(out.hearth_state(s.twin))


@router.get("/twin/geometry", tags=["twin"], summary="Furnace geometry, wall zones and model grid (for 2D/3D drawing)")
def twin_geometry(s=Depends(ready_svc), p=Depends(viewer)):
    return s.geometry()


@router.get("/twin/rist", tags=["twin"])
def twin_rist(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(out.rist(s.twin, out.specific(s.twin, 4.0)))


@router.get("/twin/observer", tags=["twin"], summary="Parameter EKF status + data reconciliation")
def twin_observer(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe({"ekf": s.ekf.status(), "reconciliation": s.recon})


@router.get("/twin/layers", tags=["twin"])
def twin_layers(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return {"layers": s.twin.layers.payload()}


@router.get("/twin/history", tags=["twin"], summary="Twin KPI trajectories (virtual sensors) with residuals")
def twin_history(hours: float = Query(12.0, gt=0, le=48), keys: Optional[str] = None,
                 max_points: int = Query(600, ge=10, le=5000), s=Depends(ready_svc), p=Depends(viewer)):
    want = [k.strip() for k in keys.split(",")] if keys else None
    allowed = set(STREAM_KPIS) | {"hmt_corrected_C", "si_corrected_pct", "casting", "el_res_max", "E_res_pct"}
    if want and any(k not in allowed for k in want):
        raise DomainError("UNKNOWN_KEY", "unknown history key", 422, {"allowed": sorted(allowed)})
    with s.lock:
        n = int(hours * 3600 / s.twin.dt)
        rows = list(s.twin_hist)[-n:]
    if want:
        rows = [{k: r.get(k) for k in ["t_s", "t_utc"] + want} for r in rows]
    return {"rows": downsample(rows, max_points), "total_rows": len(rows),
            "label": "MODEL (virtual sensor) values of the physics twin; *_corrected = physics + observer bias"}


@router.get("/twin/setpoints", tags=["twin"])
def get_setpoints(s=Depends(ready_svc), p=Depends(viewer)):
    return {"setpoints": s.setpoints, "limits": s.cfg["equipment_limits"], "rate_limits_per_15min": s.cfg["rate_limits_per_15min"],
            "units": INPUT_UNITS, "applies_to": "SYNTHETIC PLANT (demo)"}


@router.post("/twin/setpoints", tags=["twin"], responses=ERR,
             summary="DEMO ONLY: change synthetic-plant setpoints (units converted, envelope enforced)")
def post_setpoints(body: SetpointRequest, p=Depends(operator), s=Depends(ready_svc)):
    changes = {}
    for k, v in body.changes.items():
        if isinstance(v, dict) and set(v) >= {"value", "unit"} or hasattr(v, "unit"):
            val = v.value if hasattr(v, "value") else v["value"]
            unit = v.unit if hasattr(v, "unit") else v["unit"]
            canon = INPUT_UNITS.get(k)
            if canon is None:
                raise DomainError("UNIT_NOT_SUPPORTED", f"{k} does not accept a unit", 422)
            try:
                changes[k] = convert(float(val), unit, canon)
            except UnitError as e:
                raise DomainError("UNIT_ERROR", str(e), 422)
        else:
            changes[k] = v
    return s.set_setpoints(changes, p)


@router.post("/twin/faults", tags=["twin"], summary="DEMO ONLY: inject a sensor fault into the synthetic sensor layer")
def post_fault(body: FaultRequest, p=Depends(operator), s=Depends(ready_svc)):
    return s.inject_fault(body.model_dump(), p)


@router.delete("/twin/faults", tags=["twin"])
def clear_faults(p=Depends(operator), s=Depends(ready_svc)):
    s.clear_faults()
    s.audit(p.sub, p.role, "DEMO_FAULTS_CLEARED")
    return {"active_faults": []}


# ------------------------------------------------------------------- thermochemistry explorer
@router.get("/thermo/reactions", tags=["thermochemistry"], summary="Reaction catalogue: dH, dG, log K vs T, kinetics forms")
def thermo_reactions(T: Optional[str] = None):
    Ts = (900.0, 1100.0, 1300.0, 1500.0, 1800.0)
    if T:
        try:
            Ts = tuple(float(x) for x in T.split(","))
        except ValueError:
            raise DomainError("INVALID_T", "T must be a comma-separated list of kelvin values", 422)
        if not all(300 <= x <= 2800 for x in Ts) or len(Ts) > 12:
            raise DomainError("OUT_OF_RANGE", "temperatures must be within [300, 2800] K (max 12)", 422)
    return _json_safe({"table": th.reaction_catalogue_table(Ts)})


@router.get("/thermo/kinetics", tags=["thermochemistry"])
def thermo_kinetics(s=Depends(ready_svc)):
    rows = kinetics_table()
    T = np.linspace(900.0, 1900.0, 41)
    from ...physics.reactions import REACTIONS
    curves = {}
    mult = s.cfg["calibration"].get("kinetic_multipliers", {})
    for r in rows:
        kin = REACTIONS[r["name"]].kinetics
        if kin:
            curves[r["name"]] = (kin.k(T) * mult.get("R_" + r["name"], mult.get(r["name"], 1.0))).tolist()
    return _json_safe({"rows": rows, "arrhenius": {"T_K": T.tolist(), "k_per_s": curves},
                       "calibration_multipliers": mult,
                       "note": "Lumped literature-type kinetics; provisional entries await site calibration."})


@router.get("/thermo/ellingham", tags=["thermochemistry"])
def thermo_ellingham():
    return th.ellingham()


@router.get("/thermo/baur-glaessner", tags=["thermochemistry"])
def thermo_bg(P_bar: float = Query(1.0, gt=0.2, le=6.0)):
    return th.baur_glaessner(P_bar)


@router.get("/thermo/species", tags=["thermochemistry"])
def thermo_species():
    return {"species": [{"key": k, "phase": sp.phase, "formula": sp.formula, "molar_mass_g_mol": round(sp.molar_mass * 1000.0, 4),
                         "label": sp.label} for k, sp in SPECIES.items()]}


@router.get("/thermo/refractories", tags=["thermochemistry"], summary="Refractory and stave material properties used by the wall model")
def thermo_refractories():
    from ...physics.heat_transfer import MATERIALS as WALL_MATERIALS
    return {"materials": [{"key": k, "k_W_mK": v["k"], "rho_kg_m3": v["rho"], "cp_J_kgK": v["cp"],
                           "diffusivity_m2_s": v["k"] / (v["rho"] * v["cp"])} for k, v in WALL_MATERIALS.items()],
            "note": "Constant-property values (illustrative, typical vendor ranges); replace with supplier data sheets "
                    "for commissioning. Conductivity of carbon grades varies strongly with temperature and grade."}

