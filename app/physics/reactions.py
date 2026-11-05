"""Balanced reaction network with kinetic rate-law parameters.

All stoichiometries are verified for element conservation by tests/test_stoichiometry.py.
Rate laws are lumped engineering forms (per computational cell, mol/s):

  gas-solid reduction   r = k(T) * f_size * f_RI * n_oxide * DF,
                        DF = y_red - y_red,eq   (mole-fraction driving force, >= 0)
  carbon gasification   r = k(T) * f_CRI * n_C * DF,   DF = y_ox - y_ox,eq
  calcination           r = k(T) * n_carbonate * max(0, 1 - p_CO2/p_eq)
  first-order release   r = k(T) * n

  k(T) = k_ref * exp(-E/R * (1/T - 1/T_ref))

Every extent is additionally limited by (i) the equilibrium extent of the local gas
(no overshoot past equilibrium) and (ii) the available reactant inventory (positivity).
Parameters marked provisional are calibrated so that the illustrative demo furnace reproduces
typical large-BF operating values (gas utilisation, direct reduction degree, solution loss);
they must be re-identified against plant data (see docs/MODEL_VALIDATION.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Optional

import numpy as np

from .species import R_GAS, IDX_GAS, IDX_SOLID, IDX_LIQ, SPECIES


@dataclass(frozen=True)
class Kinetics:
    k_ref: float      # 1/s (per unit driving force where applicable)
    E: float          # J/mol
    T_ref: float      # K

    def k(self, T):
        return self.k_ref * np.exp(-self.E / R_GAS * (1.0 / np.asarray(T, dtype=float) - 1.0 / self.T_ref))


@dataclass(frozen=True)
class Reaction:
    stoich: Dict[str, float]
    equation: str
    zone: str
    eq_key: Optional[str] = None
    kinetics: Optional[Kinetics] = None
    kinetics_text: str = ""
    source: str = ""
    provisional: bool = True


_RED_SRC = "lumped mixed-control form after McKewan (1960), Bogdandy & Engell (1971); constants provisional"

REACTIONS: Dict[str, Reaction] = {
    # ---------------- shaft: indirect reduction ----------------
    "R_hem_CO": Reaction({"Fe2O3": -3, "CO": -1, "Fe3O4": 2, "CO2": 1}, "3Fe2O3 + CO -> 2Fe3O4 + CO2", "shaft",
                         "hem_CO", Kinetics(6.0e-3, 60e3, 1073.0), "r=k n DF", _RED_SRC),
    "R_mag_CO": Reaction({"Fe3O4": -1, "CO": -1, "FeO": 3, "CO2": 1}, "Fe3O4 + CO -> 3FeO + CO2", "shaft",
                         "mag_CO", Kinetics(4.8e-3, 75e3, 1173.0), "r=k n DF", _RED_SRC),
    "R_wus_CO": Reaction({"FeO": -1, "CO": -1, "Fe": 1, "CO2": 1}, "FeO + CO -> Fe + CO2", "shaft",
                         "wus_CO", Kinetics(6.0e-3, 70e3, 1173.0), "r=k n DF", _RED_SRC),
    "R_hem_H2": Reaction({"Fe2O3": -3, "H2": -1, "Fe3O4": 2, "H2O": 1}, "3Fe2O3 + H2 -> 2Fe3O4 + H2O", "shaft",
                         "hem_H2", Kinetics(1.8e-2, 50e3, 1073.0), "r=k n DF", _RED_SRC),
    "R_mag_H2": Reaction({"Fe3O4": -1, "H2": -1, "FeO": 3, "H2O": 1}, "Fe3O4 + H2 -> 3FeO + H2O", "shaft",
                         "mag_H2", Kinetics(1.5e-2, 65e3, 1173.0), "r=k n DF", _RED_SRC),
    "R_wus_H2": Reaction({"FeO": -1, "H2": -1, "Fe": 1, "H2O": 1}, "FeO + H2 -> Fe + H2O", "shaft",
                         "wus_H2", Kinetics(2.0e-2, 60e3, 1173.0), "r=k n DF", _RED_SRC),
    # ---------------- shaft: carbon gasification / gas reactions ----------------
    "R_boud": Reaction({"C_k": -1, "CO2": -1, "CO": 2}, "C(coke) + CO2 -> 2CO (solution loss)", "shaft",
                       "boud", Kinetics(1.2e-3, 230e3, 1273.0), "r=k f_CRI n_C DF_CO2",
                       "Turkdogan & Vinters (1970) form; constants provisional"),
    "R_wg": Reaction({"C_k": -1, "H2O": -1, "CO": 1, "H2": 1}, "C(coke) + H2O -> CO + H2", "shaft",
                     "wg", Kinetics(2.5e-3, 200e3, 1273.0), "r=k f_CRI n_C DF_H2O", "provisional"),
    "R_wgs": Reaction({"CO": -1, "H2O": -1, "CO2": 1, "H2": 1}, "CO + H2O <-> CO2 + H2", "shaft",
                      "wgs", Kinetics(2.0e-1, 80e3, 1173.0), "relaxation to equilibrium (Fe-catalysed)", "provisional"),
    "R_char_boud": Reaction({"Char": -1, "CO2": -1, "CO": 2}, "C(char) + CO2 -> 2CO", "shaft",
                            "boud", Kinetics(5.0e-4, 180e3, 1273.0), "r=k n_char DF_CO2", "provisional"),
    "R_char_wg": Reaction({"Char": -1, "H2O": -1, "CO": 1, "H2": 1}, "C(char) + H2O -> CO + H2", "shaft",
                          "wg", Kinetics(1.0e-3, 170e3, 1273.0), "r=k n_char DF_H2O", "provisional"),
    "R_dr_hem": Reaction({"Fe2O3": -1, "C_k": -1, "FeO": 2, "CO": 1}, "Fe2O3 + C -> 2FeO + CO (high T)", "shaft",
                         None, Kinetics(2.0e-4, 250e3, 1373.0), "r=k n", "provisional"),
    "R_dr_mag": Reaction({"Fe3O4": -1, "C_k": -1, "FeO": 3, "CO": 1}, "Fe3O4 + C -> 3FeO + CO (high T)", "shaft",
                         None, Kinetics(2.0e-4, 250e3, 1373.0), "r=k n", "provisional"),
    # ---------------- shaft: decomposition / drying / volatiles ----------------
    "R_calc": Reaction({"CaCO3": -1, "CaO": 1, "CO2": 1}, "CaCO3 -> CaO + CO2", "shaft", "calc",
                       Kinetics(1.5e-3, 180e3, 1173.0), "r=k n (1-p_CO2/p_eq)", "provisional"),
    "R_mgcalc": Reaction({"MgCO3": -1, "MgO": 1, "CO2": 1}, "MgCO3 -> MgO + CO2 (dolomitic)", "shaft", "mgcalc",
                         Kinetics(1.5e-3, 220e3, 1023.0), "r=k n (1-p_CO2/p_eq)", "provisional"),
    "R_dry": Reaction({"H2O_l": -1, "H2O": 1}, "H2O(l) -> H2O(g) (burden moisture)", "shaft", None,
                      Kinetics(2.0e-2, 0.0, 373.15), "r=k n phi(T-373)", "provisional"),
    "R_dry_k": Reaction({"H2O_k": -1, "H2O": 1}, "H2O(l) -> H2O(g) (coke moisture)", "shaft", None,
                        Kinetics(2.0e-2, 0.0, 373.15), "r=k n phi(T-373)", "provisional"),
    "R_dehyd": Reaction({"H2O_b": -1, "H2O": 1}, "2FeOOH -> Fe2O3 + H2O (combined water)", "shaft", None,
                        Kinetics(2.0e-3, 120e3, 623.0), "r=k n", "Robie & Hemingway enthalpy; kinetics provisional"),
    "R_vm_H2": Reaction({"H2_k": -1, "H2": 1}, "coke volatile H -> H2", "shaft", None,
                        Kinetics(3.0e-4, 150e3, 1173.0), "r=k n", "provisional"),
    "R_vm_N2": Reaction({"N2_k": -1, "N2": 1}, "coke volatile N -> N2", "shaft", None,
                        Kinetics(1.5e-4, 150e3, 1273.0), "r=k n", "provisional"),
    "R_vm_CO": Reaction({"CO_k": -1, "CO": 1}, "coke volatile O -> CO", "shaft", None,
                        Kinetics(3.0e-4, 150e3, 1173.0), "r=k n", "provisional"),
    "R_zn": Reaction({"ZnO": -1, "CO": -1, "Zn": 1, "CO2": 1}, "ZnO + CO -> Zn(g) + CO2", "shaft", None,
                     Kinetics(1.0e-3, 150e3, 1273.0), "r=k n (T>1100 K)", "provisional"),
    "R_sio_ox": Reaction({"SiO": -1, "CO": -1, "SiO2": 1, "C_ore": 1}, "SiO(g) + CO -> SiO2 + C (re-oxidation)",
                         "shaft", None, None, "fraction per cell f(T)", "provisional"),
    # ---------------- dripping zone / deadman ----------------
    "D_feo": Reaction({"FeO_sl": -1, "C_k": -1, "Fe_l": 1, "CO": 1}, "(FeO) + C -> Fe + CO (direct reduction)",
                      "dripping", None, Kinetics(4.0e-2, 200e3, 1773.0), "fraction 1-exp(-k tau)", "provisional"),
    "D_carb": Reaction({"C_k": -1, "C_hm": 1}, "C(coke) -> [C] (carburisation)", "dripping", None,
                       None, "approach to Neumann saturation", "Neumann (1968) solubility"),
    "D_sio_abs": Reaction({"SiO": -1, "C_hm": -1, "Si_hm": 1, "CO": 1}, "SiO + [C] -> [Si] + CO", "dripping", None,
                          Kinetics(0.55, 120e3, 1773.0), "absorbed fraction f(T)", "provisional"),
    "D_mno": Reaction({"MnO_sl": -1, "C_k": -1, "Mn_hm": 1, "CO": 1}, "(MnO) + C -> [Mn] + CO", "dripping", None,
                      None, "partition-controlled", "provisional"),
    "D_p2o5": Reaction({"P2O5_sl": -1, "C_k": -5, "P_hm": 2, "CO": 5}, "(P2O5) + 5C -> 2[P] + 5CO", "dripping",
                       None, None, "near-complete (configurable yield)", "operating practice"),
    "D_k2o": Reaction({"K2O_sl": -1, "C_k": -1, "K": 2, "CO": 1}, "(K2O) + C -> 2K(g) + CO", "dripping", None,
                      None, "configurable volatilised fraction", "provisional"),
    "D_na2o": Reaction({"Na2O_sl": -1, "C_k": -1, "Na": 2, "CO": 1}, "(Na2O) + C -> 2Na(g) + CO", "dripping",
                       None, None, "configurable volatilised fraction", "provisional"),
    "D_cos_slag": Reaction({"COS": -1, "CaO_sl": -1, "C_k": -1, "CaS_sl": 1, "CO": 2}, "COS + (CaO) + C -> (CaS) + 2CO",
                           "dripping", None, None, "absorbed fraction", "provisional"),
    "D_cos_hm": Reaction({"COS": -1, "S_hm": 1, "CO": 1}, "COS -> [S] + CO", "dripping", None, None,
                         "absorbed fraction", "provisional"),
    # ---------------- cohesive-zone melting conversions ----------------
    "M_fes": Reaction({"FeS": -1, "Fe_l": 1, "S_hm": 1}, "FeS -> Fe + [S] (on melting)", "cohesive", None, None, "on melting", "-"),
    "M_caco3": Reaction({"CaCO3": -1, "CaO_sl": 1, "CO2": 1}, "CaCO3 -> (CaO) + CO2 (residual, on melting)", "cohesive", None, None, "on melting", "-"),
    "M_mgco3": Reaction({"MgCO3": -1, "MgO_sl": 1, "CO2": 1}, "MgCO3 -> (MgO) + CO2 (residual, on melting)", "cohesive", None, None, "on melting", "-"),
    # ---------------- hearth slag-metal ----------------
    "H_desulf": Reaction({"S_hm": -1, "CaO_sl": -1, "C_hm": -1, "CaS_sl": 1, "CO": 1}, "[S] + (CaO) + [C] -> (CaS) + CO",
                         "hearth", None, Kinetics(1.0e-3, 0.0, 1773.0), "relaxation to L_S equilibrium",
                         "Sosinsky & Sommerville (1986) sulfide capacity"),
    "H_desi_feo": Reaction({"Si_hm": -1, "FeO_sl": -2, "SiO2_sl": 1, "Fe_l": 2}, "[Si] + 2(FeO) -> (SiO2) + 2Fe", "hearth",
                           None, Kinetics(2.0e-4, 0.0, 1773.0), "r=k n_Si x_FeO", "provisional"),
    "H_desi_mno": Reaction({"Si_hm": -1, "MnO_sl": -2, "SiO2_sl": 1, "Mn_hm": 2}, "[Si] + 2(MnO) -> (SiO2) + 2[Mn]",
                           "hearth", None, Kinetics(2.0e-4, 0.0, 1773.0), "r=k n_Si x_MnO", "provisional"),
    "H_mn": Reaction({"MnO_sl": -1, "C_hm": -1, "Mn_hm": 1, "CO": 1}, "(MnO) + [C] -> [Mn] + CO", "hearth", None,
                     Kinetics(2.0e-4, 0.0, 1773.0), "relaxation to partition", "provisional"),
    # ---------------- raceway ----------------
    "RW_O2": Reaction({"C_k": -2, "O2": -1, "CO": 2}, "2C + O2 -> 2CO (raceway net)", "raceway", None, None,
                      "complete (O2-limited)", "stoichiometric"),
    "RW_H2O": Reaction({"C_k": -1, "H2O": -1, "CO": 1, "H2": 1}, "C + H2O -> CO + H2 (raceway)", "raceway", None, None,
                       "complete", "stoichiometric"),
    "RW_CH4": Reaction({"CH4": -2, "O2": -1, "CO": 2, "H2": 4}, "2CH4 + O2 -> 2CO + 4H2", "raceway", None, None,
                       "complete", "stoichiometric"),
    "RW_sio": Reaction({"SiO2_k": -1, "C_k": -1, "SiO": 1, "CO": 1}, "SiO2(ash) + C -> SiO(g) + CO", "raceway", None,
                       Kinetics(0.35, 260e3, 2373.0), "volatilised fraction f(T_raceway)", "provisional"),
    "RW_cos": Reaction({"S_k": -1, "C_k": -1, "O2": -0.5, "COS": 1}, "S + C + 1/2 O2 -> COS", "raceway", None, None,
                       "complete", "stoichiometric"),
    "RW_ashfe": Reaction({"Fe2O3_k": -1, "C_k": -3, "Fe_l": 2, "CO": 3}, "Fe2O3(ash) + 3C -> 2Fe + 3CO", "raceway",
                         None, None, "complete", "stoichiometric"),
}


@dataclass
class KineticSet:
    """Mutable copy of kinetic constants with calibration multipliers (observer/plant tuning)."""
    mult: Dict[str, float] = field(default_factory=dict)

    def k(self, name: str, T):
        r = REACTIONS[name]
        return r.kinetics.k(T) * self.mult.get(name, 1.0)


@dataclass
class CompiledReaction:
    g_idx: np.ndarray
    g_nu: np.ndarray
    s_idx: np.ndarray
    s_nu: np.ndarray
    l_idx: np.ndarray
    l_nu: np.ndarray


def compile_reaction(name: str) -> CompiledReaction:
    st = REACTIONS[name].stoich
    g = [(IDX_GAS[k], v) for k, v in st.items() if SPECIES[k].phase == "gas"]
    s = [(IDX_SOLID[k], v) for k, v in st.items() if SPECIES[k].phase in ("ore", "coke")]
    l_ = [(IDX_LIQ[k], v) for k, v in st.items() if SPECIES[k].phase in ("hm", "slag")]

    def arr(p):
        return (np.array([i for i, _ in p], dtype=int), np.array([v for _, v in p], dtype=float))

    gi, gn = arr(g)
    si, sn = arr(s)
    li, ln = arr(l_)
    return CompiledReaction(gi, gn, si, sn, li, ln)


COMPILED: Dict[str, CompiledReaction] = {k: compile_reaction(k) for k in REACTIONS}


def kinetics_table() -> list:
    rows = []
    for name, r in REACTIONS.items():
        kin = r.kinetics
        rows.append({
            "name": name, "equation": r.equation, "zone": r.zone,
            "k_ref_per_s": kin.k_ref if kin else None, "E_kJ_per_mol": (kin.E / 1000 if kin else None),
            "T_ref_K": kin.T_ref if kin else None, "rate_form": r.kinetics_text, "equilibrium": r.eq_key,
            "source": r.source, "provisional": r.provisional,
        })
    return rows


def with_multiplier(kin: Kinetics, m: float) -> Kinetics:
    return replace(kin, k_ref=kin.k_ref * m)
