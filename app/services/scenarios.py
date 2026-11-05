"""What-if scenario library and runner.

Every scenario runs a BASELINE and a CASE from the same initial twin state with identical
numerics, so differences are attributable to the change. Results include trajectories,
before/after balances (element residuals at every step), active constraints and a provenance
label (SYNTHETIC unless the twin is calibrated to site data).
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Callable, Dict, List, Optional

import numpy as np

from ..physics import outputs as out
from ..physics.burden import ChemistryError
from ..physics.material_balance import max_rel_residual

KPI_KEYS = ["hmt_C", "hm_si_pct", "hm_s_pct", "production_t_d", "coke_rate_kg_tHM", "fuel_rate_kg_tHM", "pci_rate_kg_tHM",
            "eta_CO_pct", "top_gas_T_C", "pressure_drop_kPa", "blast_pressure_kPa_g", "raft_C", "slag_rate_kg_tHM",
            "slag_B2", "dripping_zone_height_m", "solution_loss_kgC_tHM", "heat_loss_total_MW", "active_coke_T_C",
            # compositions (hot metal, slag, top gas) and operating inputs - full time trajectories
            "hm_c_pct", "hm_mn_pct", "hm_p_pct", "slag_FeO_pct", "slag_Al2O3_pct", "slag_viscosity_Pa_s",
            "top_CO_pct", "top_CO2_pct", "top_H2_pct", "top_N2_pct", "eta_H2_pct", "pci_burnout_pct",
            "hearth_metal_level_m", "hearth_slag_level_m", "blast_volume_Nm3_min", "blast_temp_C", "o2_enrichment_pct",
            "pci_t_h", "productivity_t_m3_d", "thermal_reserve_T_C", "direct_reduction_degree", "top_gas_Nm3_tHM"]

LIBRARY: List[dict] = [
    {"id": "S01", "name": "Coke-to-ore ratio +4 %", "type": "simulation", "duration_h": 20, "expected_lag_h": "6-10",
     "description": "Coke batch +4 % at constant ore batch: delayed heating, Si rise, lower productivity per coke.",
     "changes": [{"at_h": 1.0, "op": {"coke_batch_t": ["mul", 1.04]}}]},
    {"id": "S01b", "name": "Coke-to-ore ratio -4 %", "type": "simulation", "duration_h": 20, "expected_lag_h": "6-10",
     "description": "Coke batch -4 %: furnace cools after the burden travel time; cohesive zone descends.",
     "changes": [{"at_h": 1.0, "op": {"coke_batch_t": ["mul", 0.96]}}]},
    {"id": "S02", "name": "PCI +12 % at constant oxygen", "type": "simulation", "duration_h": 14, "expected_lag_h": "0.5-4",
     "description": "More coal at the tuyeres: RAFT falls, burnout falls, coke replacement after hours.",
     "changes": [{"at_h": 1.0, "op": {"pci_t_h": ["mul", 1.12]}}]},
    {"id": "S02b", "name": "PCI +12 % with O2 +1.0 %", "type": "simulation", "duration_h": 14, "expected_lag_h": "0.5-4",
     "description": "Oxygen compensates the RAFT drop of higher PCI.",
     "changes": [{"at_h": 1.0, "op": {"pci_t_h": ["mul", 1.12], "o2_enrichment_pct": ["add", 1.0]}}]},
    {"id": "S03", "name": "Hot blast temperature +50 degC", "type": "simulation", "duration_h": 12, "expected_lag_h": "0.5-3",
     "description": "Sensible heat to the raceway: RAFT and HMT rise quickly.",
     "changes": [{"at_h": 1.0, "op": {"blast_temp_C": ["add", 50.0]}}]},
    {"id": "S03b", "name": "Oxygen enrichment +1.5 %", "type": "simulation", "duration_h": 12, "expected_lag_h": "0.5-3",
     "description": "Higher RAFT, lower bosh gas volume per tHM, higher productivity.",
     "changes": [{"at_h": 1.0, "op": {"o2_enrichment_pct": ["add", 1.5]}}]},
    {"id": "S03c", "name": "Blast moisture +10 g/Nm3", "type": "simulation", "duration_h": 12, "expected_lag_h": "0.5-3",
     "description": "Endothermic water-gas reaction cools the raceway; more H2 in bosh gas.",
     "changes": [{"at_h": 1.0, "op": {"blast_moisture_g_Nm3": ["add", 10.0]}}]},
    {"id": "S03d", "name": "Blast volume -5 %", "type": "simulation", "duration_h": 12, "expected_lag_h": "0.5-6",
     "description": "Lower wind rate: production falls, residence time increases.",
     "changes": [{"at_h": 1.0, "op": {"blast_volume_Nm3_min": ["mul", 0.95]}}]},
    {"id": "S04", "name": "Pellet share 20 -> 40 % (replacing sinter)", "type": "simulation", "duration_h": 20, "expected_lag_h": "6-10",
     "description": "Higher Fe grade, lower basicity, smaller particles; slag rate and permeability change.",
     "changes": [{"at_h": 1.0, "op": {"burden_mix": ["set", {"sinter": 0.50, "pellet": 0.40, "lump": 0.10, "hbi": 0.0, "dri": 0.0}]}}]},
    {"id": "S05", "name": "HBI 10 % of ore batch, coke -5 %", "type": "simulation", "duration_h": 20, "expected_lag_h": "6-10",
     "description": "Pre-reduced metallic burden: less reduction work and gas, lower coke demand.",
     "changes": [{"at_h": 1.0, "op": {"burden_mix": ["set", {"sinter": 0.62, "pellet": 0.18, "lump": 0.10, "hbi": 0.10, "dri": 0.0}],
                                      "coke_batch_t": ["mul", 0.95]}}]},
    {"id": "S06", "name": "Lower-grade sinter (Fe -1.5 %, SiO2 +0.6 %, moisture +1 %)", "type": "simulation", "duration_h": 20,
     "expected_lag_h": "6-10", "description": "More gangue: slag volume and fuel demand rise.",
     "changes": [{"at_h": 1.0, "material": {"sinter": {"chem": {"Fe_total": ["add", -1.5], "SiO2": ["add", 0.6]}, "moisture_pct": ["add", 1.0]}}}]},
    {"id": "S07", "name": "Coke quality loss (ash +2 %, CRI 24 -> 30)", "type": "simulation", "duration_h": 20, "expected_lag_h": "4-10",
     "description": "More solution loss and slag; thermal-reserve temperature falls.",
     "changes": [{"at_h": 1.0, "coke": {"ash_pct": ["add", 2.0], "C_pct": ["add", -2.0], "CRI": ["set", 30.0]}}]},
    {"id": "S08", "name": "Permeability deterioration (fines, +25 % bed resistance)", "type": "simulation", "duration_h": 8,
     "expected_lag_h": "0-1", "description": "Pressure drop rises; MPC must respect the hard dP limit or suppress advice.",
     "changes": [{"at_h": 1.0, "calibration": {"permeability_mult": ["mul", 1.25]}}]},
    {"id": "S09", "name": "Delayed tapping (open level +0.8 m)", "type": "simulation", "duration_h": 10, "expected_lag_h": "1-4",
     "description": "Hearth level rises, liquid inventory and pressure behaviour change; lab results arrive late.",
     "changes": [{"at_h": 0.5, "tapping": {"open_level_above_taphole_m": ["add", 0.8], "lab_delay_min": ["set", 60.0]}}]},
    {"id": "S10", "name": "Instrument faults: stuck thermocouple, CO2 analyser drift, top-pressure dropout", "type": "sensor",
     "duration_h": 8, "expected_lag_h": "0", "description": "Data-quality engine flags tags; advice is suppressed.",
     "faults": [{"kind": "STUCK", "tag": "HTC_SUMP", "start_h": 1.0}, {"kind": "DRIFT", "tag": "TG_CO2", "magnitude": 0.5, "start_h": 1.0},
                {"kind": "DROPOUT", "tag": "TG_PRESS", "start_h": 3.0, "end_h": 5.0}]},
    {"id": "S11", "name": "Stove change dip, oxygen trip and 4 tuyeres closed", "type": "simulation", "duration_h": 10,
     "expected_lag_h": "0-2", "description": "Blast temperature -80 degC for 20 min, O2 off for 1 h, 4 tuyeres blanked for 3 h.",
     "changes": [{"at_h": 1.0, "op": {"blast_temp_C": ["add", -80.0]}}, {"at_h": 1.33, "op": {"blast_temp_C": ["add", 80.0]}},
                 {"at_h": 3.0, "op": {"o2_enrichment_pct": ["set", 0.0]}}, {"at_h": 4.0, "op": {"o2_enrichment_pct": ["restore"]}},
                 {"at_h": 5.0, "op": {"n_tuyeres_closed": ["set", 4]}}, {"at_h": 8.0, "op": {"n_tuyeres_closed": ["set", 0]}}]},
    {"id": "S12", "name": "Coke price +20 %: cost-HMT Pareto (MPC economics)", "type": "pareto", "duration_h": 0,
     "expected_lag_h": "-", "description": "Linear MPC solved with increasing economic weight: trade-off of fuel cost vs HMT tracking."},
    {"id": "S13", "name": "Simultaneous disturbances with tight hard limits", "type": "mpc_infeasible", "duration_h": 0,
     "expected_lag_h": "-", "description": "Hard dP limit set below the current value: optimiser must report NO_FEASIBLE_SOLUTION."},
    {"id": "S14", "name": "Transition to thermal instability (coke -8 %)", "type": "simulation", "duration_h": 24,
     "expected_lag_h": "6-14", "description": "Large coke deficit: cooling, Si collapse, cohesive zone falls; alerts and wider uncertainty.",
     "changes": [{"at_h": 1.0, "op": {"coke_batch_t": ["mul", 0.92]}}]},
]
LIB_INDEX = {s["id"]: s for s in LIBRARY}


def _apply(target: dict, key: str, spec, base: dict):
    kind = spec[0]
    if kind == "mul":
        target[key] = target[key] * spec[1]
    elif kind == "add":
        target[key] = target[key] + spec[1]
    elif kind == "set":
        target[key] = copy.deepcopy(spec[1])
    elif kind == "restore":
        target[key] = copy.deepcopy(base[key])


def apply_change(m, ch: dict, base_op: dict, base_cfg: dict):
    for k, spec in ch.get("op", {}).items():
        _apply(m.op, k, spec, base_op)
    for mat, d in ch.get("material", {}).items():
        md = m.cfg["materials"][mat]
        for k, spec in d.items():
            if k == "chem":
                for ck, cs in spec.items():
                    _apply(md["chem"], ck, cs, base_cfg["materials"][mat]["chem"])
            else:
                _apply(md, k, spec, base_cfg["materials"][mat])
        m._prepare_materials()
    for k, spec in ch.get("coke", {}).items():
        _apply(m.cfg["coke"], k, spec, base_cfg["coke"])
        m._prepare_materials()
    for k, spec in ch.get("calibration", {}).items():
        _apply(m.cfg["calibration"], k, spec, base_cfg["calibration"])
    for k, spec in ch.get("tapping", {}).items():
        _apply(m.cfg["tapping"], k, spec, base_cfg["tapping"])


def _sample(m) -> Dict[str, Optional[float]]:
    k = out.kpis(m)
    return {key: k[key]["value"] for key in KPI_KEYS}


def _cum(m) -> Dict[str, float]:
    """Cumulative input/output weights since the start of the run [t or kNm3]."""
    c = {f"in.{k}": float(v) for k, v in m.cum["in"].items()}
    c.update({f"out.{k}": float(v) for k, v in m.cum["out"].items()})
    return c


def run_simulation(base_model, spec: dict, dt: float = 240.0, progress: Optional[Callable] = None,
                   sample_every_h: float = 0.25) -> Dict[str, object]:
    t0 = time.time()
    dur = float(spec.get("duration_h", 12))
    runs = {}
    for label in ("baseline", "case"):
        m = base_model.clone()
        base_op = copy.deepcopy(m.op)
        base_cfg = copy.deepcopy(m.cfg)
        changes = sorted(spec.get("changes", []), key=lambda c: c["at_h"]) if label == "case" else []
        n = int(dur * 3600 / dt)
        every = max(int(sample_every_h * 3600 / dt), 1)
        traj = {"t_h": [], **{k: [] for k in KPI_KEYS}}
        cum0 = _cum(m)
        cum_traj: Dict[str, List[float]] = {}
        res_max = 0.0
        e_res_max = 0.0
        status = "COMPLETED"
        ci = 0
        for i in range(n):
            th = i * dt / 3600.0
            while ci < len(changes) and changes[ci]["at_h"] <= th + 1e-9:
                try:
                    apply_change(m, changes[ci], base_op, base_cfg)
                except ChemistryError as e:
                    return {"status": "FAILED", "error": f"invalid chemistry: {e}"}
                ci += 1
            try:
                rec = m.step(dt)
            except Exception as e:
                status = f"MODEL_ENVELOPE_EXIT at t={th:.1f} h: {e}"
                break
            res_max = max(res_max, max_rel_residual(rec))
            e_res_max = max(e_res_max, abs(rec.E_in - rec.E_out - rec.E_acc) / max(abs(rec.E_in) + abs(rec.E_out), 1.0))
            if i % every == 0 or i == n - 1:
                s = _sample(m)
                traj["t_h"].append(round((i + 1) * dt / 3600.0, 3))
                for kk in KPI_KEYS:
                    traj[kk].append(None if s[kk] is None else round(float(s[kk]), 5))
                cu = _cum(m)
                for kk, v in cu.items():
                    series = cum_traj.setdefault(kk, [0.0] * (len(traj["t_h"]) - 1))
                    series.append(round(v - cum0.get(kk, 0.0), 4))
            if progress and i % 20 == 0:
                progress((0.5 if label == "case" else 0.0) + 0.5 * i / n, f"{label} {th:.1f}/{dur:.0f} h")
        runs[label] = {"trajectory": traj, "cumulative_t": cum_traj, "status": status, "max_rel_element_residual": res_max,
                       "max_rel_energy_residual": e_res_max, "final": _sample(m) if status == "COMPLETED" else None,
                       "balances": out.balances(m)["step"] if status == "COMPLETED" else None,
                       "streams": out.streams(m) if status == "COMPLETED" else None,
                       "heat_balance": out.heat_balance(m) if status == "COMPLETED" else None}
    fin_b, fin_c = runs["baseline"]["final"], runs["case"]["final"]
    delta = {k: (fin_c[k] - fin_b[k]) if fin_b and fin_c and fin_b[k] is not None and fin_c[k] is not None else None
             for k in KPI_KEYS}
    return {"status": "SUCCEEDED" if runs["case"]["status"] == "COMPLETED" else "PARTIAL", "scenario": spec,
            "runs": runs, "delta_final": delta, "seconds": time.time() - t0, "data_origin": "SYNTHETIC",
            "provenance": "Physics twin clone (illustrative calibration) - not plant measurements",
            "units": {"cumulative_t": "t (solids/liquids) or kNm3 (gases) since run start",
                      "trajectory": "see /api/v1/twin/state for per-KPI units"},
            "input_hash": hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()}
