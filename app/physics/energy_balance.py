"""Energy accounting.

1. Absolute-enthalpy balance (authoritative, every step): In - Out - Accumulation using species
   enthalpies that include formation enthalpy, so reaction heat is never double counted.
2. Conventional ironmaking heat balance (Hess path, 25 degC reference) for metallurgists.
   Every boundary stream enthalpy H is split into a formation part F (sum n*dHf298 of the base
   compound; hot-metal solutes are referred to the pure elements, F = 0) and a sensible part
   S = H - F (which for hot metal and slag includes fusion and solution heats).  Exactly:

       S_in + (F_in - F_out) = S_out + Q_losses + dH_inventory

   The chemical term (F_in - F_out) is decomposed into named reaction categories computed from
   boundary flows and recorded extents; 'unallocated' is the arithmetic remainder (small, from
   minor S/Zn/alkali/SiO chemistry and gangue that changed compound).
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from .species import (GAS, SOLID, LIQ, IDX_GAS, IDX_SOLID, IDX_LIQ, TT_GAS, TT_SOLID, HM_MASK, SLAG_MASK, SPECIES,
                      THERMO)

DHF_GAS = TT_GAS.dHf
DHF_SOLID = TT_SOLID.dHf
# formation of liquids referred to base compound (slag) and pure elements (hot-metal solutes)
DHF_LIQ_BASE = np.array([0.0 if SPECIES[k].phase == "hm" else THERMO[SPECIES[k].thermo].dHf for k in LIQ])


def dhf(key: str) -> float:
    return THERMO[SPECIES[key].thermo].dHf


def new_window() -> Dict[str, object]:
    z = lambda n: np.zeros(n)
    return {"t": 0.0, "charged_n": z(len(SOLID)), "charged_H": 0.0, "dust_n": z(len(SOLID)), "dust_H": 0.0,
            "ext_gas_n": z(len(GAS)), "ext_H": 0.0, "ext_F": 0.0, "pci_kg": 0.0, "pci_F_org": 0.0,
            "top_n": z(len(GAS)), "top_H": 0.0, "tap_n": z(len(LIQ)), "tap_H_hm": 0.0, "tap_H_sl": 0.0,
            "Q_shaft": 0.0, "Q_hearth": 0.0, "Q_tuyere": 0.0, "dH_inv": 0.0, "ext": {}, "pci_h2o_mol": 0.0,
            "pci_ash_Fe2O3_mol": 0.0, "melt_hm_kg": 0.0}


def conventional_heat_balance(w: Dict[str, object]) -> Dict[str, object]:
    t = max(float(w["t"]), 1e-9)
    tap = w["tap_n"]
    from .species import MM_LIQ
    hm_kg = float(np.sum(np.where(HM_MASK, tap, 0.0) * MM_LIQ))
    if hm_kg <= 1.0:
        hm_kg = float(w.get("melt_hm_kg", 1.0))
    top = w["top_n"]
    ch_net = w["charged_n"] - w["dust_n"]
    ext = w["ext"]
    # ---- sensible parts --------------------------------------------------------------------
    S_ext = w["ext_H"] - w["ext_F"]
    S_ch = (w["charged_H"] - w["dust_H"]) - float(ch_net @ DHF_SOLID)
    F_top = float(top @ DHF_GAS)
    S_top = w["top_H"] - F_top
    F_tap_sl = float(np.where(SLAG_MASK, tap, 0.0) @ DHF_LIQ_BASE)
    S_hm = w["tap_H_hm"]
    S_sl = w["tap_H_sl"] - F_tap_sl
    # ---- chemical heat (boundary flows) ----------------------------------------------------
    F_in = float(ch_net @ DHF_SOLID) + w["ext_F"]
    F_out = F_top + F_tap_sl
    chem = F_in - F_out
    n_calc = ext.get("R_calc", 0.0) + ext.get("M_caco3", 0.0)
    n_mg = ext.get("R_mgcalc", 0.0) + ext.get("M_mgco3", 0.0)
    n_carb_co2 = n_calc + n_mg
    n_co_k = ch_net[IDX_SOLID["CO_k"]]
    cat = {}
    cat["Carbon gasification to CO"] = -(top[IDX_GAS["CO"]] - n_co_k) * dhf("CO") + n_co_k * 0.0
    cat["Carbon gasification to CO2"] = -(top[IDX_GAS["CO2"]] - n_carb_co2) * dhf("CO2")
    n_evap = ext.get("R_dry", 0.0) + ext.get("R_dry_k", 0.0) + ext.get("M_h2o_l", 0.0)
    n_dehyd = ext.get("R_dehyd", 0.0) + ext.get("M_h2o_b", 0.0)
    cat["Moisture evaporation & LOI dehydration"] = -(n_evap * (dhf("H2O") - dhf("H2O_l")) + n_dehyd * (dhf("H2O") - dhf("H2O_b")))
    # hydrogen: everything H-bearing in vs out (net of evaporation)
    h_in = (ch_net[IDX_SOLID["H2O_l"]] * dhf("H2O_l") + ch_net[IDX_SOLID["H2O_k"]] * dhf("H2O_k")
            + ch_net[IDX_SOLID["H2O_b"]] * dhf("H2O_b") + w["ext_gas_n"][IDX_GAS["H2O"]] * dhf("H2O")
            + w["ext_gas_n"][IDX_GAS["CH4"]] * dhf("CH4") + w.get("pci_h2o_mol", 0.0) * dhf("H2O_l"))
    h_out = top[IDX_GAS["H2O"]] * dhf("H2O") + top[IDX_GAS["CH4"]] * dhf("CH4")
    cat["Hydrogen reactions (moisture dissociation, H2 -> H2O, net)"] = (h_in - h_out) - cat["Moisture evaporation & LOI dehydration"]
    # iron oxides
    fe_in = sum(ch_net[IDX_SOLID[k]] * dhf(k) for k in ("Fe2O3", "Fe3O4", "FeO", "Fe2O3_k"))
    fe_in += w.get("pci_ash_Fe2O3_mol", 0.0) * dhf("Fe2O3")
    cat["Iron oxide reduction"] = fe_in - tap[IDX_LIQ["FeO_sl"]] * dhf("FeO_sl")
    cat["Si, Mn, P reduction to hot metal"] = (tap[IDX_LIQ["Si_hm"]] * dhf("SiO2") + tap[IDX_LIQ["Mn_hm"]] * dhf("MnO")
                                               + 0.5 * tap[IDX_LIQ["P_hm"]] * dhf("P2O5"))
    cat["Carbonate decomposition"] = (n_calc * (dhf("CaCO3") - dhf("CaO") - dhf("CO2"))
                                      + n_mg * (dhf("MgCO3") - dhf("MgO") - dhf("CO2")))
    cat["PCI decomposition"] = w["pci_F_org"]
    allocated = sum(cat.values())
    unalloc = chem - allocated
    supply = {"Blast & injectant sensible heat": S_ext, "Charge sensible heat": S_ch}
    demand = {"Hot metal heat content (sensible, fusion, solution)": S_hm, "Slag heat content (sensible + fusion)": S_sl,
              "Top gas sensible heat": S_top, "Dust sensible heat": w["dust_H"] - float(w["dust_n"] @ DHF_SOLID),
              "Shaft wall / stave losses": w["Q_shaft"], "Hearth wall & bottom losses": w["Q_hearth"],
              "Tuyere cooling losses": w["Q_tuyere"], "Accumulation (inventories, refractory)": w["dH_inv"]}
    for k, v in cat.items():
        (supply if v >= 0 else demand)[k] = abs(v) if v >= 0 else -v
    rows = []
    for side, d in (("supply", supply), ("demand", demand)):
        for k, v in d.items():
            rows.append({"side": side, "item": k, "MW": v / t / 1e6, "GJ_per_tHM": v / hm_kg / 1e6})
    rows.append({"side": "closure", "item": "Unallocated chemical heat (S, Zn, alkali, SiO cycle, gangue)",
                 "MW": unalloc / t / 1e6, "GJ_per_tHM": unalloc / hm_kg / 1e6})
    sup = sum(supply.values())
    dem = sum(demand.values())
    return {"rows": rows, "supply_GJ_tHM": sup / hm_kg / 1e6, "demand_GJ_tHM": dem / hm_kg / 1e6,
            "closure_GJ_tHM": (sup - dem + unalloc) / hm_kg / 1e6, "unallocated_pct_of_supply": 100 * unalloc / max(sup, 1e-9),
            "window_h": t / 3600.0, "hot_metal_t": hm_kg / 1000.0,
            "method": "Hess path at 25 degC; S_in + (F_in - F_out) = S_out + Q_loss + dH_inv (exact)"}
