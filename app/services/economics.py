"""Hot-metal unit economics, energy intensity and carbon accounting (declared boundaries).

Cost (variable, per tHM, configurable currency - default INR):
  burden materials + coke + PCI + NG/H2 + flux + oxygen + blower electricity + stove fuel
  (blast heating duty / stove efficiency valued at BFG price) + other variable consumables
  - credits (granulated slag sale, net top-gas export valued at BFG price).
Fixed costs are reported separately (per day and per tHM at current production).
Recovered BF gas used for stoves is NOT also credited (no double counting).

Carbon (BF_GATE_TO_GATE default): direct CO2 = 44/12 x (C in - C in hot metal - C in dust)
assuming all top-gas carbon is ultimately oxidised wherever the gas is used; optional
scope-2 (electricity, oxygen) and upstream (coke making, sinter, pellet, HBI) factors are added
only when the selected boundary includes them. All factors are illustrative defaults.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from ..physics import outputs as out
from ..physics.species import IDX_GAS, IDX_LIQ, MM_LIQ, HM_MASK, TT_GAS, MOL_PER_NM3

LHV = {"CO": 12.63, "H2": 10.78, "CH4": 35.8}  # MJ/Nm3


def economics(m) -> Dict[str, object]:
    cfg = m.cfg
    ec = cfg["economics"]
    sp = out.specific(m, 4.0)
    prod_t_h = max(sp["production_t_d"] / 24.0, 1e-6)
    n_ch, V_ch, masses = m.charge_composition()
    ch_h = sp["charges_per_h"]
    rows = []

    def add(item, kg_tHM, price_per_t, unit="kg/tHM"):
        rows.append({"item": item, "specific": kg_tHM, "unit": unit, "price": price_per_t,
                     "cost_per_tHM": kg_tHM / 1000.0 * price_per_t})

    for k, kg in masses.items():
        if kg <= 0 or k == "coke":
            continue
        md = cfg["materials"][k]
        add(md["name"], kg * ch_h / prod_t_h, md["price_INR_t"])
    add("Coke (wet, as charged)", masses.get("coke", 0.0) * ch_h / prod_t_h, cfg["coke"]["price_INR_t"])
    add("PCI coal", sp["pci_kg_tHM"], cfg["pci"]["price_INR_t"])
    if sp["ng_Nm3_tHM"] > 0:
        rows.append({"item": "Natural gas", "specific": sp["ng_Nm3_tHM"], "unit": "Nm3/tHM",
                     "price": cfg["natural_gas"]["price_INR_Nm3"], "cost_per_tHM": sp["ng_Nm3_tHM"] * cfg["natural_gas"]["price_INR_Nm3"]})
    rows.append({"item": "Oxygen", "specific": sp["o2_Nm3_tHM"], "unit": "Nm3/tHM", "price": ec["oxygen_INR_Nm3"],
                 "cost_per_tHM": sp["o2_Nm3_tHM"] * ec["oxygen_INR_Nm3"]})
    kwh = sp["blast_Nm3_tHM"] * ec["blower_kWh_per_Nm3"]
    rows.append({"item": "Blower electricity", "specific": kwh, "unit": "kWh/tHM", "price": ec["electricity_INR_kWh"],
                 "cost_per_tHM": kwh * ec["electricity_INR_kWh"]})
    ext = m.last_ext
    T_b = ext.T_blast
    duty_W = float(ext.gas @ (TT_GAS.h(T_b) - TT_GAS.h(298.15)))  # blast heating duty
    stove_GJ_tHM = duty_W / max(ec["stove_efficiency"], 0.3) * 3600 / 1e9 / prod_t_h
    rows.append({"item": "Hot stove fuel (BFG equivalent)", "specific": stove_GJ_tHM, "unit": "GJ/tHM",
                 "price": ec["bfg_value_INR_GJ"], "cost_per_tHM": stove_GJ_tHM * ec["bfg_value_INR_GJ"]})
    rows.append({"item": "Other variable (refractory, clay, utilities, labour-variable)", "specific": 1, "unit": "lump",
                 "price": ec["other_variable_INR_tHM"], "cost_per_tHM": ec["other_variable_INR_tHM"]})
    tg = out.top_gas(m)
    dry = tg["dry_vol_pct"]
    lhv = tg["LHV_MJ_Nm3_dry"]
    gas_GJ_tHM = tg["dry_flow_Nm3_h"] * lhv / 1000 / prod_t_h
    net_export = max(gas_GJ_tHM - stove_GJ_tHM, 0.0)
    credits = [{"item": "Granulated slag sale", "specific": sp["slag_kg_tHM"], "unit": "kg/tHM", "price": ec["slag_credit_INR_t"],
                "cost_per_tHM": -sp["slag_kg_tHM"] / 1000 * ec["slag_credit_INR_t"]},
               {"item": "Net BF gas export (after stoves)", "specific": net_export, "unit": "GJ/tHM", "price": ec["bfg_value_INR_GJ"],
                "cost_per_tHM": -net_export * ec["bfg_value_INR_GJ"]}]
    var = sum(r["cost_per_tHM"] for r in rows)
    cred = sum(c["cost_per_tHM"] for c in credits)
    fixed_tHM = ec["fixed_cost_INR_day"] / max(sp["production_t_d"], 1e-6)
    return {"currency": ec["currency"], "rows": rows, "credits": credits, "variable_INR_tHM": var, "credits_INR_tHM": cred,
            "net_variable_INR_tHM": var + cred, "fixed_INR_tHM": fixed_tHM, "total_INR_tHM": var + cred + fixed_tHM,
            "fixed_INR_day": ec["fixed_cost_INR_day"], "top_gas_GJ_tHM": gas_GJ_tHM, "stove_GJ_tHM": stove_GJ_tHM,
            "note": "Illustrative prices; fixed costs kept separate from variable costs; stove BFG not double-credited."}


def carbon(m, boundary: str = None) -> Dict[str, object]:
    cfg = m.cfg
    cc = cfg["carbon"]
    boundary = boundary or cc["boundary"]
    w = m.hist.window(4.0, m.t)
    t = max(w.get("dt", 0.0), 1e-9)
    hm_t = max(w.get("hm_kg", 0.0) / 1000.0, 1e-9)
    # carbon inputs (per tHM) from model streams
    top = m.F_faces[:, -1].sum(axis=0)
    prod_t_s = hm_t / t
    C_top = (top[IDX_GAS["CO"]] + top[IDX_GAS["CO2"]] + top[IDX_GAS["CH4"]] + top[IDX_GAS["COS"]]) * 0.012011  # kg/s
    C_dust = top[IDX_GAS["Char"]] * 0.012011
    direct = 44.0 / 12.0 * C_top / max(prod_t_s, 1e-9)  # kgCO2/tHM leaving with top gas
    sp = out.specific(m, 4.0)
    items = [{"item": "Direct process CO2 (top-gas carbon, fully oxidised wherever used)", "kgCO2_tHM": direct, "scope": "1"}]
    total = direct
    if boundary in ("BF_PLUS_UTILITIES", "BF_PLUS_UPSTREAM"):
        el = sp["blast_Nm3_tHM"] * cfg["economics"]["blower_kWh_per_Nm3"] * cc["grid_EF_kgCO2_kWh"]
        ox = sp["o2_Nm3_tHM"] * cc["oxygen_EF_kgCO2_Nm3"]
        items += [{"item": "Blower electricity", "kgCO2_tHM": el, "scope": "2"},
                  {"item": "Oxygen (electricity for ASU)", "kgCO2_tHM": ox, "scope": "2"}]
        total += el + ox
    if boundary == "BF_PLUS_UPSTREAM":
        n_ch, V_ch, masses = m.charge_composition()
        ch_h = sp["charges_per_h"]
        prod_h = max(sp["production_t_d"] / 24, 1e-6)
        ef = {"coke": cc["coke_upstream_EF_kgCO2_t"], "sinter": cc["sinter_upstream_EF_kgCO2_t"],
              "pellet": cc["pellet_upstream_EF_kgCO2_t"], "hbi": cc["hbi_upstream_EF_kgCO2_t"]}
        for k, kg in masses.items():
            if k in ef and kg > 0:
                v = kg * ch_h / prod_h / 1000 * ef[k]
                items.append({"item": f"Upstream {k}", "kgCO2_tHM": v, "scope": "3"})
                total += v
    return {"boundary": boundary, "items": items, "kgCO2_tHM": total, "tCO2_day": total * sp["production_t_d"] / 1000.0,
            "dust_C_kg_tHM": C_dust / max(prod_t_s, 1e-9),
            "energy_GJ_tHM": _energy(m, sp), "method": "Carbon element balance of model streams; illustrative factors",
            "measured_or_estimated": "ESTIMATED (model)"}


def _energy(m, sp) -> Dict[str, float]:
    coke_GJ = sp["coke_kg_tHM"] / 1000 * m.cfg["coke"].get("HHV_MJ_kg", 29.5)
    pci_GJ = sp["pci_kg_tHM"] / 1000 * m.cfg["pci"]["HHV_MJ_kg"]
    ng_GJ = sp["ng_Nm3_tHM"] * 35.8 / 1000
    el_GJ = sp["blast_Nm3_tHM"] * m.cfg["economics"]["blower_kWh_per_Nm3"] * 3.6 / 1000
    return {"coke_GJ_tHM": coke_GJ, "pci_GJ_tHM": pci_GJ, "ng_GJ_tHM": ng_GJ, "electricity_GJ_tHM": el_GJ,
            "gross_GJ_tHM": coke_GJ + pci_GJ + ng_GJ + el_GJ}
