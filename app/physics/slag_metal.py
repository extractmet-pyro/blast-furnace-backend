"""Slag and hot-metal property models.

Implemented (with sources):
  * basicity ratios: B2 = CaO/SiO2, B3 = (CaO+MgO)/SiO2, B4 = (CaO+MgO)/(SiO2+Al2O3) (mass basis)
  * optical basicity Lambda (Duffy & Ingram; values as compiled by Mills & Sridhar 1999)
  * sulfide capacity log C_S = (22690 - 54640 Lambda)/T + 43.6 Lambda - 25.2
    (Sosinsky & Sommerville, Metall. Trans. B 17B (1986) 331)
  * S partition L_S = (%S)/[%S] = C_S f_S /(K_S p_O2^0.5), K_S from Sigworth & Elliott (1974),
    p_O2 fixed by C/CO equilibrium at hearth CO pressure, f_S from first-order interaction coefficients
  * carbon solubility (Neumann, 1968): [C]sat = 1.30 + 2.57e-3 T(degC) - 0.31[Si] - 0.33[P] - 0.40[S] + 0.028[Mn]
  * viscosity: modified Urbain model (Mills & Sridhar, Ironmaking Steelmaking 26 (1999) 262)
  * liquidus temperature: PROVISIONAL quadratic surrogate valid only inside a declared domain
    (B2 0.9-1.4, Al2O3 8-22 wt%, MgO 4-14 wt%); outside it the value is flagged OUT_OF_DOMAIN.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

from .species import SPECIES, R_GAS

OPT_BASICITY = {"CaO": 1.0, "MgO": 0.78, "SiO2": 0.48, "Al2O3": 0.605, "FeO": 1.0, "MnO": 1.0,
                "P2O5": 0.40, "K2O": 1.40, "Na2O": 1.15}
N_OXYGEN = {"CaO": 1, "MgO": 1, "SiO2": 2, "Al2O3": 3, "FeO": 1, "MnO": 1, "P2O5": 5, "K2O": 1, "Na2O": 1}
SLAG_OXIDES = ["CaO", "SiO2", "Al2O3", "MgO", "FeO", "MnO", "P2O5", "K2O", "Na2O"]

RHO_HM = 6900.0     # kg/m3 hot metal at ~1500 degC
RHO_SLAG = 2700.0   # kg/m3 BF slag at ~1500 degC


def slag_wt_pct(n_sl: Dict[str, float]) -> Dict[str, float]:
    """Mass percent of slag constituents from moles of *_sl species (CaS reported as S)."""
    mass = {}
    for ox in SLAG_OXIDES:
        k = ox + "_sl"
        mass[ox] = max(n_sl.get(k, 0.0), 0.0) * SPECIES[k].molar_mass
    n_cas = max(n_sl.get("CaS_sl", 0.0), 0.0)
    # CaS reported as CaO + S equivalent (convention: (CaO) and (S) analysed separately)
    mass["CaO"] += n_cas * SPECIES["CaO_sl"].molar_mass
    mass["S"] = n_cas * 0.03206
    tot = sum(mass.values())
    if tot <= 0:
        return {k: 0.0 for k in mass}
    return {k: 100.0 * v / tot for k, v in mass.items()}


def hm_wt_pct(n_hm: Dict[str, float]) -> Dict[str, float]:
    keys = {"Fe": "Fe_l", "C": "C_hm", "Si": "Si_hm", "Mn": "Mn_hm", "P": "P_hm", "S": "S_hm"}
    mass = {el: max(n_hm.get(k, 0.0), 0.0) * SPECIES[k].molar_mass for el, k in keys.items()}
    tot = sum(mass.values())
    if tot <= 0:
        return {k: 0.0 for k in mass}
    return {k: 100.0 * v / tot for k, v in mass.items()}


def basicities(w: Dict[str, float]) -> Dict[str, float]:
    si = max(w.get("SiO2", 0.0), 1e-9)
    b2 = w.get("CaO", 0.0) / si
    b3 = (w.get("CaO", 0.0) + w.get("MgO", 0.0)) / si
    b4 = (w.get("CaO", 0.0) + w.get("MgO", 0.0)) / max(si + w.get("Al2O3", 0.0), 1e-9)
    mg_al = w.get("MgO", 0.0) / max(w.get("Al2O3", 0.0), 1e-9)
    return {"B2": b2, "B3": b3, "B4": b4, "MgO_Al2O3": mg_al}


def optical_basicity(w: Dict[str, float]) -> float:
    num = den = 0.0
    for ox in SLAG_OXIDES:
        x = w.get(ox, 0.0) / (SPECIES[ox + "_sl"].molar_mass * 1000)
        num += x * N_OXYGEN[ox] * OPT_BASICITY[ox]
        den += x * N_OXYGEN[ox]
    return num / den if den > 0 else 0.6


def sulfide_capacity(lam: float, T: float) -> float:
    return 10.0 ** ((22690.0 - 54640.0 * lam) / T + 43.6 * lam - 25.2)


def log_fS(hm: Dict[str, float]) -> float:
    return 0.11 * hm.get("C", 4.5) + 0.063 * hm.get("Si", 0.5) - 0.028 * hm.get("S", 0.03) \
        - 0.026 * hm.get("Mn", 0.3) + 0.29 * hm.get("P", 0.1)


def p_O2_C_CO(T: float, p_CO_bar: float) -> float:
    dG = -223400.0 - 175.3 * T  # 2C + O2 = 2CO
    return p_CO_bar**2 * np.exp(dG / (R_GAS * T))


def sulfur_partition(w_slag: Dict[str, float], hm: Dict[str, float], T: float, p_CO_bar: float = 2.5) -> Dict[str, float]:
    lam = optical_basicity(w_slag)
    Cs = sulfide_capacity(lam, T)
    logKs = 7054.6 / T - 1.224
    pO2 = p_O2_C_CO(T, p_CO_bar)
    logLs = np.log10(Cs) + log_fS(hm) - logKs - 0.5 * np.log10(pO2)
    return {"Lambda": lam, "C_S": Cs, "L_S": float(10.0 ** logLs), "log_pO2": float(np.log10(pO2))}


def carbon_saturation(T_C: float, hm: Dict[str, float]) -> float:
    return 1.30 + 2.57e-3 * T_C - 0.31 * hm.get("Si", 0.5) - 0.33 * hm.get("P", 0.1) \
        - 0.40 * hm.get("S", 0.03) + 0.028 * hm.get("Mn", 0.3)


def urbain_viscosity(w: Dict[str, float], T: float) -> float:
    """Modified Urbain viscosity [Pa s] (Mills & Sridhar 1999)."""
    x = {ox: w.get(ox, 0.0) / (SPECIES[ox + "_sl"].molar_mass * 1000) for ox in SLAG_OXIDES}
    tot = sum(x.values())
    if tot <= 0:
        return float("nan")
    x = {k: v / tot for k, v in x.items()}
    N = x["SiO2"] + x["P2O5"]                  # glass formers
    xm = x["CaO"] + x["MgO"] + x["FeO"] + x["MnO"] + x["K2O"] + x["Na2O"]  # modifiers
    xa = x["Al2O3"]                             # amphoteric
    alpha = xm / max(xm + xa, 1e-9)
    B0 = 13.8 + 39.9355 * alpha - 44.049 * alpha**2
    B1 = 30.481 - 117.1505 * alpha + 129.9978 * alpha**2
    B2 = -40.9429 + 234.0486 * alpha - 300.04 * alpha**2
    B3 = 60.7619 - 153.9276 * alpha + 211.1616 * alpha**2
    B = B0 + B1 * N + B2 * N**2 + B3 * N**3
    lnA = -(0.2693 * B + 11.6725)
    eta_poise = np.exp(lnA) * T * np.exp(1000.0 * B / T)
    return float(eta_poise * 0.1)


def liquidus_estimate(w: Dict[str, float]) -> Dict[str, object]:
    """PROVISIONAL liquidus surrogate [degC] for CaO-SiO2-Al2O3-MgO BF slags."""
    b = basicities(w)
    B2, al, mg = b["B2"], w.get("Al2O3", 0.0), w.get("MgO", 0.0)
    T = 1395.0 + 420.0 * (B2 - 1.12) ** 2 + 95.0 * max(B2 - 1.12, 0.0) + 4.0 * (al - 14.0) - 6.0 * (mg - 8.0) \
        + 0.35 * (mg - 8.0) ** 2
    in_domain = 0.9 <= B2 <= 1.4 and 8.0 <= al <= 22.0 and 4.0 <= mg <= 14.0
    return {"value_C": float(T), "status": "PROVISIONAL" if in_domain else "OUT_OF_DOMAIN",
            "note": "Quadratic surrogate; replace with a FactSage/thermodynamic-database-derived model for plant use."}


def slag_summary(n_sl: Dict[str, float], n_hm: Dict[str, float], T: float) -> Dict[str, object]:
    w = slag_wt_pct(n_sl)
    hm = hm_wt_pct(n_hm)
    out = {"wt_pct": w, **basicities(w)}
    out.update(sulfur_partition(w, hm, T))
    out["viscosity_Pa_s"] = urbain_viscosity(w, T)
    out["viscosity_1500C_Pa_s"] = urbain_viscosity(w, 1773.15)
    out["liquidus"] = liquidus_estimate(w)
    out["C_sat_wt_pct"] = carbon_saturation(T - 273.15, hm)
    return out
