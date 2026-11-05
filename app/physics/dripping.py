"""Cohesive-zone melting conversions and the active-coke / dripping / deadman node.

The node holds the deadman and active-coke inventory (coke-carrier species plus any ore that
arrived unmelted). Each step it receives raceway gas (rising) and primary melts (falling) and
performs, with exact stoichiometry:
  (FeO)+C -> Fe+CO (direct reduction of primary-slag FeO), carburisation towards Neumann
  saturation, SiO + [C] -> [Si] + CO, (MnO)+C -> [Mn]+CO, (P2O5)+5C -> 2[P]+5CO,
  COS absorption into slag (CaS) and metal ([S]) split by the L_S partition, and alkali
  volatilisation. Heat exchange uses effectiveness (NTU) relations; the node enthalpy closes the
  energy balance exactly (H_dm' = H_dm + H_in - H_out).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

import numpy as np

from .species import (GAS, LIQ, SOLID, IDX_GAS, IDX_LIQ, IDX_SOLID, TT_GAS, TT_LIQ, TT_SOLID, ORE_MASK, SPECIES, R_GAS)
from .reactions import COMPILED, REACTIONS
from . import slag_metal as sm

_ORE_KEYS = [k for k in SOLID if SPECIES[k].phase == "ore"]


def melt_conversion(n_melt: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    """Convert melted ore-carrier species (mol or mol/s, SOLID-indexed) into liquids, gas products
    and the coke carbon needed for residual higher-oxide / ZnO reduction on melting."""
    liq = np.zeros(len(LIQ))
    gas = np.zeros(len(GAS))
    c_need = 0.0
    g = lambda k: float(n_melt[IDX_SOLID[k]])
    for k_s, k_l in (("Fe", "Fe_l"), ("FeO", "FeO_sl"), ("CaO", "CaO_sl"), ("MgO", "MgO_sl"), ("SiO2", "SiO2_sl"),
                     ("Al2O3", "Al2O3_sl"), ("MnO", "MnO_sl"), ("P2O5", "P2O5_sl"), ("K2O", "K2O_sl"),
                     ("Na2O", "Na2O_sl"), ("C_ore", "C_hm")):
        liq[IDX_LIQ[k_l]] += g(k_s)
    liq[IDX_LIQ["Fe_l"]] += g("FeS")
    liq[IDX_LIQ["S_hm"]] += g("FeS")
    liq[IDX_LIQ["FeO_sl"]] += 2 * g("Fe2O3") + 3 * g("Fe3O4")
    gas[IDX_GAS["CO"]] += g("Fe2O3") + g("Fe3O4") + g("ZnO")
    c_need += g("Fe2O3") + g("Fe3O4") + g("ZnO")
    gas[IDX_GAS["Zn"]] += g("ZnO")
    liq[IDX_LIQ["CaO_sl"]] += g("CaCO3")
    liq[IDX_LIQ["MgO_sl"]] += g("MgCO3")
    gas[IDX_GAS["CO2"]] += g("CaCO3") + g("MgCO3")
    gas[IDX_GAS["H2O"]] += g("H2O_l") + g("H2O_b")
    return liq, gas, c_need


@dataclass
class DripResult:
    gas_out: np.ndarray
    T_gas_out: float
    liq_out: np.ndarray
    T_liq_out: float
    H_liq_out: float
    dm_consumed: np.ndarray           # SOLID-indexed mol/s removed from node (coke C + melted ore)
    Q_node: float                     # W net enthalpy gain of node from streams
    rates: Dict[str, float] = field(default_factory=dict)
    diag: Dict[str, float] = field(default_factory=dict)


def _apply(name, x, gas, liq, sol):
    cr = COMPILED[name]
    if cr.g_idx.size:
        gas[cr.g_idx] += cr.g_nu * x
    if cr.l_idx.size:
        liq[cr.l_idx] += cr.l_nu * x
    if cr.s_idx.size:
        sol[cr.s_idx] += cr.s_nu * x


def dripping_node(gas_in: np.ndarray, T_gas_in: float, liq_in: np.ndarray, H_liq_in: float, dm_n: np.ndarray,
                  T_dm: float, cfg: dict, dt: float, h_drip: float = 3.0) -> DripResult:
    """h_drip: height [m] of the coke-only dripping zone between tuyere level and cohesive-zone root;
    liquid superheating, SiO pick-up and FeO reduction intensities scale with it (a lower
    cohesive zone gives colder, lower-Si hot metal)."""
    cal = cfg["calibration"]
    f_h = float(np.clip(h_drip / cal.get("drip_height_ref_m", 3.0), 0.08, 2.0))
    gas = gas_in.copy()
    liq = liq_in.copy()
    sol = np.zeros(len(SOLID))            # change of node solids (mol/s)
    rates: Dict[str, float] = {}
    # (0) unmelted ore reaching the deadman melts here (cold-furnace pathway)
    ore_in_dm = np.where(ORE_MASK, dm_n, 0.0)
    dm_melt_liq = None
    if np.sum(ore_in_dm) > 1e-9:
        # temperature-gated (heat-limited) melting of raw burden that reached the tuyere level
        Ts_, Tm_ = cal.get("dm_T_soft_K", 1430.0), cal.get("dm_T_melt_K", 1700.0)
        x_ = float(np.clip((T_dm - Ts_) / (Tm_ - Ts_), 0.0, 1.0))
        n_m = ore_in_dm * (x_ * x_ * (3 - 2 * x_)) / 900.0
        l2, g2, cneed = melt_conversion(n_m)
        liq += l2
        gas += g2
        dm_melt_liq = l2
        sol -= n_m
        sol[IDX_SOLID["C_k"]] -= cneed
        rates["deadman_melting_mol_s"] = float(np.sum(n_m))
        for nm_, key_ in (("M_caco3", "CaCO3"), ("M_mgco3", "MgCO3"), ("M_h2o_l", "H2O_l"), ("M_h2o_b", "H2O_b")):
            rates[nm_] = rates.get(nm_, 0.0) + float(n_m[IDX_SOLID[key_]])
    T = T_dm
    T_l_in_est = T_dm
    if np.sum(liq_in) > 1e-12:
        T_l_in_est = float(TT_LIQ.T_from_H(liq_in, np.array(H_liq_in), np.array(T_dm)))
    # (1) direct reduction of primary-slag FeO by coke
    k_feo = REACTIONS["D_feo"].kinetics.k(T) * cal.get("kinetic_multipliers", {}).get("D_feo", 1.0)
    # reduction of primary-slag FeO by coke towards a kinetic (mass-transfer limited) residual level:
    # BF slags leave the furnace with ~0.3-0.8 wt% FeO, not with zero (calibration: slag_feo_residual_pct)
    m_slag = float(sum(liq[IDX_LIQ[k]] * SPECIES[k].molar_mass for k in LIQ if SPECIES[k].phase == "slag"))
    n_feo_res = cal.get("slag_feo_residual_pct", 0.45) / 100.0 * m_slag / SPECIES["FeO_sl"].molar_mass
    x = (1.0 - np.exp(-k_feo * 120.0 * max(f_h, 0.5))) * max(liq[IDX_LIQ["FeO_sl"]] - n_feo_res, 0.0)
    _apply("D_feo", x, gas, liq, sol)
    rates["D_feo"] = x
    # (2) P2O5 and MnO reduction
    x = cal["p_to_metal_frac"] * liq[IDX_LIQ["P2O5_sl"]]
    _apply("D_p2o5", x, gas, liq, sol)
    rates["D_p2o5"] = x
    phi_mn = float(np.clip(0.45 + 0.0025 * (T - 1673.0), 0.30, 0.85))
    x = phi_mn * liq[IDX_LIQ["MnO_sl"]]
    _apply("D_mno", x, gas, liq, sol)
    rates["D_mno"] = x
    # (3) SiO absorption by metal droplets
    phi_si = float(np.clip(cal["sio_abs_k"] * min(f_h, 1.5) * np.exp(-120e3 / R_GAS * (1.0 / T - 1.0 / 1773.0)), 0.0, 0.92))
    if liq[IDX_LIQ["Fe_l"]] <= 1e-9:
        phi_si = 0.0
    n_si = phi_si * gas[IDX_GAS["SiO"]]
    # (4) carburisation to target (accounting for C consumed by SiO absorption)
    m_other = float(liq[IDX_LIQ["Fe_l"]] * 0.055845 + (liq[IDX_LIQ["Si_hm"]] + n_si) * 0.028085
                    + liq[IDX_LIQ["Mn_hm"]] * 0.054938 + liq[IDX_LIQ["P_hm"]] * 0.030974 + liq[IDX_LIQ["S_hm"]] * 0.03206)
    hm_w = {"Si": 100 * (liq[IDX_LIQ["Si_hm"]] + n_si) * 0.028085 / max(m_other, 1e-12),
            "Mn": 100 * liq[IDX_LIQ["Mn_hm"]] * 0.054938 / max(m_other, 1e-12),
            "P": 100 * liq[IDX_LIQ["P_hm"]] * 0.030974 / max(m_other, 1e-12),
            "S": 100 * liq[IDX_LIQ["S_hm"]] * 0.03206 / max(m_other, 1e-12)}
    # carbon pick-up is limited by saturation at the temperature of the dripping metal
    T_l_est = T_dm + (T_l_in_est - T_dm) * np.exp(-cal["drip_ntu_liq"] * f_h)
    w_target = cal["carburisation_approach"] * sm.carbon_saturation(T_l_est - 273.15, hm_w) / 100.0
    nC_target = w_target / (1 - w_target) * m_other / 0.012011
    x_carb = max(nC_target - liq[IDX_LIQ["C_hm"]] + n_si, 0.0)
    _apply("D_carb", x_carb, gas, liq, sol)
    _apply("D_sio_abs", n_si, gas, liq, sol)
    rates["D_carb"] = x_carb
    rates["D_sio_abs"] = n_si
    # (5) sulfur from COS: split slag/metal by equilibrium partition
    n_cos = cal["s_gas_absorb_frac"] * gas[IDX_GAS["COS"]]
    if n_cos > 0 and liq[IDX_LIQ["Fe_l"]] > 0:
        n_sl = {k: liq[IDX_LIQ[k]] for k in LIQ if SPECIES[k].phase == "slag"}
        n_hm = {k: liq[IDX_LIQ[k]] for k in LIQ if SPECIES[k].phase == "hm"}
        w_sl = sm.slag_wt_pct(n_sl)
        w_hm = sm.hm_wt_pct(n_hm)
        Ls = sm.sulfur_partition(w_sl, w_hm, T)["L_S"] if sum(n_sl.values()) > 0 else 30.0
        m_sl = sum(v * SPECIES[k].molar_mass for k, v in n_sl.items())
        m_hm = sum(v * SPECIES[k].molar_mass for k, v in n_hm.items())
        f_slag = Ls * m_sl / max(Ls * m_sl + m_hm, 1e-12)
        x_sl = min(f_slag * n_cos, 0.9 * liq[IDX_LIQ["CaO_sl"]])
        _apply("D_cos_slag", x_sl, gas, liq, sol)
        _apply("D_cos_hm", n_cos - x_sl, gas, liq, sol)
        rates["D_cos_slag"], rates["D_cos_hm"] = x_sl, n_cos - x_sl
    # (6) alkali volatilisation
    fa = cal["alkali_volatile_frac"]
    for nm, key in (("D_k2o", "K2O_sl"), ("D_na2o", "Na2O_sl")):
        x = fa * liq[IDX_LIQ[key]]
        _apply(nm, x, gas, liq, sol)
        rates[nm] = x
    # coke availability guard
    c_used = -sol[IDX_SOLID["C_k"]]
    c_avail = 0.8 * dm_n[IDX_SOLID["C_k"]] / dt
    if c_used > c_avail:
        raise RuntimeError("CHILL: deadman/active-coke inventory exhausted by raw burden reaching the tuyeres - state outside model envelope")
    # heat exchange (effectiveness form) - outlet temperatures
    # inlet liquid mixture: shaft melts + raceway slag (stream enthalpy) and raw burden melted
    # inside the node (at node temperature)
    n_lin = liq_in.copy()
    H_lin = H_liq_in
    if dm_melt_liq is not None:
        n_lin = n_lin + dm_melt_liq
        H_lin = H_lin + float(dm_melt_liq @ TT_LIQ.h(T_dm))
    T_l_in = float(T_dm)
    if np.sum(n_lin) > 1e-12:
        T_l_in = float(TT_LIQ.T_from_H(n_lin, np.array(H_lin), np.array(T_dm)))
    T_l_out = T_dm + (T_l_in - T_dm) * np.exp(-cal["drip_ntu_liq"] * f_h) + cal.get("liquid_superheat_K", 0.0)
    # raw burden reaching the tuyere level is exposed to the hottest gas: the gas-to-node heat
    # transfer intensifies with the ore share of the node inventory (stabilising feedback)
    m_tot = float(np.sum(dm_n * TT_SOLID.mm))
    x_ore_dm = float(np.sum(np.where(ORE_MASK, dm_n, 0.0) * TT_SOLID.mm)) / max(m_tot, 1e-9)
    ntu_g = cal["drip_ntu_gas"] + cal.get("drip_ntu_gas_ore", 6.0) * x_ore_dm
    T_g_out = T_dm + (T_gas_in - T_dm) * np.exp(-ntu_g)
    liq = np.maximum(liq, 0.0)
    gas = np.maximum(gas, 0.0)
    H_g_in = float(gas_in @ TT_GAS.h(T_gas_in))
    H_g_out = float(gas @ TT_GAS.h(T_g_out))
    H_l_out = float(liq @ TT_LIQ.h(T_l_out))
    # species removed from the node (coke C, melted deadman ore) leave inside gas_out / liq_out, so
    # the node energy balance is simply streams-in minus streams-out
    Q_node = H_g_in + H_liq_in - H_g_out - H_l_out
    dm_consumed = -sol
    diag = {"h_drip_m": h_drip, "f_drip": f_h, "ntu_gas": ntu_g, "ore_mass_frac_node": x_ore_dm, "phi_SiO_abs": phi_si, "phi_Mn": phi_mn, "C_target_wt_pct": 100 * w_target,
            "T_liquid_in_C": T_l_in - 273.15, "T_liquid_out_C": T_l_out - 273.15, "T_gas_out_C": T_g_out - 273.15}
    return DripResult(gas, float(T_g_out), liq, float(T_l_out), H_l_out, dm_consumed, Q_node, rates, diag)
