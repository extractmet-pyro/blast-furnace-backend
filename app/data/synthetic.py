"""Synthetic demonstration plant ('Industry-X', SYNTHETIC - not a real furnace).

A separate physics model instance plays the role of the plant. It deliberately differs from the
twin's nominal parameters (hidden truth: wall heat-loss coefficient, coke reactivity, burden
reducibility, permeability, PCI burnout) and is driven by stochastic disturbances (coke moisture,
burden chemistry, blast humidity). Its tags are produced through a sensor layer with noise,
bias, drift, dropouts, stuck values, laboratory delays and timestamp disorder, so that data
quality checks, reconciliation and the observer have something real to do.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from ..physics.integrator import BlastFurnaceModel
from ..physics import outputs as out
from .tag_mapping import TAG_INDEX, TAGS

EPOCH = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

HIDDEN_TRUTH = {
    "wall_h_in_mult": 1.08,
    "coke_CRI": 25.5,
    "kinetic_multipliers": {"R_wus_CO": 0.95, "R_wus_H2": 0.95},
    "permeability_mult_factor": 1.06,
    "pci_burnout_eta_max": 0.905,
}


@dataclass
class Reading:
    tag: str
    value: Optional[float]
    unit: str
    ts_sensor: str
    ts_ingest: str
    source: str = "SYNTHETIC"
    raw: Optional[float] = None
    note: str = ""

    def as_dict(self):
        return {"tag": self.tag, "value": self.value, "unit": self.unit, "ts_sensor": self.ts_sensor,
                "ts_ingest": self.ts_ingest, "source": self.source, "note": self.note}


@dataclass
class FaultSpec:
    kind: str           # DRIFT | BIAS | STUCK | DROPOUT | DISORDER
    tag: str
    magnitude: float = 0.0   # per hour for DRIFT, absolute for BIAS
    start_h: float = 0.0
    end_h: float = 1e9


def iso(t_s: float) -> str:
    return (EPOCH + timedelta(seconds=float(t_s))).isoformat().replace("+00:00", "Z")


def truth_config(cfg: dict) -> dict:
    c = copy.deepcopy(cfg)
    cal = c["calibration"]
    cal["wall_h_in_mult"] = cal.get("wall_h_in_mult", 1.0) * HIDDEN_TRUTH["wall_h_in_mult"]
    c["coke"]["CRI"] = HIDDEN_TRUTH["coke_CRI"]
    km = dict(cal.get("kinetic_multipliers", {}))
    for k, v in HIDDEN_TRUTH["kinetic_multipliers"].items():
        km[k] = km.get(k, 1.0) * v
    cal["kinetic_multipliers"] = km
    cal["permeability_mult"] = cal.get("permeability_mult", 1.0) * HIDDEN_TRUTH["permeability_mult_factor"]
    cal["pci_burnout_eta_max"] = HIDDEN_TRUTH["pci_burnout_eta_max"]
    c["plant"]["data_origin"] = "SYNTHETIC"
    return c


class SyntheticPlant:
    def __init__(self, cfg: dict, seed: int = 7, model: Optional[BlastFurnaceModel] = None):
        self.cfg_nominal = copy.deepcopy(cfg)
        self.model = model or BlastFurnaceModel(truth_config(cfg), seed=seed)
        self.rng = np.random.default_rng(seed)
        self.faults: List[FaultSpec] = []
        self.pending_lab: List[Reading] = []
        self.stuck: Dict[str, float] = {}
        self.dist = {"coke_moisture": 0.0, "blast_moist": 0.0, "sinter_feo": 0.0}
        self.cast_was_active = False
        self.last_dip_t = -1e9

    # disturbances: bounded random walks (deterministic for a given seed)
    def _disturb(self, dt):
        r = self.rng
        for k, sd, lim in (("coke_moisture", 0.05, 1.0), ("blast_moist", 0.15, 2.5)):
            self.dist[k] = float(np.clip(0.995 * self.dist[k] + r.normal(0, sd) * math.sqrt(dt / 120.0), -lim, lim))
        base = self.cfg_nominal["coke"]["moisture_pct"]
        self.model.cfg["coke"]["moisture_pct"] = base + self.dist["coke_moisture"]
        # rebuild coke species vector only when moisture changes noticeably (cheap enough each step)
        from ..physics.burden import coke_species_per_kg
        self.model.mat["coke"]["vec"], _ = coke_species_per_kg(self.model.cfg["coke"])

    def apply_inputs(self, op: Dict[str, float]):
        for k, v in op.items():
            if k in self.model.op:
                self.model.op[k] = v

    def step(self, dt: Optional[float] = None) -> List[Reading]:
        dt = dt or self.model.dt
        self._disturb(dt)
        # actual blast humidity deviates from setpoint (measured by BL_MOIST)
        sp_moist = self.model.op["blast_moisture_g_Nm3"]
        self.model.op["blast_moisture_g_Nm3"] = max(sp_moist + self.dist["blast_moist"], 0.0)
        self.model.step(dt)
        actual_moist = self.model.op["blast_moisture_g_Nm3"]
        self.model.op["blast_moisture_g_Nm3"] = sp_moist
        return self._sense(actual_moist)

    # ------------------------------------------------------------------------------------
    def _sense(self, actual_moist: float) -> List[Reading]:
        m = self.model
        t = m.t
        k = out.kpis(m)
        tg = out.top_gas(m)
        now = iso(t)
        vals: Dict[str, Optional[float]] = {
            "BL_FLOW": m.op["blast_volume_Nm3_min"] if m.op.get("blast_on", True) else 0.0,
            "BL_TEMP": m.op["blast_temp_C"], "BL_PRESS": k["blast_pressure_kPa_g"]["value"],
            "BL_MOIST": actual_moist, "BL_O2": m.op["o2_enrichment_pct"], "PCI_RATE": m.op["pci_t_h"],
            "TG_CO": tg["dry_vol_pct"]["CO"], "TG_CO2": tg["dry_vol_pct"]["CO2"], "TG_H2": tg["dry_vol_pct"]["H2"],
            "TG_TEMP": self._lag("TG_TEMP", tg["T_C"], 600.0), "TG_PRESS": m.op["top_pressure_kPa_g"],
            "TUY_CLOSED": float(m.op.get("n_tuyeres_closed", 0)),
            "TUY_Q": (m.last_rw.Q_tuyere / 1e3) if m.last_rw else 0.0,
            "STOCKLINE": float(max(m.grid.H_work - getattr(m, "stockline_m", m.grid.H_work), 0.0) + m.cfg["geometry"]["stockline_m"]),
            "CHARGES_H": k["charges_per_h"]["value"], "COKE_BATCH": m.op["coke_batch_t"], "ORE_BATCH": m.op["ore_batch_t"],
        }
        # stave heat loads per zone
        zq = {}
        for w in m.walls:
            zq[w.zone] = zq.get(w.zone, 0.0) + (w.Q_water_last) / 1e3
        vals["ST_Q_BOSH"] = zq.get("bosh")
        vals["ST_Q_BELLY"] = zq.get("belly_lower_stack")
        vals["ST_Q_MID"] = zq.get("mid_stack")
        vals["ST_Q_UPPER"] = zq.get("upper_stack_throat")
        tc_cfg = m.cfg["walls"]
        vals["HTC_SUMP"] = m.w_sump.temperature_at_depth(tc_cfg["sump_wall"].get("thermocouple_depth_from_cold_face_m", 0.35)) - 273.15
        vals["HTC_BOTTOM"] = m.w_bottom.temperature_at_depth(tc_cfg["hearth_bottom"].get("thermocouple_depth_from_cold_face_m", 0.4)) - 273.15
        # shaft pressure taps at 25 %, 50 %, 75 % of the working height
        P = (m.P_faces - 101325.0) / 1000.0
        zf = m.grid.z_faces
        for tag, fr in (("SH_P_LOWER", 0.25), ("SH_P_MID", 0.5), ("SH_P_UPPER", 0.75)):
            vals[tag] = float(np.interp(fr * zf[-1], zf, P))
        if m.n_r > 1:
            vals["ABP_T_CENTER"] = float(m.Tg_faces[0, -1] - 273.15)
            vals["ABP_T_WALL"] = float(m.Tg_faces[-1, -1] - 273.15)
        if getattr(m, "last_rw", None) is not None:
            # L2 "calculated RAFT" as a DCS would report it: conventional formula on the measured blast
            vals["RAFT_CALC"] = float(m.last_rw.raft_std_C)
        vals["CAST_ACTIVE"] = 1.0 if m.hearth.cast.casting else 0.0
        readings: List[Reading] = []
        for tag, v in vals.items():
            td = TAG_INDEX.get(tag)
            if td is None or not td.installed:
                continue
            readings.append(self._make(tag, v, t, now))
        readings.append(Reading("DP_TOTAL", None if vals["BL_PRESS"] is None else vals["BL_PRESS"] - vals["TG_PRESS"],
                                "kPa", now, now, note="CALC from BL_PRESS - TG_PRESS"))
        readings.append(Reading("TG_N2", 100.0 - vals["TG_CO"] - vals["TG_CO2"] - vals["TG_H2"], "vol%", now, now,
                                note="CALC by difference"))
        # hot metal dip temperature during casts (every ~15 min)
        if m.hearth.cast.casting and t - self.last_dip_t >= 900.0:
            self.last_dip_t = t
            readings.append(self._make("HMT_DIP", m.hearth.T_hm - 273.15, t, now))
        # laboratory results: released after the lab delay, stamped with the sampling time
        for ev in m.last_record.events:
            if ev.get("type") == "SAMPLE":
                ts = iso(ev["t"])
                for tag, key in (("HM_SI", "Si"), ("HM_S", "S"), ("HM_C", "C"), ("HM_MN", "Mn"), ("HM_P", "P")):
                    td = TAG_INDEX[tag]
                    val = ev["hm_wt_pct"].get(key, 0.0) + self.rng.normal(0, td.noise_sd)
                    self.pending_lab.append(Reading(tag, float(max(val, 0.0)), td.unit, ts, iso(ev["t"] + td.lab_delay_min * 60)))
                w = ev["slag_wt_pct"]
                b2 = w.get("CaO", 0) / max(w.get("SiO2", 1e-9), 1e-9)
                for tag, val in (("SLAG_B2", b2), ("SLAG_AL2O3", w.get("Al2O3", 0.0)), ("SLAG_FEO", w.get("FeO", 0.0))):
                    td = TAG_INDEX[tag]
                    self.pending_lab.append(Reading(tag, float(max(val + self.rng.normal(0, td.noise_sd), 0.0)), td.unit, ts,
                                                    iso(ev["t"] + td.lab_delay_min * 60)))
            if ev.get("type") == "CAST_END":
                readings.append(Reading("HM_CAST_T", float(ev["hm_t"]), "t", iso(ev["t_end"]), now))
        due = [r for r in self.pending_lab if r.ts_ingest <= now]
        self.pending_lab = [r for r in self.pending_lab if r.ts_ingest > now]
        if any(f.kind == "DISORDER" for f in self._active_faults(t)):
            due = list(reversed(due))
        readings.extend(due)
        return readings

    def _lag(self, key: str, v: Optional[float], tau_s: float) -> Optional[float]:
        """First-order measurement lag (uptake mixing + thermocouple sheath) for averaged signals."""
        if v is None:
            return None
        if not hasattr(self, "_lag_state"):
            self._lag_state = {}
        prev = self._lag_state.get(key)
        if prev is None:
            self._lag_state[key] = float(v)
        else:
            a = 1.0 - np.exp(-self.model.dt / tau_s)
            self._lag_state[key] = prev + a * (float(v) - prev)
        return self._lag_state[key]

    def _active_faults(self, t):
        h = t / 3600.0
        return [f for f in self.faults if f.start_h <= h < f.end_h]

    def _make(self, tag: str, v: Optional[float], t: float, now: str) -> Reading:
        td = TAG_INDEX[tag]
        raw = v
        note = ""
        if v is not None and td.noise_sd > 0:
            v = float(v + self.rng.normal(0, td.noise_sd))
        for f in self._active_faults(t):
            if f.tag != tag:
                continue
            if f.kind == "DRIFT" and v is not None:
                v += f.magnitude * (t / 3600.0 - f.start_h)
                note = "injected drift"
            elif f.kind == "BIAS" and v is not None:
                v += f.magnitude
                note = "injected calibration bias"
            elif f.kind == "STUCK":
                self.stuck.setdefault(tag, v)
                v = self.stuck[tag]
                note = "injected stuck value"
            elif f.kind == "DROPOUT":
                v = None
                note = "injected dropout"
        if tag in self.stuck and not any(f.kind == "STUCK" and f.tag == tag for f in self._active_faults(t)):
            self.stuck.pop(tag, None)
        return Reading(tag, None if v is None else float(v), td.unit, now, now, raw=raw, note=note)
