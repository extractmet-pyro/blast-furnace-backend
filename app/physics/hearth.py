"""Hearth: hot-metal and slag inventories, slag-metal reactions, heat exchange, wall/bottom
losses and taphole casting dynamics.

Levels are computed assuming liquids occupy the deadman voids (porosity eps_dm).
Taphole flow (orifice/Bernoulli):  v = Cd * sqrt(2 dP / rho_mix),  dP = p_hearth,gauge +
rho g h_above_taphole; the taphole diameter erodes linearly during a cast. The drained phase
fraction follows the slag/metal interface position relative to the taphole.
Slag-metal: desulfurisation relaxes towards the L_S equilibrium distribution; desiliconisation
by (FeO)/(MnO); manganese relaxes towards a partition. The metal pool closes the energy balance
of the reactions; the slag pool keeps its temperature during species transfer.
Remaining-lining estimates are monitoring proxies (1150 degC isotherm), not life predictions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .species import LIQ, IDX_LIQ, TT_LIQ, TT_GAS, IDX_GAS, GAS, SPECIES, HM_MASK, SLAG_MASK, MM_LIQ
from .reactions import COMPILED
from .heat_transfer import WallSegment
from . import slag_metal as sm


@dataclass
class CastState:
    casting: bool = False
    cast_no: int = 0
    taphole: int = 0
    t_start: float = 0.0
    t_last_end: float = -1e9
    d_th: float = 0.05
    hm_t: float = 0.0
    slag_t: float = 0.0
    sampled: bool = False


@dataclass
class HearthStepResult:
    gas: np.ndarray
    H_gas: float
    tapped: np.ndarray
    H_tapped_hm: float
    H_tapped_sl: float
    Q_water: float
    Q_shell: float
    dE_walls: float
    Q_from_dm: float
    events: List[dict] = field(default_factory=list)


class Hearth:
    def __init__(self, cfg: dict, walls: dict):
        g = cfg["geometry"]
        self.cfg = cfg
        self.A = math.pi * g["hearth_diameter_m"] ** 2 / 4.0
        self.eps = g["deadman_porosity"]
        self.z_th = g["sump_depth_m"]                         # taphole height above hearth pad
        self.H_hearth = g["sump_depth_m"] + g["taphole_to_tuyere_m"]
        self.n_tapholes = g["n_tapholes"]
        self.d_th0 = g["taphole_diameter_m"]
        self.walls = walls                                    # 'sump_wall', 'hearth_bottom'
        self.n = np.zeros(len(LIQ))
        self.T_hm = 1773.0
        self.T_sl = 1793.0
        self.H_hm = 0.0
        self.H_sl = 0.0
        self.cast = CastState(d_th=self.d_th0)
        self.casts: List[dict] = []

    # ---------------------------------------------------------------------------------
    def initialise(self, T_hm=1773.0, metal_above_taphole_m=0.4, slag_m=0.25):
        w_hm = {"Fe_l": 94.25, "C_hm": 4.70, "Si_hm": 0.50, "Mn_hm": 0.32, "P_hm": 0.10, "S_hm": 0.03}
        w_sl = {"CaO_sl": 40.5, "SiO2_sl": 35.0, "Al2O3_sl": 14.5, "MgO_sl": 8.0, "FeO_sl": 0.45, "MnO_sl": 0.4,
                "CaS_sl": 2.0, "K2O_sl": 0.4, "Na2O_sl": 0.1, "P2O5_sl": 0.02}
        V_hm = self.A * self.eps * (self.z_th + metal_above_taphole_m)
        m_hm = V_hm * sm.RHO_HM
        V_sl = self.A * self.eps * slag_m
        m_sl = V_sl * sm.RHO_SLAG
        self.n[:] = 0.0
        for k, w in w_hm.items():
            self.n[IDX_LIQ[k]] = m_hm * w / 100 / SPECIES[k].molar_mass
        for k, w in w_sl.items():
            self.n[IDX_LIQ[k]] = m_sl * w / 100 / SPECIES[k].molar_mass
        self.T_hm, self.T_sl = T_hm, T_hm + 20.0
        self.H_hm = float(np.where(HM_MASK, self.n, 0) @ TT_LIQ.h(self.T_hm))
        self.H_sl = float(np.where(SLAG_MASK, self.n, 0) @ TT_LIQ.h(self.T_sl))
        for w in self.walls.values():
            w.init_steady(self.T_hm)

    def masses(self):
        m = self.n * MM_LIQ
        return float(np.sum(m[HM_MASK])), float(np.sum(m[SLAG_MASK]))

    def levels(self):
        m_hm, m_sl = self.masses()
        h_hm = m_hm / sm.RHO_HM / (self.A * self.eps)
        h_sl = m_sl / sm.RHO_SLAG / (self.A * self.eps)
        return h_hm, h_hm + h_sl

    def composition(self):
        n_hm = {k: float(self.n[IDX_LIQ[k]]) for k in LIQ if SPECIES[k].phase == "hm"}
        n_sl = {k: float(self.n[IDX_LIQ[k]]) for k in LIQ if SPECIES[k].phase == "slag"}
        return sm.hm_wt_pct(n_hm), sm.slag_wt_pct(n_sl), n_hm, n_sl

    def inventory_enthalpy(self):
        return self.H_hm + self.H_sl

    # ---------------------------------------------------------------------------------
    def step(self, liq_in: np.ndarray, H_liq_in: float, T_liq_in: float, T_dm: float, dt: float,
             p_gauge_Pa: float, t: float) -> HearthStepResult:
        cal = self.cfg["calibration"]
        events: List[dict] = []
        # (1) inflow: split enthalpy of the incoming liquid between metal and slag
        h_in = TT_LIQ.h(T_liq_in)
        H_in_hm = float(np.where(HM_MASK, liq_in, 0) @ h_in)
        H_in_sl = H_liq_in - H_in_hm
        self.n += liq_in * dt
        self.H_hm += H_in_hm * dt
        self.H_sl += H_in_sl * dt
        self._update_T()
        # (2) slag-metal reactions (metal pool absorbs reaction enthalpy)
        gas = np.zeros(len(GAS))
        H_before = self.H_hm + self.H_sl
        w_hm, w_sl, n_hm, n_sl = self.composition()
        m_hm, m_sl = self.masses()
        rates = {}
        if m_sl > 1.0 and m_hm > 1.0:
            ls = sm.sulfur_partition(w_sl, w_hm, self.T_hm)["L_S"]
            S_hm = self.n[IDX_LIQ["S_hm"]]
            S_tot = S_hm + self.n[IDX_LIQ["CaS_sl"]]
            S_eq = S_tot / (1.0 + ls * m_sl / m_hm)
            if S_hm > S_eq:
                x = min((S_hm - S_eq) * (1 - math.exp(-1.0e-3 * dt)), 0.5 * self.n[IDX_LIQ["CaO_sl"]],
                        0.5 * self.n[IDX_LIQ["C_hm"]])
                self._react("H_desulf", x, gas)
                rates["H_desulf"] = x / dt
            # [Si] + 2(FeO) -> (SiO2) + 2Fe acts only on FeO above the kinetic residual level
            n_feo_res = cal.get("slag_feo_residual_pct", 0.45) / 100.0 * m_sl / 0.0718444
            feo_x = max(self.n[IDX_LIQ["FeO_sl"]] - n_feo_res, 0.0)
            w_feo_x = max(w_sl.get("FeO", 0.0) - cal.get("slag_feo_residual_pct", 0.45), 0.0)
            x_si = cal["si_desi_k"] * dt * self.n[IDX_LIQ["Si_hm"]] * min(w_feo_x / 1.0, 2.0)
            x_si = min(x_si, 0.45 * feo_x, 0.3 * self.n[IDX_LIQ["Si_hm"]])
            self._react("H_desi_feo", x_si, gas)
            x_mn = cal["si_desi_k"] * dt * self.n[IDX_LIQ["Si_hm"]] * min(w_sl.get("MnO", 0.0) / 1.0, 2.0)
            x_mn = min(x_mn, 0.45 * self.n[IDX_LIQ["MnO_sl"]], 0.3 * self.n[IDX_LIQ["Si_hm"]])
            self._react("H_desi_mno", x_mn, gas)
            rates["H_desi"] = (x_si + x_mn) / dt
        H_sl_new = float(np.where(SLAG_MASK, self.n, 0) @ TT_LIQ.h(self.T_sl))
        H_gas = float(gas @ TT_GAS.h(self.T_hm))
        self.H_hm = H_before - H_sl_new - H_gas
        self.H_sl = H_sl_new
        # (3) heat exchange: deadman <-> pools, slag <-> metal
        U_dm = cal["hearth_dm_U_W_m2K"]
        U_sm = cal["slag_metal_U_W_m2K"]
        Q_dm_hm = U_dm * self.A * (T_dm - self.T_hm)
        Q_dm_sl = U_dm * self.A * (T_dm - self.T_sl) if m_sl > 1.0 else 0.0
        Q_sm = U_sm * self.A * (self.T_sl - self.T_hm) if m_sl > 1.0 else 0.0
        self.H_hm += (Q_dm_hm + Q_sm) * dt
        self.H_sl += (Q_dm_sl - Q_sm) * dt
        # (4) wall & bottom losses from the metal pool (refractory transient)
        Qw = Qs = dE = 0.0
        for nm in ("sump_wall", "hearth_bottom"):
            r = self.walls[nm].step(self.T_hm, dt)
            self.H_hm -= r.Q_in * dt
            Qw += r.Q_water
            Qs += r.Q_shell
            dE += r.dE
        self._update_T()
        # (5) tapping
        tapped = np.zeros(len(LIQ))
        H_tap_hm = H_tap_sl = 0.0
        ev = self._tapping(dt, p_gauge_Pa, t)
        if ev:
            events.extend(ev.get("events", []))
            f_hm, f_sl = ev["f_hm"], ev["f_sl"]
            tapped = np.where(HM_MASK, self.n * f_hm, self.n * f_sl)
            H_tap_hm = self.H_hm * f_hm
            H_tap_sl = self.H_sl * f_sl
            self.n -= tapped
            self.H_hm -= H_tap_hm
            self.H_sl -= H_tap_sl
        self._update_T()
        return HearthStepResult(gas / dt, H_gas / dt, tapped / dt, H_tap_hm / dt, H_tap_sl / dt, Qw, Qs, dE,
                                Q_dm_hm + Q_dm_sl, events)

    def _react(self, name, x, gas):
        if x <= 0:
            return
        cr = COMPILED[name]
        if cr.l_idx.size:
            self.n[cr.l_idx] += cr.l_nu * x
        if cr.g_idx.size:
            gas[cr.g_idx] += cr.g_nu * x

    def _update_T(self):
        n_hm = np.where(HM_MASK, self.n, 0.0)
        n_sl = np.where(SLAG_MASK, self.n, 0.0)
        if np.sum(n_hm) > 1e-9:
            self.T_hm = float(TT_LIQ.T_from_H(n_hm, np.array(self.H_hm), np.array(self.T_hm)))
        if np.sum(n_sl) > 1e-6:
            self.T_sl = float(TT_LIQ.T_from_H(n_sl, np.array(self.H_sl), np.array(self.T_sl)))
            self.T_sl = float(np.clip(self.T_sl, 1200.0, 2400.0))
            # re-sync enthalpy if clipped (only happens for vanishing slag inventories)
            self.H_sl = float(n_sl @ TT_LIQ.h(self.T_sl)) if abs(self.T_sl - 1200.0) < 1e-9 or abs(self.T_sl - 2400.0) < 1e-9 else self.H_sl

    def _tapping(self, dt, p_gauge, t) -> Optional[dict]:
        tp = self.cfg["tapping"]
        c = self.cast
        h_hm, h_top = self.levels()
        out = {"events": []}
        if not c.casting:
            gap_ok = (t - c.t_last_end) >= tp["min_gap_min"] * 60.0
            if gap_ok and (h_top - self.z_th) >= tp["open_level_above_taphole_m"]:
                c.casting, c.cast_no, c.t_start = True, c.cast_no + 1, t
                c.taphole = (c.taphole % self.n_tapholes) + 1
                c.d_th, c.hm_t, c.slag_t, c.sampled = self.d_th0, 0.0, 0.0, False
                out["events"].append({"type": "CAST_START", "cast_no": c.cast_no, "taphole": c.taphole, "t": t})
            else:
                return None
        # cast in progress
        el = t - c.t_start
        c.d_th = self.d_th0 + tp["taphole_erosion_mm_per_h"] / 1000.0 * el / 3600.0
        m_hm, m_sl = self.masses()
        rho_mix_head = 9.81 * (sm.RHO_HM * max(h_hm - self.z_th, 0.0) + sm.RHO_SLAG * max(h_top - max(h_hm, self.z_th), 0.0))
        dP = max(p_gauge, 0.0) + rho_mix_head
        # the taphole draws from a vertical window around its axis: metal fraction of the outflow = share of the
        # window below the slag/metal interface (drainage window ~1 m: the interface is drawn down locally)
        win = max(float(tp.get("drainage_window_m", 1.0)), 0.1)
        f_metal = float(np.clip((h_hm - self.z_th) / win + 0.5, 0.0, 1.0))
        rho = f_metal * sm.RHO_HM + (1 - f_metal) * sm.RHO_SLAG
        v = tp["discharge_coeff"] * math.sqrt(2.0 * dP / rho)
        Q = v * math.pi * c.d_th**2 / 4.0
        dm_hm = min(Q * f_metal * sm.RHO_HM * dt, max(m_hm - self.A * self.eps * self.z_th * sm.RHO_HM * 0.98, 0.0))
        dm_sl = min(Q * (1 - f_metal) * sm.RHO_SLAG * dt, m_sl * 0.95)
        out["f_hm"] = dm_hm / max(m_hm, 1e-9)
        out["f_sl"] = dm_sl / max(m_sl, 1e-9)
        c.hm_t += dm_hm / 1000.0
        c.slag_t += dm_sl / 1000.0
        w_hm, w_sl, _, _ = self.composition()
        if not c.sampled and el >= 0.5 * tp["max_cast_min"] * 60.0 * 0.5:
            c.sampled = True
            out["events"].append({"type": "SAMPLE", "cast_no": c.cast_no, "t": t, "hm_wt_pct": w_hm, "slag_wt_pct": w_sl,
                                  "T_hm_C": self.T_hm - 273.15, "T_slag_C": self.T_sl - 273.15})
        if (h_top <= self.z_th + 0.05 and el > 20 * 60) or el >= tp["max_cast_min"] * 60.0:
            c.casting = False
            c.t_last_end = t
            rec = {"type": "CAST_END", "cast_no": c.cast_no, "taphole": c.taphole, "t_start": c.t_start, "t_end": t,
                   "duration_min": el / 60.0, "hm_t": c.hm_t, "slag_t": c.slag_t, "T_hm_C": self.T_hm - 273.15,
                   "hm_wt_pct": w_hm, "slag_wt_pct": w_sl, "dry": bool(h_top <= self.z_th + 0.05)}
            out["events"].append(rec)
            self.casts.append(rec)
            self.casts = self.casts[-200:]
        return out
