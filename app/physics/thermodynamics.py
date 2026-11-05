"""Thermodynamic functions: equilibrium constants, reaction enthalpies, diagrams.

Two consistent layers are used:
1. Energy bookkeeping uses absolute species enthalpies (species.py).
2. Gas-solid equilibria that control driving forces use established log10 K = A/T + B
   correlations for the Fe-O-C-H system (consistent with the Baur-Glaessner diagram) because
   wuestite non-stoichiometry makes "FeO" species-derived constants less accurate.

Standard states: pure condensed phases (activity 1), ideal gases at p0 = 1 bar (= 1e5 Pa).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np

from .species import R_GAS, ThermoTable, SPECIES, THERMO, T0

LN10 = np.log(10.0)
P_REF = 1.0e5  # Pa


@dataclass(frozen=True)
class LogK:
    reaction: str
    A: float
    B: float
    source: str
    t_range: tuple = (700.0, 2000.0)

    def logK(self, T):
        return self.A / np.asarray(T, dtype=float) + self.B

    def K(self, T):
        return 10.0 ** self.logK(T)


# Literature correlations (log10 K = A/T + B, T in K)
LOGK: Dict[str, LogK] = {
    # Fe-O-C: e.g. Turkdogan, Physical Chemistry of High Temperature Technology (1980);
    # Bogdandy & Engell, The Reduction of Iron Ores (1971).
    "hem_CO": LogK("3Fe2O3 + CO = 2Fe3O4 + CO2", 2726.0, 2.144, "Bogdandy & Engell (1971)"),
    "mag_CO": LogK("Fe3O4 + CO = 3FeO + CO2", -1834.0, 2.17, "Bogdandy & Engell (1971)", (843, 1650)),
    "wus_CO": LogK("FeO + CO = Fe + CO2", 688.0, -0.90, "Bogdandy & Engell (1971)", (843, 1650)),
    # Water-gas shift: Moe, Chem. Eng. Prog. 58 (1962) 33: K = exp(4577.8/T - 4.33)
    "wgs": LogK("CO + H2O = CO2 + H2", 4577.8 / LN10, -4.33 / LN10, "Moe (1962)", (500, 1800)),
    # Boudouard: dG = 170700 - 174.5 T J/mol (Turkdogan 1980)
    "boud": LogK("C + CO2 = 2CO", -170700.0 / (R_GAS * LN10), 174.5 / (R_GAS * LN10), "Turkdogan (1980)", (700, 2000)),
    # CaCO3 decomposition, p_CO2 [atm] (e.g. Gaskell); MgCO3 from dH~117 kJ, T(1 atm)~664 K
    "calc": LogK("CaCO3 = CaO + CO2 (p_CO2/atm)", -8908.0, 7.53, "Gaskell / Turkdogan", (800, 1400)),
    "mgcalc": LogK("MgCO3 = MgO + CO2 (p_CO2/atm)", -6111.0, 9.20, "RH-derived (dH 116.6 kJ)", (500, 1200)),
}
# derived correlations (exact combinations of the above)
_w = LOGK["wgs"]
LOGK["hem_H2"] = LogK("3Fe2O3 + H2 = 2Fe3O4 + H2O", LOGK["hem_CO"].A - _w.A, LOGK["hem_CO"].B - _w.B, "derived: hem_CO - wgs")
LOGK["mag_H2"] = LogK("Fe3O4 + H2 = 3FeO + H2O", LOGK["mag_CO"].A - _w.A, LOGK["mag_CO"].B - _w.B, "derived: mag_CO - wgs")
LOGK["wus_H2"] = LogK("FeO + H2 = Fe + H2O", LOGK["wus_CO"].A - _w.A, LOGK["wus_CO"].B - _w.B, "derived: wus_CO - wgs")
LOGK["wg"] = LogK("C + H2O = CO + H2", LOGK["boud"].A + _w.A, LOGK["boud"].B + _w.B, "derived: boud + wgs")


def K(name: str, T):
    return LOGK[name].K(T)


def y_red_eq(name: str, T):
    """Equilibrium fraction of reducing gas y_red/(y_red + y_ox) for oxide reduction."""
    return 1.0 / (1.0 + K(name, T))


def p_co2_calcination(T, kind="calc"):
    """Equilibrium CO2 pressure over carbonate [Pa]."""
    return K(kind, T) * 101325.0


# ----------------------------------------------------------------------------------------
# species-based reaction thermochemistry
# ----------------------------------------------------------------------------------------

def reaction_props(stoich: Dict[str, float], T) -> Dict[str, np.ndarray]:
    """dH, dS, dG [J/mol-extent] from the species database for a stoichiometry dict."""
    keys = list(stoich)
    tt = ThermoTable(keys)
    nu = np.array([stoich[k] for k in keys])
    T = np.atleast_1d(np.asarray(T, dtype=float))
    h = tt.h(T)
    s = tt.s(T)
    dH = h @ nu
    dS = s @ nu
    dG = dH - T * dS
    return {"T": T, "dH": dH, "dS": dS, "dG": dG, "K": np.exp(-dG / (R_GAS * T))}


def element_residual(stoich: Dict[str, float]) -> Dict[str, float]:
    """Element balance of a stoichiometry dict (should be all zero)."""
    out: Dict[str, float] = {}
    for k, nu in stoich.items():
        for el, n in SPECIES[k].formula.items():
            out[el] = out.get(el, 0.0) + nu * n
    return {k: v for k, v in out.items() if abs(v) > 1e-12}


# ----------------------------------------------------------------------------------------
# diagrams for the GUI
# ----------------------------------------------------------------------------------------

ELLINGHAM: List[dict] = [
    # dG0 = A + B*T  [J per mol O2]; approximate textbook values (Gaskell 2008; Turkdogan 1980)
    {"reaction": "2Fe + O2 = 2FeO", "A": -528000.0, "B": 129.3},
    {"reaction": "6FeO + O2 = 2Fe3O4", "A": -624400.0, "B": 250.2},
    {"reaction": "4Fe3O4 + O2 = 6Fe2O3", "A": -498900.0, "B": 281.4},
    {"reaction": "2C + O2 = 2CO", "A": -223400.0, "B": -175.3},
    {"reaction": "C + O2 = CO2", "A": -394100.0, "B": -0.8},
    {"reaction": "2CO + O2 = 2CO2", "A": -564800.0, "B": 173.6},
    {"reaction": "2H2 + O2 = 2H2O(g)", "A": -492900.0, "B": 109.6},
    {"reaction": "Si + O2 = SiO2", "A": -907100.0, "B": 175.7},
    {"reaction": "2Mn + O2 = 2MnO", "A": -769400.0, "B": 145.6},
    {"reaction": "4/3Al + O2 = 2/3Al2O3", "A": -1117000.0, "B": 213.0},
    {"reaction": "2Ca + O2 = 2CaO", "A": -1267000.0, "B": 213.0},
    {"reaction": "2Mg + O2 = 2MgO", "A": -1203000.0, "B": 216.0},
]


def ellingham(T_min=500.0, T_max=2300.0, n=60) -> dict:
    T = np.linspace(T_min, T_max, n)
    lines = [{"reaction": e["reaction"], "dG_kJ_per_molO2": ((e["A"] + e["B"] * T) / 1000).round(2).tolist()} for e in ELLINGHAM]
    return {"T_K": T.round(1).tolist(), "T_C": (T - 273.15).round(1).tolist(), "lines": lines,
            "note": "Approximate dG0 = A + B*T lines (per mol O2) for orientation; not a substitute for assessed databases."}


def baur_glaessner(P_bar=1.0, T_min=843.0, T_max=1573.0, n=60) -> dict:
    T = np.linspace(T_min, T_max, n)
    out = {"T_C": (T - 273.15).round(1).tolist()}
    for nm in ("mag_CO", "wus_CO", "mag_H2", "wus_H2"):
        out[nm] = (100 * y_red_eq(nm, T)).round(2).tolist()
    # Boudouard: x^2 P/(1-x) = K  ->  %CO for total pressure P (bar)
    Tb = np.linspace(673.0, T_max, n)
    Kb = K("boud", Tb)
    out["boud_T_C"] = (Tb - 273.15).round(1).tolist()
    for p in sorted({1.0, round(P_bar, 2)}):
        x = (-Kb + np.sqrt(Kb**2 + 4 * p * Kb)) / (2 * p)
        out[f"boud_{p}bar"] = (100 * x).round(2).tolist()
    out["note"] = "Equilibrium %CO in CO-CO2 (or %H2 in H2-H2O) for the Fe-O-C-H system."
    return out


def reaction_catalogue_table(T_list=(900.0, 1100.0, 1300.0, 1500.0, 1800.0)) -> List[dict]:
    from .reactions import REACTIONS  # local import to avoid cycle

    rows = []
    T = np.array(T_list)
    for name, r in REACTIONS.items():
        props = reaction_props(r.stoich, T)
        rows.append({
            "name": name, "equation": r.equation, "zone": r.zone,
            "dH_kJ": (props["dH"] / 1000).round(2).tolist(),
            "dG_species_kJ": (props["dG"] / 1000).round(2).tolist(),
            "logK_lit": (LOGK[r.eq_key].logK(T).round(3).tolist() if r.eq_key else None),
            "kinetics": r.kinetics_text, "source": r.source, "provisional": r.provisional,
        })
    return [{"T_K": list(T_list)}] + rows
