"""Derived outputs: KPIs, axial/radial profiles, 3-D field payloads, stream compositions, Rist
diagram and balance reports. Everything here is computed from model state - nothing is
hard-coded for display. Each KPI carries a unit and a provenance tag:
  MODEL (first-principles state), DERIVED (computed from model streams), PROXY (indicator).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional

import numpy as np

from .species import (GAS, SOLID, LIQ, IDX_GAS, IDX_SOLID, IDX_LIQ, TT_GAS, TT_SOLID, TT_LIQ, HM_MASK, SLAG_MASK,
                      MM_LIQ, MM_SOLID, MM_GAS, MOL_PER_NM3, TRUE_GAS_MASK, ELEMENTS, E_GAS, E_SOLID, E_LIQ, SPECIES)
from .material_balance import balance_table, gas_composition, AM
from . import slag_metal as sm
from . import thermodynamics as th
from . import gas_flow as gf
from .zones import classify_zones
from .energy_balance import conventional_heat_balance

LHV_MJ_NM3 = {"CO": 12.63, "H2": 10.78, "CH4": 35.80}
P_ATM = 101325.0


def q(value, unit, prov="MODEL", **kw):
    v = None if value is None or (isinstance(value, float) and not math.isfinite(value)) else float(value)
    return {"value": v, "unit": unit, "provenance": prov, **kw}


class History:
    """Rolling per-step stream totals for windowed KPIs (production, specific consumptions)."""

    def __init__(self, maxlen=2000):
        self.buf = deque(maxlen=maxlen)

    def add(self, rec: dict):
        self.buf.append(rec)

    def window(self, hours: float, now: float) -> Dict[str, float]:
        out = {}
        t0 = now - hours * 3600.0
        for r in reversed(self.buf):
            if r["t"] < t0:
                break
            for k, v in r.items():
                if k != "t":
                    out[k] = out.get(k, 0.0) + v
        return out


def step_stream_record(m) -> dict:
    dt = m.last_record.dt
    ext = m.last_ext
    hm_kg = float(np.sum(m.last_drip.liq_out[HM_MASK] * MM_LIQ[HM_MASK])) * dt
    sl_kg = float(np.sum(m.last_drip.liq_out[SLAG_MASK] * MM_LIQ[SLAG_MASK])) * dt
    ch = m.last_charge if getattr(m, "last_charge", None) else {}
    masses = ch.get("masses", {}) if ch.get("charged") else {}
    coke_wet = masses.get("coke", 0.0)
    coke_dry = coke_wet * (1 - m.cfg["coke"]["moisture_pct"] / 100.0)
    ore_kg = sum(v for k, v in masses.items() if k != "coke")
    top = m.F_faces[:, -1].sum(axis=0)
    ext_ = m.last_record.extents
    sol_C = sum(ext_.get(k, 0.0) for k in ("R_boud", "R_wg", "R_char_boud", "R_char_wg", "D_feo", "R_dr_hem", "R_dr_mag"))
    carb = ext_.get("R_calc", 0.0) + ext_.get("R_mgcalc", 0.0) + ext_.get("M_caco3", 0.0) + ext_.get("M_mgco3", 0.0)
    return {"t": m.t, "dt": dt, "hm_kg": hm_kg, "slag_kg": sl_kg, "coke_dry_kg": coke_dry, "ore_kg": ore_kg,
            "pci_kg": ext.pci_kg_s * dt, "blast_Nm3": ext.blast_dry_Nm3_s * dt,
            "o2_Nm3": ext.blast_dry_Nm3_s * max(ext.o2_frac - 0.2095, 0) / max(m.op.get("o2_purity_pct", 99.5) / 100 - 0.2095, 1e-6) * dt,
            "ng_Nm3": m.op.get("ng_Nm3_h", 0.0) / 3600 * dt, "h2_Nm3": m.op.get("h2_Nm3_h", 0.0) / 3600 * dt,
            "topgas_Nm3": float(np.sum(top[TRUE_GAS_MASK])) / MOL_PER_NM3 * dt,
            "solution_loss_C_mol": sol_C, "carbonate_mol": carb, "Fe_mol": float(m.last_drip.liq_out[IDX_LIQ["Fe_l"]]) * dt,
            "charges": 1.0 if ch.get("charged") else 0.0, "heat_loss_J": (m.last_record.terms.get("shaft_wall_loss_W", 0)
                                                                           + m.last_record.terms.get("hearth_wall_loss_W", 0)
                                                                           + m.last_record.terms.get("tuyere_loss_W", 0)) * dt}


def charge_rates(m, hours=4.0) -> Optional[Dict[str, float]]:
    """Charged-material rates over WHOLE charge cycles inside the window.

    Counting discrete charges in a fixed time window quantises the coke rate by one charge (about 4 %
    on a 4-h window); dividing the mass of K charges by the K complete charge intervals they own removes
    that artefact. Returns None until two charges are in the window."""
    t0 = m.t - hours * 3600.0
    ev = [r for r in m.hist.buf if r.get("charges") and r["t"] >= t0]
    if len(ev) < 2:
        return None
    span = ev[-1]["t"] - ev[0]["t"]
    if span <= 0:
        return None
    own = ev[:-1]
    t_a, t_b = ev[0]["t"], ev[-1]["t"]
    hm_kg = sum(r["hm_kg"] for r in m.hist.buf if t_a < r["t"] <= t_b)     # same interval as the charges
    return {"coke_dry_kg_s": sum(r["coke_dry_kg"] for r in own) / span, "ore_kg_s": sum(r["ore_kg"] for r in own) / span,
            "hm_kg_s": hm_kg / span, "charges_per_h": len(own) / span * 3600.0, "span_h": span / 3600.0}


def specific(m, hours=4.0) -> Dict[str, float]:
    w = m.hist.window(hours, m.t)
    t = max(w.get("dt", 0.0), 1e-9)
    hm_t = max(w.get("hm_kg", 0.0) / 1000.0, 1e-9)
    cr = charge_rates(m, hours)
    if cr and cr["hm_kg_s"] > 0:
        hm_t_s = cr["hm_kg_s"] / 1000.0
        coke_kg_tHM, ore_kg_tHM = cr["coke_dry_kg_s"] / hm_t_s, cr["ore_kg_s"] / hm_t_s
    else:
        cr = None
        coke_kg_tHM, ore_kg_tHM = w.get("coke_dry_kg", 0.0) / hm_t, w.get("ore_kg", 0.0) / hm_t
    out = {"window_h": t / 3600.0, "production_t_d": w.get("hm_kg", 0.0) / 1000.0 / t * 86400.0,
           "coke_kg_tHM": coke_kg_tHM, "pci_kg_tHM": w.get("pci_kg", 0.0) / hm_t,
           "slag_kg_tHM": w.get("slag_kg", 0.0) / hm_t, "blast_Nm3_tHM": w.get("blast_Nm3", 0.0) / hm_t,
           "o2_Nm3_tHM": w.get("o2_Nm3", 0.0) / hm_t, "topgas_Nm3_tHM": w.get("topgas_Nm3", 0.0) / hm_t,
           "ng_Nm3_tHM": w.get("ng_Nm3", 0.0) / hm_t, "ore_kg_tHM": ore_kg_tHM,
           "charges_per_h": cr["charges_per_h"] if cr else w.get("charges", 0.0) / (t / 3600.0),
           "charge_basis": "whole charge cycles" if cr else "time window",
           "solution_loss_kgC_tHM": max(w.get("solution_loss_C_mol", 0.0) - w.get("carbonate_mol", 0.0), 0.0) * 0.012011 / hm_t,
           "heat_loss_GJ_tHM": w.get("heat_loss_J", 0.0) / 1e9 / hm_t,
           "rd": max(w.get("solution_loss_C_mol", 0.0) - w.get("carbonate_mol", 0.0), 0.0) / max(w.get("Fe_mol", 0.0), 1e-9)}
    out["fuel_rate_kg_tHM"] = out["coke_kg_tHM"] + out["pci_kg_tHM"]
    # reducing agent rate: coke + PCI + NG (0.72 kg/Nm3) on mass basis (declared convention)
    out["rar_kg_tHM"] = out["fuel_rate_kg_tHM"] + out["ng_Nm3_tHM"] * 0.717
    return out


def top_gas(m) -> dict:
    F = m.F_faces[:, -1].sum(axis=0)
    Tsec = m.Tg_faces[:, -1]
    Hs = np.sum(m.F_faces[:, -1] * TT_GAS.h(Tsec), axis=1)
    T = float(TT_GAS.T_from_H(F, np.array(float(np.sum(Hs))), np.array(float(np.mean(Tsec)))))
    dry = gas_composition(F, dry=True)
    wet = gas_composition(F, dry=False)
    eta_co = dry["CO2"] / max(dry["CO"] + dry["CO2"], 1e-9)
    eta_h2 = wet.get("H2O", 0.0) / max(wet.get("H2", 0.0) + wet.get("H2O", 0.0), 1e-9)
    n_dry = float(np.sum([F[GAS.index(k)] for k in GAS if k not in ("H2O", "Char")]))
    lhv = sum(LHV_MJ_NM3.get(k, 0.0) * v / 100.0 for k, v in dry.items())
    sec = []
    for r in range(m.n_r):
        d = gas_composition(m.F_faces[r, -1], dry=True)
        sec.append({"sector": r, "T_C": float(m.Tg_faces[r, -1] - 273.15),
                    "eta_CO": d["CO2"] / max(d["CO"] + d["CO2"], 1e-9), "share_of_flow": float(m.w_gas[r])})
    return {"T_C": T - 273.15, "dry_vol_pct": dry, "wet_vol_pct": wet, "eta_CO": eta_co, "eta_H2": eta_h2,
            "dry_flow_Nm3_h": n_dry / MOL_PER_NM3 * 3600.0, "wet_flow_Nm3_h": float(np.sum(F[TRUE_GAS_MASK])) / MOL_PER_NM3 * 3600,
            "LHV_MJ_Nm3_dry": lhv, "dust_char_kg_h": float(F[IDX_GAS["Char"]]) * 0.012011 * 3600, "radial": sec}


def reduction_degree(n: np.ndarray) -> np.ndarray:
    fe = 2 * n[..., IDX_SOLID["Fe2O3"]] + 3 * n[..., IDX_SOLID["Fe3O4"]] + n[..., IDX_SOLID["FeO"]] + n[..., IDX_SOLID["Fe"]]
    O = 3 * n[..., IDX_SOLID["Fe2O3"]] + 4 * n[..., IDX_SOLID["Fe3O4"]] + n[..., IDX_SOLID["FeO"]]
    with np.errstate(invalid="ignore", divide="ignore"):
        rd = np.where(fe > 1e-9, 1.0 - O / np.maximum(fe, 1e-12) / 1.5, np.nan)
    return rd


def profiles(m) -> dict:
    nr, nz = m.n_r, m.n_z
    w = m.grid.sector_area_frac
    zc = m.grid.z_c
    Tg_c = 0.5 * (m.Tg_faces[:, 1:] + m.Tg_faces[:, :-1])
    yF = 0.5 * (m.F_faces[:, 1:] + m.F_faces[:, :-1])
    y = gf.mole_fractions(yF)
    rd = reduction_degree(m.s_n)
    x_ore = m.s_V[..., m.ore_mat_mask].sum(axis=2) / np.maximum(m.s_V.sum(axis=2), 1e-12)
    zones = classify_zones(np.average(m.s_T, axis=0, weights=w), np.average(m.soft, axis=0, weights=w),
                           float(np.mean(m._cell_soft_melt()[0])), float(np.mean(m._cell_soft_melt()[1])))
    rates = {k: (np.sum(v, axis=0) / np.maximum(m.grid.vol, 1e-9)).round(6).tolist() for k, v in m.rates.items()}
    P = m.P_faces
    mean = lambda a: np.average(a, axis=0, weights=w)
    rd_mean = np.array([np.nan if np.all(np.isnan(rd[:, j])) else float(np.nansum(rd[:, j] * w) / np.sum(w[~np.isnan(rd[:, j])]))
                        for j in range(nz)])
    return {
        "z_m": zc.round(3).tolist(), "z_faces_m": m.grid.z_faces.round(3).tolist(), "radius_m": m.grid.r_c.round(3).tolist(),
        "zone": zones,
        "T_solid_C": (mean(m.s_T) - 273.15).round(1).tolist(), "T_gas_C": (mean(Tg_c) - 273.15).round(1).tolist(),
        "T_gas_faces_C": (mean(m.Tg_faces) - 273.15).round(1).tolist(),
        "reduction_degree_pct": (100 * rd_mean).round(2).tolist(),
        "y_CO_pct": (100 * mean(y[..., IDX_GAS["CO"]])).round(2).tolist(),
        "y_CO2_pct": (100 * mean(y[..., IDX_GAS["CO2"]])).round(2).tolist(),
        "y_H2_pct": (100 * mean(y[..., IDX_GAS["H2"]])).round(2).tolist(),
        "y_H2O_pct": (100 * mean(y[..., IDX_GAS["H2O"]])).round(2).tolist(),
        "y_N2_pct": (100 * mean(y[..., IDX_GAS["N2"]])).round(2).tolist(),
        "eta_CO_pct": (100 * mean(y[..., IDX_GAS["CO2"]]) / np.maximum(mean(y[..., IDX_GAS["CO"]] + y[..., IDX_GAS["CO2"]]), 1e-9)).round(2).tolist(),
        "pressure_kPa_g": ((P - P_ATM) / 1000).round(2).tolist(),
        "dP_cell_kPa": (np.average(m.dP_cells, axis=0, weights=m.w_gas) / 1000).round(3).tolist(),
        "gas_velocity_m_s": mean(m.u_g).round(3).tolist(),
        "solid_descent_mm_s": (1000 * np.mean(m.u_s_faces, axis=0)).round(4).tolist(),
        "ore_volume_fraction": mean(x_ore).round(4).tolist(), "softening_index": mean(m.soft).round(3).tolist(),
        "melt_rate_kg_s": np.sum(m.melt_rate, axis=0).round(3).tolist(),
        "gas_solid_heat_MW": (np.sum(m.Qgs_cell, axis=0) / 1e6).round(3).tolist(),
        "wall_heat_MW": (m.Qw_cell / 1e6).round(4).tolist(),
        "coke_t": (np.sum(m.s_n[..., IDX_SOLID["C_k"]], axis=0) * 0.012011 / 1000).round(2).tolist(),
        "rates_mol_m3_s": rates,
        "units": {"T": "degC", "y": "vol% (wet)", "P": "kPa gauge", "rates": "mol/(m3 s)", "z": "m above tuyere centre-line"},
    }


def fields3d(m) -> dict:
    """Compact (r,z) fields for the Three.js scene: model-computed values on the model grid."""
    Tg_c = 0.5 * (m.Tg_faces[:, 1:] + m.Tg_faces[:, :-1])
    yF = 0.5 * (m.F_faces[:, 1:] + m.F_faces[:, :-1])
    y = gf.mole_fractions(yF)
    rd = np.nan_to_num(reduction_degree(m.s_n), nan=1.0)
    x_ore = m.s_V[..., m.ore_mat_mask].sum(axis=2) / np.maximum(m.s_V.sum(axis=2), 1e-12)
    cz = cohesive_zone(m)
    return {
        "schema": "bf-fields-v1", "t_s": m.t, "z_faces_m": m.grid.z_faces.round(3).tolist(),
        "r_edges_norm": m.grid.sector_r_edges.round(4).tolist(), "radius_m": m.grid.r_c.round(3).tolist(),
        "fields": {
            "T_gas_C": (Tg_c - 273.15).round(1).tolist(), "T_solid_C": (m.s_T - 273.15).round(1).tolist(),
            "reduction_degree_pct": (100 * rd).round(1).tolist(), "y_CO_pct": (100 * y[..., IDX_GAS["CO"]]).round(2).tolist(),
            "y_CO2_pct": (100 * y[..., IDX_GAS["CO2"]]).round(2).tolist(), "y_H2_pct": (100 * y[..., IDX_GAS["H2"]]).round(2).tolist(),
            "gas_velocity_m_s": m.u_g.round(3).tolist(), "ore_volume_fraction": x_ore.round(3).tolist(),
            "softening_index": m.soft.round(3).tolist(), "pressure_kPa_g": ((m.P_faces_sec - P_ATM) / 1000).round(2).tolist(),
        },
        "units": {"T_gas_C": "degC", "T_solid_C": "degC", "reduction_degree_pct": "%", "y_CO_pct": "vol%",
                  "y_CO2_pct": "vol%", "y_H2_pct": "vol%", "gas_velocity_m_s": "m/s (superficial)",
                  "ore_volume_fraction": "-", "softening_index": "0-1", "pressure_kPa_g": "kPa(g)"},
        "cohesive_zone": cz, "layers": m.layers.payload()[:120],
        "descent_mm_s": (1000 * np.mean(m.u_s_faces, axis=0)).round(4).tolist(),
        "hearth": hearth_state(m), "raceway": {"depth_m": m.last_rw.diag["raceway_depth_m"] if m.last_rw else 0.0,
                                              "T_C": (m.last_rw.T_gas - 273.15) if m.last_rw else None},
        "label": "Model-computed fields on the model grid (multi-zone/axial FV). Spatial detail between cells is "
                 "interpolated for display; these are NOT measurements or CFD.",
    }


def cohesive_zone(m) -> dict:
    """Cohesive zone (softening index between 0.02 and 0.98) boundaries per radial sector."""
    out = []
    zf = m.grid.z_faces
    for r in range(m.n_r):
        s = m.soft[r]
        x = m.s_V[r][:, m.ore_mat_mask].sum(axis=1) / np.maximum(m.s_V[r].sum(axis=1), 1e-12)
        idx = np.where((s > 0.02) & (x > 0.01))[0]
        if idx.size:
            out.append({"sector": r, "z_bottom_m": float(zf[idx.min()]), "z_top_m": float(zf[idx.max() + 1])})
        else:
            out.append({"sector": r, "z_bottom_m": None, "z_top_m": None})
    return {"sectors": out, "dripping_zone_height_m": float(getattr(m, "h_drip", 0.0)),
            "confidence": "LOW - temperature-window proxy (softening/melting temperatures of burden)"}


def hearth_state(m) -> dict:
    h = m.hearth
    w_hm, w_sl, n_hm, n_sl = h.composition()
    lv = h.levels()
    m_hm, m_sl = h.masses()
    c = h.cast
    return {"T_hm_C": h.T_hm - 273.15, "T_slag_C": h.T_sl - 273.15, "metal_level_m": lv[0], "slag_top_m": lv[1],
            "taphole_level_m": h.z_th, "metal_t": m_hm / 1000.0, "slag_t": m_sl / 1000.0, "hm_wt_pct": w_hm,
            "slag_wt_pct": w_sl, "casting": c.casting, "cast_no": c.cast_no, "taphole": c.taphole,
            "cast_elapsed_min": (m.t - c.t_start) / 60.0 if c.casting else None, "taphole_diameter_mm": c.d_th * 1000,
            "tuyere_level_m": h.z_th + m.cfg["geometry"]["taphole_to_tuyere_m"],
            "liquid_margin_to_tuyeres_m": h.z_th + m.cfg["geometry"]["taphole_to_tuyere_m"] - lv[1],
            "levels_basis": "liquid in deadman pore space (porosity) above the hearth pad",
            "last_casts": h.casts[-12:]}


def rist(m, sp: dict) -> dict:
    """Rist operating line from model streams (CO/CO2 basis, H2 treated as equivalent reductant)."""
    w = m.hist.window(24.0, m.t)
    ext = m.last_ext
    Fe = max(w.get("Fe_mol", 0.0), 1e-9)
    tg = top_gas(m)
    F = m.F_faces[:, -1].sum(axis=0)
    eta = tg["eta_CO"]
    t = max(w.get("dt", 0.0), 1e-9)
    nFe_s = Fe / t
    nC = (F[IDX_GAS["CO"]] + F[IDX_GAS["CO2"]]) / max(nFe_s, 1e-9)
    yb = (2 * ext.gas[IDX_GAS["O2"]] + ext.gas[IDX_GAS["H2O"]] + ext.pci_kg_s * (ext.pci["O"] + ext.pci["H2O"])) / max(nFe_s, 1e-9)
    xA = 1 + eta
    yA = nC * xA - yb
    T_w = 1173.0
    eta_w = 1.0 - th.y_red_eq("wus_CO", T_w)
    return {"mu_C_per_Fe": nC, "y_blast_O_per_Fe": yb, "point_A": [xA, yA], "point_E": [0.0, -yb],
            "W_point": [1 + eta_w, 1.05], "line_x": [0.0, xA], "line_y": [-yb, yA],
            "eta_CO_top": eta, "eta_CO_W_equilibrium": eta_w, "T_W_C": T_w - 273.15,
            "note": "Rist (1964) operating line; mu = slope = C gasified per Fe (mol/mol)."}


def kpis(m) -> dict:
    sp4 = specific(m, 4.0)
    sp24 = specific(m, 24.0)
    rw = m.last_rw
    tg = top_gas(m)
    hs = hearth_state(m)
    w_hm, w_sl, n_hm, n_sl = m.hearth.composition()
    slag = sm.slag_summary(n_sl, n_hm, m.hearth.T_hm) if sum(n_sl.values()) > 0 else {}
    vol = m.vol["working_volume_m3"]
    P_top = m.op["top_pressure_kPa_g"]
    dP = (m.P_faces[0] - m.P_faces[-1]) / 1000.0
    P_blast_g = (m.P_blast - P_ATM) / 1000.0
    V_bosh = rw.diag["bosh_gas_Nm3_min"] if rw else 0.0
    Pb, Pt = m.P_blast / 1e5, (P_top * 1000 + P_ATM) / 1e5
    K_res = (Pb**2 - Pt**2) / max(V_bosh, 1e-9) ** 1.7 * 1e4 if V_bosh > 0 else None
    walls = {w.zone: 0.0 for w in m.walls}
    for wseg in m.walls:
        walls[wseg.zone] += wseg.Q_water_last + wseg.Q_shell_last
    walls["sump_wall"] = m.w_sump.Q_water_last + m.w_sump.Q_shell_last
    walls["upper_hearth_wall"] = m.w_upper.Q_water_last + m.w_upper.Q_shell_last
    walls["hearth_bottom"] = m.w_bottom.Q_water_last + m.w_bottom.Q_shell_last
    tuy = rw.Q_tuyere if rw else 0.0
    Ts_mean = np.average(m.s_T, axis=0, weights=m.grid.sector_area_frac)
    trz = _trz(m.grid.z_c, Ts_mean)
    cz = cohesive_zone(m)
    hm_prod = sp4["production_t_d"]
    out = {
        "production_t_d": q(hm_prod, "tHM/d", "DERIVED", window_h=sp4["window_h"]),
        "production_24h_t_d": q(sp24["production_t_d"], "tHM/d", "DERIVED", window_h=sp24["window_h"]),
        "productivity_t_m3_d": q(hm_prod / vol, "tHM/(m3 d)", "DERIVED", basis="working volume"),
        "coke_rate_kg_tHM": q(sp4["coke_kg_tHM"], "kg/tHM (dry)", "DERIVED"),
        "pci_rate_kg_tHM": q(sp4["pci_kg_tHM"], "kg/tHM", "DERIVED"),
        "fuel_rate_kg_tHM": q(sp4["fuel_rate_kg_tHM"], "kg/tHM", "DERIVED"),
        "rar_kg_tHM": q(sp4["rar_kg_tHM"], "kg/tHM", "DERIVED", definition="coke(dry)+PCI+NG(0.717 kg/Nm3)"),
        "slag_rate_kg_tHM": q(sp4["slag_kg_tHM"], "kg/tHM", "DERIVED"),
        "blast_volume_Nm3_min": q(m.op["blast_volume_Nm3_min"], "Nm3/min (dry, incl. O2)", "MODEL", role="MV"),
        "blast_temp_C": q(m.op["blast_temp_C"], "degC", "MODEL", role="MV"),
        "blast_moisture_g_Nm3": q(m.op["blast_moisture_g_Nm3"], "g/Nm3 dry blast", "MODEL", role="MV"),
        "o2_enrichment_pct": q(m.op["o2_enrichment_pct"], "% O2 above air (dry blast)", "MODEL", role="MV"),
        "pci_t_h": q(m.op["pci_t_h"], "t/h", "MODEL", role="MV"),
        "blast_Nm3_tHM": q(sp4["blast_Nm3_tHM"], "Nm3/tHM", "DERIVED"),
        "oxygen_Nm3_tHM": q(sp4["o2_Nm3_tHM"], "Nm3/tHM", "DERIVED"),
        "blast_pressure_kPa_g": q(P_blast_g, "kPa(g)", "MODEL"),
        "top_pressure_kPa_g": q(P_top, "kPa(g)", "MODEL", role="MV"),
        "pressure_drop_kPa": q(dP, "kPa", "MODEL", basis="tuyere level to stockline (bed only)"),
        "resistance_index_K": q(K_res, "bar2/(Nm3/min)^1.7 x1e4", "PROXY"),
        "permeability_index": q(V_bosh / max(P_blast_g - P_top, 1e-6), "Nm3/min per kPa", "PROXY"),
        "flooding_ratio": q((getattr(m, "hyd_flags", None) or {}).get("flooding_ratio"), "-", "PROXY",
                            note="dripping-zone flooding ordinate / empirical limit; > 1 indicates flooding risk"),
        "fluidisation_ratio": q((getattr(m, "hyd_flags", None) or {}).get("fluidisation_ratio"), "-", "PROXY",
                                note="stockline gas velocity / minimum fluidisation velocity of 3 mm fines (Wen & Yu)"),
        "raft_C": q(rw.raft_std_C if rw else None, "degC", "MODEL", note="ESTIMATE (coke 1500 degC convention); not measurable"),
        "raceway_gas_T_C": q((rw.T_gas - 273.15) if rw else None, "degC", "MODEL"),
        "bosh_gas_Nm3_min": q(V_bosh, "Nm3/min", "MODEL"),
        "tuyere_velocity_m_s": q(rw.diag["tuyere_velocity_m_s"] if rw else None, "m/s", "MODEL"),
        "blast_kinetic_energy_kW": q(rw.diag["blast_kinetic_energy_kW"] if rw else None, "kW per tuyere", "MODEL"),
        "raceway_depth_m": q(rw.diag["raceway_depth_m"] if rw else None, "m", "PROXY"),
        "pci_burnout_pct": q(100 * rw.pci_burnout if rw else None, "%", "MODEL"),
        "top_gas_T_C": q(tg["T_C"], "degC", "MODEL"),
        "top_CO_pct": q(tg["dry_vol_pct"]["CO"], "vol% dry", "MODEL"),
        "top_CO2_pct": q(tg["dry_vol_pct"]["CO2"], "vol% dry", "MODEL"),
        "top_H2_pct": q(tg["dry_vol_pct"]["H2"], "vol% dry", "MODEL"),
        "top_N2_pct": q(tg["dry_vol_pct"]["N2"], "vol% dry", "MODEL"),
        "eta_CO_pct": q(100 * tg["eta_CO"], "%", "MODEL", definition="CO2/(CO+CO2), dry top gas"),
        "eta_H2_pct": q(100 * tg["eta_H2"], "%", "MODEL", definition="H2O/(H2+H2O), wet top gas"),
        "top_gas_Nm3_tHM": q(sp4["topgas_Nm3_tHM"], "Nm3/tHM (wet)", "DERIVED"),
        "top_gas_LHV_MJ_Nm3": q(tg["LHV_MJ_Nm3_dry"], "MJ/Nm3 dry", "DERIVED"),
        "hmt_C": q(hs["T_hm_C"], "degC", "MODEL"), "slag_T_C": q(hs["T_slag_C"], "degC", "MODEL"),
        "hm_si_pct": q(w_hm.get("Si"), "wt%", "MODEL"), "hm_s_pct": q(w_hm.get("S"), "wt%", "MODEL"),
        "hm_c_pct": q(w_hm.get("C"), "wt%", "MODEL"), "hm_mn_pct": q(w_hm.get("Mn"), "wt%", "MODEL"),
        "hm_p_pct": q(w_hm.get("P"), "wt%", "MODEL"),
        "slag_B2": q(slag.get("B2"), "-", "DERIVED", definition="CaO/SiO2 (wt)"),
        "slag_B4": q(slag.get("B4"), "-", "DERIVED", definition="(CaO+MgO)/(SiO2+Al2O3) (wt)"),
        "slag_MgO_Al2O3": q(slag.get("MgO_Al2O3"), "-", "DERIVED"),
        "slag_Al2O3_pct": q(slag.get("wt_pct", {}).get("Al2O3"), "wt%", "MODEL"),
        "slag_FeO_pct": q(slag.get("wt_pct", {}).get("FeO"), "wt%", "MODEL"),
        "slag_viscosity_Pa_s": q(slag.get("viscosity_Pa_s"), "Pa s", "DERIVED", model="modified Urbain"),
        "sulfur_partition_LS": q(slag.get("L_S"), "-", "DERIVED"),
        "optical_basicity": q(slag.get("Lambda"), "-", "DERIVED"),
        "slag_liquidus_C": q(slag.get("liquidus", {}).get("value_C"), "degC", "PROXY",
                             status=slag.get("liquidus", {}).get("status")),
        "active_coke_T_C": q(m.dm_T - 273.15, "degC", "MODEL"),
        "dripping_zone_height_m": q(cz["dripping_zone_height_m"], "m", "PROXY"),
        "thermal_reserve_T_C": q(trz, "degC", "DERIVED"),
        "solution_loss_kgC_tHM": q(sp4["solution_loss_kgC_tHM"], "kg C/tHM", "DERIVED"),
        "direct_reduction_degree": q(min(sp4["rd"], 1.0), "-", "DERIVED", definition="solution-loss C per Fe (Rist basis)"),
        "heat_loss_total_MW": q((sum(walls.values()) + tuy) / 1e6, "MW", "MODEL"),
        "heat_loss_GJ_tHM": q(sp4["heat_loss_GJ_tHM"], "GJ/tHM", "DERIVED"),
        "tuyere_cooling_MW": q(tuy / 1e6, "MW", "MODEL"),
        "charges_per_h": q(sp4["charges_per_h"], "1/h", "DERIVED"),
        "stockline_m": q(m.grid.H_work - m.stockline_m if hasattr(m, "stockline_m") else None, "m below set level", "MODEL"),
        "burden_descent_time_h": q(_descent_time(m), "h", "DERIVED", basis="stockline to tuyere level"),
        "hearth_metal_level_m": q(hs["metal_level_m"], "m above hearth pad", "MODEL"),
        "hearth_slag_level_m": q(hs["slag_top_m"], "m above hearth pad", "MODEL"),
        "hearth_liquid_margin_m": q(hs["liquid_margin_to_tuyeres_m"], "m below tuyere level", "MODEL"),
    }
    out["_walls_MW"] = {k: v / 1e6 for k, v in walls.items()}
    return out


def _trz(z, T):
    """Thermal-reserve-zone temperature: solids temperature where the axial gradient is flattest inside the
    820-1100 degC window. A weighted mean (weights 1/(gradient + 5 K/m)^2, tapered over 20 K at the window
    edges) instead of an arg-min, so the value moves continuously when the flattest cell changes."""
    Tc = T - 273.15
    num = den = 0.0
    for i in range(1, len(z) - 1):
        taper = min(max((Tc[i] - 820.0) / 20.0, 0.0), 1.0) * min(max((1100.0 - Tc[i]) / 20.0, 0.0), 1.0)
        if taper <= 0.0:
            continue
        g = abs(Tc[i + 1] - Tc[i - 1]) / max(z[i + 1] - z[i - 1], 1e-9)
        w = taper / (g + 5.0) ** 2
        num += w * Tc[i]
        den += w
    return num / den if den > 0 else None


def _descent_time(m):
    u = np.mean(m.u_s_faces, axis=0)
    zf = m.grid.z_faces
    t = 0.0
    for j in range(len(zf) - 1):
        uj = max(0.5 * (u[j] + u[j + 1]), 1e-7)
        t += (zf[j + 1] - zf[j]) / uj
    return t / 3600.0


def balances(m) -> dict:
    rec = m.last_record
    tbl = balance_table(rec.el_in, rec.el_out, rec.el_acc, tol_pct=0.5)
    E_res = rec.E_in - rec.E_out - rec.E_acc
    ext = m.last_ext
    # energy basis: heat of combustion of tuyere coke carbon to CO2 plus PCI HHV during the step
    fuel_W = (m.last_rw.diag["coke_C_mol_s"] * 393.5e3 if m.last_rw else 0.0) + ext.pci_kg_s * m.cfg["pci"]["HHV_MJ_kg"] * 1e6
    basis_J = max(fuel_W * rec.dt, 1.0)
    cum_in = m.cum["el_in"]
    cum_out = m.cum["el_out"]
    return {"step": {"t_s": rec.t, "dt_s": rec.dt, "elements": tbl,
                     "energy": {"in_J": rec.E_in, "out_J": rec.E_out, "accumulation_J": rec.E_acc, "residual_J": E_res,
                                "basis_J": basis_J, "residual_pct_of_basis": 100 * E_res / basis_J,
                                "status": "OK" if abs(100 * E_res / basis_J) <= 1.0 else "FAIL",
                                "terms_W": rec.terms},
                     "status": "OK" if all(r["status"] == "OK" for r in tbl) and abs(100 * E_res / basis_J) <= 1.0 else "FAIL"},
            "tolerances": {"element_residual_pct": 0.5, "energy_residual_pct_of_fuel_basis": 1.0,
                           "note": "Demo numerical acceptance only - not validated industrial limits."},
            "cumulative_elements_kg": {e: {"in": float(cum_in[i] * AM[i]), "out": float(cum_out[i] * AM[i])} for i, e in enumerate(ELEMENTS)}}


def streams(m) -> dict:
    """Inputs and outputs: rates and compositions (current) + cumulative totals."""
    sp = specific(m, 4.0)
    n_ch, V_ch, masses = m.charge_composition()
    ch_per_h = sp["charges_per_h"]
    inputs = []
    for k, kg in masses.items():
        if kg <= 0:
            continue
        md = m.cfg["materials"].get(k) if k != "coke" else m.cfg["coke"]
        inputs.append({"stream": k, "kind": "burden" if k != "coke" else "fuel", "kg_per_charge": kg,
                       "t_h": kg * ch_per_h / 1000.0, "kg_tHM": kg * ch_per_h / max(sp["production_t_d"] / 24.0, 1e-9),
                       "chem_wt_pct": (md.get("chem") if k != "coke" else {"C": md["C_pct"], "ash": md["ash_pct"],
                                                                           "S": md["S_pct"], "H": md["H_pct"], "N": md["N_pct"]}),
                       "moisture_pct": md.get("moisture_pct")})
    ext = m.last_ext
    inputs.append({"stream": "pci", "kind": "injectant", "t_h": ext.pci_kg_s * 3.6, "kg_tHM": sp["pci_kg_tHM"],
                   "chem_wt_pct": {k: m.cfg["pci"][k + "_pct"] for k in ("C", "H", "O", "N", "S", "ash")}})
    blast_comp = {k: float(100 * ext.gas[IDX_GAS[k]] / max(np.sum(ext.gas), 1e-9)) for k in ("O2", "N2", "H2O", "CH4", "H2")}
    inputs.append({"stream": "hot_blast", "kind": "gas", "Nm3_h": ext.blast_dry_Nm3_s * 3600, "T_C": ext.T_blast - 273.15,
                   "vol_pct_wet": blast_comp, "Nm3_tHM": sp["blast_Nm3_tHM"]})
    w_hm, w_sl, _, _ = m.hearth.composition()
    tg = top_gas(m)
    outputs = [
        {"stream": "hot_metal", "t_h": sp["production_t_d"] / 24.0, "T_C": m.hearth.T_hm - 273.15, "wt_pct": w_hm},
        {"stream": "slag", "t_h": sp["production_t_d"] / 24.0 * sp["slag_kg_tHM"] / 1000, "kg_tHM": sp["slag_kg_tHM"],
         "T_C": m.hearth.T_sl - 273.15, "wt_pct": w_sl},
        {"stream": "top_gas", "Nm3_h": tg["wet_flow_Nm3_h"], "T_C": tg["T_C"], "vol_pct_dry": tg["dry_vol_pct"],
         "vol_pct_wet": tg["wet_vol_pct"], "Nm3_tHM": sp["topgas_Nm3_tHM"]},
        {"stream": "dust_sludge", "kg_tHM": None, "note": "charged fines fraction + unburnt char; composition = charge fines"},
    ]
    cum = {"inputs_t": {k: round(v, 3) for k, v in m.cum["in"].items()},
           "outputs_t": {k: round(v, 3) for k, v in m.cum["out"].items()}, "elapsed_h": m.cum["time_s"] / 3600}
    tapped = m.cum.get("tapped_n")
    if tapped is not None and np.sum(tapped) > 0:
        n_hm = {k: float(tapped[IDX_LIQ[k]]) for k in LIQ if SPECIES[k].phase == "hm"}
        n_sl = {k: float(tapped[IDX_LIQ[k]]) for k in LIQ if SPECIES[k].phase == "slag"}
        cum["tapped_hot_metal_wt_pct"] = sm.hm_wt_pct(n_hm)
        cum["tapped_slag_wt_pct"] = sm.slag_wt_pct(n_sl)
    return {"inputs": inputs, "outputs": outputs, "cumulative": cum}


def heat_balance(m) -> dict:
    return conventional_heat_balance(m.win)


def walls_payload(m) -> List[dict]:
    out = []
    for j, w in enumerate(m.walls):
        p = w.payload()
        p.update({"cell": j, "z_m": float(m.grid.z_c[j]), "zone": w.zone})
        out.append(p)
    for w in (m.w_upper, m.w_sump, m.w_bottom):
        p = w.payload()
        est = w.remaining_lining_estimate()
        p["isotherm_1150C_depth_from_cold_face_m"] = est
        p["remaining_lining_note"] = "Inverse 1-D proxy from heat flux and cold-face temperature; not a life prediction."
        out.append(p)
    return out
