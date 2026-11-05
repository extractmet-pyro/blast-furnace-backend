"""Species registry for the blast-furnace model.

Every species carries
  * an elemental formula (used for exact elemental bookkeeping),
  * a phase/carrier label (gas, ore-carrier solid, coke-carrier solid, hot metal, slag),
  * a thermochemical record: standard enthalpy of formation at 298.15 K, standard entropy
    and a Kubaschewski-type heat-capacity polynomial

        Cp = a + b*1e-3*T + c*1e5*T**-2 + d*1e-6*T**2      [J/(mol K)]

    plus an additive constant ``h_add`` (fusion / binding / solution enthalpy) [J/mol].

Absolute enthalpies h(T) = dHf298 + int Cp dT + h_add are used everywhere, so chemical
reaction heat appears automatically in the energy balance and is never double counted.

Data sources (declared; values rounded to the precision used here):
  [JANAF]  M.W. Chase, NIST-JANAF Thermochemical Tables, 4th ed., J. Phys. Chem. Ref. Data
           Monograph 9 (1998).
  [KAS]    O. Kubaschewski, C.B. Alcock, P.J. Spencer, Materials Thermochemistry, 6th ed.,
           Pergamon (1993) - Cp polynomial form and most coefficients.
  [RH]     R.A. Robie, B.S. Hemingway, USGS Bulletin 2131 (1995) - carbonates, goethite.
  [SE]     G.K. Sigworth, J.F. Elliott, Metal Science 8 (1974) 298-310 - heats of solution
           of C, Si, Mn, P, S in liquid iron (1 wt% standard state).
Entries flagged ``provisional=True`` are smoothed/effective fits (e.g. through magnetic or
allotropic transitions) or estimates for minor species; they are adequate for plant-scale
energy balances but are listed in docs/MODEL_EQUATIONS.md as awaiting refinement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np

T0 = 298.15
R_GAS = 8.314462618  # J/(mol K)
MOL_PER_NM3 = 101325.0 / (R_GAS * 273.15)  # 44.615 mol per normal m3 (0 degC, 101.325 kPa)

ELEMENTS: List[str] = ["Fe", "C", "O", "H", "N", "Si", "Al", "Ca", "Mg", "Mn", "P", "S", "K", "Na", "Zn"]
ATOMIC_MASS: Dict[str, float] = {
    "Fe": 55.845, "C": 12.011, "O": 15.999, "H": 1.008, "N": 14.007, "Si": 28.085,
    "Al": 26.982, "Ca": 40.078, "Mg": 24.305, "Mn": 54.938, "P": 30.974, "S": 32.06,
    "K": 39.098, "Na": 22.990, "Zn": 65.38,
}

SLAG_FUSION_J_PER_G = 230.0  # effective slag formation/fusion enthalpy, provisional [J/g]


@dataclass(frozen=True)
class Thermo:
    dHf: float          # J/mol at 298.15 K
    S298: float         # J/(mol K)
    a: float
    b: float = 0.0
    c: float = 0.0
    d: float = 0.0
    source: str = "KAS"
    provisional: bool = False
    t_max: float = 2500.0


# --- base thermochemical records -------------------------------------------------------
THERMO: Dict[str, Thermo] = {
    # gases
    "CO": Thermo(-110530.0, 197.66, 28.41, 4.10, -0.46, source="JANAF/KAS"),
    "CO2": Thermo(-393520.0, 213.79, 44.14, 9.04, -8.54, source="JANAF/KAS"),
    "H2": Thermo(0.0, 130.68, 27.28, 3.26, 0.50, source="JANAF/KAS", t_max=3000),
    "H2O": Thermo(-241830.0, 188.83, 30.00, 10.71, 0.33, source="JANAF/KAS"),
    "N2": Thermo(0.0, 191.61, 27.87, 4.27, 0.0, source="JANAF/KAS"),
    "O2": Thermo(0.0, 205.15, 29.96, 4.18, -1.67, source="JANAF/KAS", t_max=3000),
    "CH4": Thermo(-74870.0, 186.26, 23.64, 47.86, -1.92, source="JANAF/KAS", t_max=1500),
    "COS": Thermo(-138400.0, 231.57, 47.6, 9.1, -6.3, source="JANAF (Cp fit)", provisional=True),
    "SiO": Thermo(-100420.0, 211.6, 36.0, 1.2, -5.4, source="JANAF (Cp fit)", provisional=True),
    "Zn_g": Thermo(130400.0, 161.0, 20.79, source="JANAF"),
    "K_g": Thermo(89000.0, 160.3, 20.79, source="JANAF"),
    "Na_g": Thermo(107500.0, 153.7, 20.79, source="JANAF"),
    # condensed elements / compounds
    "C": Thermo(0.0, 5.74, 17.15, 4.27, -8.79, source="KAS (graphite)", t_max=3000),
    # Fe: effective Cp fitted to JANAF enthalpy increments 298-1800 K (smooths Curie/alpha-gamma)
    "Fe": Thermo(0.0, 27.28, 27.56, 10.43, 0.0, source="JANAF (H-increment fit)", provisional=True),
    "FeO": Thermo(-272040.0, 60.75, 50.80, 8.61, -3.31, source="JANAF/KAS"),
    "Fe3O4": Thermo(-1118400.0, 146.4, 175.0, 40.0, 0.0, source="JANAF (H-increment fit)", provisional=True),
    "Fe2O3": Thermo(-824200.0, 87.40, 98.28, 77.82, -14.85, source="KAS"),
    "CaCO3": Thermo(-1207600.0, 91.7, 104.52, 21.92, -25.94, source="RH/KAS"),
    "MgCO3": Thermo(-1111700.0, 65.1, 77.91, 57.74, -17.41, source="RH/KAS"),
    "CaO": Thermo(-635090.0, 38.1, 49.62, 4.52, -6.95, source="KAS"),
    "MgO": Thermo(-601600.0, 26.95, 48.99, 3.14, -11.44, source="KAS"),
    # SiO2: smoothed alpha/beta-quartz/cristobalite fit
    "SiO2": Thermo(-910700.0, 41.46, 56.0, 12.0, -13.0, source="JANAF (smoothed)", provisional=True),
    "Al2O3": Thermo(-1675700.0, 50.92, 114.77, 12.80, -35.44, source="KAS"),
    "MnO": Thermo(-385200.0, 59.71, 46.48, 8.12, -3.68, source="KAS"),
    "P2O5": Thermo(-1504900.0, 114.4, 70.0, 60.0, 0.0, source="JANAF (per P2O5, Cp est.)", provisional=True),
    "FeS": Thermo(-100000.0, 60.3, 50.0, 12.0, 0.0, source="JANAF (Cp est.)", provisional=True),
    "CaS": Thermo(-473200.0, 56.5, 42.68, 15.90, 0.0, source="KAS", provisional=True),
    "K2O": Thermo(-363200.0, 94.1, 72.0, 40.0, 0.0, source="JANAF (Cp est.)", provisional=True),
    "Na2O": Thermo(-418000.0, 75.1, 55.0, 47.0, 0.0, source="JANAF (Cp est.)", provisional=True),
    "ZnO": Thermo(-350500.0, 43.65, 49.0, 5.1, -9.1, source="KAS"),
    "S": Thermo(0.0, 32.07, 22.0, 0.0, 0.0, source="estimate (bound S in coke)", provisional=True),
    "H2O_liq": Thermo(-285830.0, 69.95, 75.3, 0.0, 0.0, source="JANAF"),
    # combined (goethite) water: dehydration 2FeOOH -> Fe2O3 + H2O(g) ~ +52.6 kJ/mol H2O [RH]
    "H2O_bound": Thermo(-294430.0, 60.0, 75.0, 0.0, 0.0, source="RH (goethite)", provisional=True),
    # solutes in liquid iron: dHf includes heat of solution to 1 wt% std state [SE]
    "C_dis": Thermo(22600.0, 0.0, 17.15, 4.27, -8.79, source="SE + graphite Cp", provisional=True),
    "Si_dis": Thermo(-81300.0, 0.0, 27.2, 0.0, 0.0, source="SE (Si(s)->Si(l)->[Si])", provisional=True),
    "Mn_dis": Thermo(16200.0, 0.0, 46.0, 0.0, 0.0, source="SE", provisional=True),
    "P_dis": Thermo(-50200.0, 0.0, 30.0, 0.0, 0.0, source="SE + JANAF P2(g)", provisional=True),
    "S_dis": Thermo(-70800.0, 0.0, 32.0, 0.0, 0.0, source="SE + JANAF S2(g)", provisional=True),
}


@dataclass(frozen=True)
class Species:
    key: str
    formula: Dict[str, float]
    phase: str            # 'gas', 'ore', 'coke', 'hm' (hot metal), 'slag'
    thermo: str           # key into THERMO
    h_add: float = 0.0    # J/mol added to the base record (fusion, solution, ...)
    label: str = ""
    molar_mass: float = field(default=0.0)

    def mm(self) -> float:
        return sum(ATOMIC_MASS[e] * n for e, n in self.formula.items())


def _sp(key, formula, phase, thermo, h_add=0.0, label=""):
    s = Species(key, formula, phase, thermo, h_add, label or key)
    object.__setattr__(s, "molar_mass", s.mm() / 1000.0)  # kg/mol
    return s


def _slag(key, formula, thermo, label):
    tmp = _sp(key, formula, "slag", thermo)
    return _sp(key, formula, "slag", thermo, SLAG_FUSION_J_PER_G * tmp.molar_mass * 1000.0, label)


SPECIES_LIST: List[Species] = [
    # ---- gas phase (quasi-steady counter-current gas) ----
    _sp("CO", {"C": 1, "O": 1}, "gas", "CO"),
    _sp("CO2", {"C": 1, "O": 2}, "gas", "CO2"),
    _sp("H2", {"H": 2}, "gas", "H2"),
    _sp("H2O", {"H": 2, "O": 1}, "gas", "H2O"),
    _sp("N2", {"N": 2}, "gas", "N2"),
    _sp("O2", {"O": 2}, "gas", "O2"),
    _sp("CH4", {"C": 1, "H": 4}, "gas", "CH4"),
    _sp("COS", {"C": 1, "O": 1, "S": 1}, "gas", "COS"),
    _sp("SiO", {"Si": 1, "O": 1}, "gas", "SiO"),
    _sp("Zn", {"Zn": 1}, "gas", "Zn_g", label="Zn(g)"),
    _sp("K", {"K": 1}, "gas", "K_g", label="K(g)"),
    _sp("Na", {"Na": 1}, "gas", "Na_g", label="Na(g)"),
    _sp("Char", {"C": 1}, "gas", "C", label="unburnt PCI char (gas-borne)"),
    # ---- ore-carrier solids (sinter, pellets, lump, HBI/DRI, fluxes) ----
    _sp("Fe2O3", {"Fe": 2, "O": 3}, "ore", "Fe2O3"),
    _sp("Fe3O4", {"Fe": 3, "O": 4}, "ore", "Fe3O4"),
    _sp("FeO", {"Fe": 1, "O": 1}, "ore", "FeO"),
    _sp("Fe", {"Fe": 1}, "ore", "Fe", label="metallic Fe"),
    _sp("C_ore", {"C": 1}, "ore", "C", label="C in HBI/DRI / deposited C"),
    _sp("CaCO3", {"Ca": 1, "C": 1, "O": 3}, "ore", "CaCO3"),
    _sp("MgCO3", {"Mg": 1, "C": 1, "O": 3}, "ore", "MgCO3"),
    _sp("CaO", {"Ca": 1, "O": 1}, "ore", "CaO"),
    _sp("MgO", {"Mg": 1, "O": 1}, "ore", "MgO"),
    _sp("SiO2", {"Si": 1, "O": 2}, "ore", "SiO2"),
    _sp("Al2O3", {"Al": 2, "O": 3}, "ore", "Al2O3"),
    _sp("MnO", {"Mn": 1, "O": 1}, "ore", "MnO"),
    _sp("P2O5", {"P": 2, "O": 5}, "ore", "P2O5"),
    _sp("FeS", {"Fe": 1, "S": 1}, "ore", "FeS"),
    _sp("H2O_l", {"H": 2, "O": 1}, "ore", "H2O_liq", label="free moisture"),
    _sp("H2O_b", {"H": 2, "O": 1}, "ore", "H2O_bound", label="combined (LOI) water"),
    _sp("K2O", {"K": 2, "O": 1}, "ore", "K2O"),
    _sp("Na2O", {"Na": 2, "O": 1}, "ore", "Na2O"),
    _sp("ZnO", {"Zn": 1, "O": 1}, "ore", "ZnO"),
    # ---- coke-carrier solids ----
    _sp("C_k", {"C": 1}, "coke", "C", label="coke fixed carbon"),
    _sp("SiO2_k", {"Si": 1, "O": 2}, "coke", "SiO2", label="coke ash SiO2"),
    _sp("Al2O3_k", {"Al": 2, "O": 3}, "coke", "Al2O3", label="coke ash Al2O3"),
    _sp("CaO_k", {"Ca": 1, "O": 1}, "coke", "CaO", label="coke ash CaO"),
    _sp("MgO_k", {"Mg": 1, "O": 1}, "coke", "MgO", label="coke ash MgO"),
    _sp("Fe2O3_k", {"Fe": 2, "O": 3}, "coke", "Fe2O3", label="coke ash Fe2O3"),
    _sp("P2O5_k", {"P": 2, "O": 5}, "coke", "P2O5", label="coke ash P2O5"),
    _sp("K2O_k", {"K": 2, "O": 1}, "coke", "K2O", label="coke ash K2O"),
    _sp("Na2O_k", {"Na": 2, "O": 1}, "coke", "Na2O", label="coke ash Na2O"),
    _sp("S_k", {"S": 1}, "coke", "S", label="coke sulfur"),
    _sp("H2_k", {"H": 2}, "coke", "H2", label="coke volatile H"),
    _sp("N2_k", {"N": 2}, "coke", "N2", label="coke volatile N"),
    _sp("CO_k", {"C": 1, "O": 1}, "coke", "CO", label="coke volatile O (as CO)"),
    _sp("H2O_k", {"H": 2, "O": 1}, "coke", "H2O_liq", label="coke moisture"),
    # ---- hot metal (liquid) ----
    _sp("Fe_l", {"Fe": 1}, "hm", "Fe", h_add=13800.0, label="Fe (liquid)"),
    _sp("C_hm", {"C": 1}, "hm", "C_dis", label="[C]"),
    _sp("Si_hm", {"Si": 1}, "hm", "Si_dis", label="[Si]"),
    _sp("Mn_hm", {"Mn": 1}, "hm", "Mn_dis", label="[Mn]"),
    _sp("P_hm", {"P": 1}, "hm", "P_dis", label="[P]"),
    _sp("S_hm", {"S": 1}, "hm", "S_dis", label="[S]"),
    # ---- slag (liquid) ----
    _slag("CaO_sl", {"Ca": 1, "O": 1}, "CaO", "(CaO)"),
    _slag("SiO2_sl", {"Si": 1, "O": 2}, "SiO2", "(SiO2)"),
    _slag("Al2O3_sl", {"Al": 2, "O": 3}, "Al2O3", "(Al2O3)"),
    _slag("MgO_sl", {"Mg": 1, "O": 1}, "MgO", "(MgO)"),
    _slag("FeO_sl", {"Fe": 1, "O": 1}, "FeO", "(FeO)"),
    _slag("MnO_sl", {"Mn": 1, "O": 1}, "MnO", "(MnO)"),
    _slag("P2O5_sl", {"P": 2, "O": 5}, "P2O5", "(P2O5)"),
    _slag("CaS_sl", {"Ca": 1, "S": 1}, "CaS", "(CaS)"),
    _slag("K2O_sl", {"K": 2, "O": 1}, "K2O", "(K2O)"),
    _slag("Na2O_sl", {"Na": 2, "O": 1}, "Na2O", "(Na2O)"),
]

SPECIES: Dict[str, Species] = {s.key: s for s in SPECIES_LIST}

GAS = [s.key for s in SPECIES_LIST if s.phase == "gas"]
TRUE_GAS = [k for k in GAS if k != "Char"]  # Char is entrained solid, excluded from mole fractions
ORE = [s.key for s in SPECIES_LIST if s.phase == "ore"]
COKE = [s.key for s in SPECIES_LIST if s.phase == "coke"]
SOLID = ORE + COKE
HM = [s.key for s in SPECIES_LIST if s.phase == "hm"]
SLAG = [s.key for s in SPECIES_LIST if s.phase == "slag"]
LIQ = HM + SLAG

IDX_GAS = {k: i for i, k in enumerate(GAS)}
IDX_SOLID = {k: i for i, k in enumerate(SOLID)}
IDX_LIQ = {k: i for i, k in enumerate(LIQ)}
ORE_MASK = np.array([SPECIES[k].phase == "ore" for k in SOLID])
COKE_MASK = ~ORE_MASK
TRUE_GAS_MASK = np.array([k != "Char" for k in GAS])
HM_MASK = np.array([SPECIES[k].phase == "hm" for k in LIQ])
SLAG_MASK = ~HM_MASK

# species that melt in the cohesive zone (all ore-carrier species) and their liquid targets
MELT_MAP: Dict[str, Tuple[str, float]] = {
    "Fe": ("Fe_l", 1.0), "FeO": ("FeO_sl", 1.0), "CaO": ("CaO_sl", 1.0), "MgO": ("MgO_sl", 1.0),
    "SiO2": ("SiO2_sl", 1.0), "Al2O3": ("Al2O3_sl", 1.0), "MnO": ("MnO_sl", 1.0),
    "P2O5": ("P2O5_sl", 1.0), "K2O": ("K2O_sl", 1.0), "Na2O": ("Na2O_sl", 1.0), "C_ore": ("C_hm", 1.0),
}


def molar_masses(keys: List[str]) -> np.ndarray:
    return np.array([SPECIES[k].molar_mass for k in keys])


def element_matrix(keys: List[str]) -> np.ndarray:
    """Matrix E[e, k] = atoms of element e in species k."""
    E = np.zeros((len(ELEMENTS), len(keys)))
    for j, k in enumerate(keys):
        for el, n in SPECIES[k].formula.items():
            E[ELEMENTS.index(el), j] = n
    return E


class ThermoTable:
    """Vectorised Cp/H/S evaluation for an ordered list of species keys."""

    def __init__(self, keys: List[str]):
        self.keys = list(keys)
        rec = [THERMO[SPECIES[k].thermo] if k in SPECIES else THERMO[k] for k in self.keys]
        h_add = [SPECIES[k].h_add if k in SPECIES else 0.0 for k in self.keys]
        self.dHf = np.array([r.dHf for r in rec]) + np.array(h_add)
        self.S298 = np.array([r.S298 for r in rec])
        self.a = np.array([r.a for r in rec])
        self.b = np.array([r.b for r in rec]) * 1e-3
        self.c = np.array([r.c for r in rec]) * 1e5
        self.d = np.array([r.d for r in rec]) * 1e-6
        self.mm = np.array([SPECIES[k].molar_mass if k in SPECIES else 0.0 for k in self.keys])

    def cp(self, T):
        T = np.asarray(T, dtype=float)[..., None]
        return self.a + self.b * T + self.c / T**2 + self.d * T**2

    def h(self, T):
        """Absolute molar enthalpy [J/mol], shape T.shape + (n_species,)."""
        T = np.asarray(T, dtype=float)[..., None]
        return (self.dHf + self.a * (T - T0) + 0.5 * self.b * (T**2 - T0**2)
                - self.c * (1.0 / T - 1.0 / T0) + self.d / 3.0 * (T**3 - T0**3))

    def s(self, T):
        T = np.asarray(T, dtype=float)[..., None]
        return (self.S298 + self.a * np.log(T / T0) + self.b * (T - T0)
                - 0.5 * self.c * (1.0 / T**2 - 1.0 / T0**2) + 0.5 * self.d * (T**2 - T0**2))

    def H_mix(self, n, T):
        """Total enthalpy of mixture n[..., k] at temperature T[...] [J]."""
        return np.sum(n * self.h(T), axis=-1)

    def Cp_mix(self, n, T):
        return np.sum(n * self.cp(T), axis=-1)

    def T_from_H(self, n, H, T_guess, tol=1e-6, max_iter=60, t_min=250.0, t_max=3600.0):
        """Invert H(T) = sum_k n_k h_k(T) for T (Newton with bisection safeguard)."""
        n = np.asarray(n, dtype=float)
        H = np.asarray(H, dtype=float)
        T = np.clip(np.asarray(T_guess, dtype=float).copy() * np.ones(H.shape), t_min, t_max)
        lo = np.full(H.shape, t_min)
        hi = np.full(H.shape, t_max)
        ntot = np.sum(n, axis=-1)
        empty = ntot <= 1e-12
        for _ in range(max_iter):
            f = self.H_mix(n, T) - H
            dfdT = np.maximum(self.Cp_mix(n, T), 1e-9)
            lo = np.where(f < 0, T, lo)
            hi = np.where(f > 0, T, hi)
            T_new = T - f / dfdT
            bad = (T_new <= lo) | (T_new >= hi)
            T_new = np.where(bad, 0.5 * (lo + hi), T_new)
            if np.all(np.abs(T_new - T) < tol * np.maximum(1.0, T)) :
                T = T_new
                break
            T = T_new
        return np.where(empty, np.asarray(T_guess) * np.ones(H.shape), T)


TT_GAS = ThermoTable(GAS)
TT_SOLID = ThermoTable(SOLID)
TT_LIQ = ThermoTable(LIQ)
TT_HM = ThermoTable(HM)
TT_SLAG = ThermoTable(SLAG)
E_GAS = element_matrix(GAS)
E_SOLID = element_matrix(SOLID)
E_LIQ = element_matrix(LIQ)
MM_GAS = TT_GAS.mm
MM_SOLID = TT_SOLID.mm
MM_LIQ = TT_LIQ.mm


def species_table() -> List[dict]:
    """Human-readable species/thermo table for the API and documentation."""
    rows = []
    for s in SPECIES_LIST:
        th = THERMO[s.thermo]
        rows.append({
            "key": s.key, "label": s.label, "phase": s.phase, "formula": s.formula,
            "molar_mass_g_per_mol": round(s.molar_mass * 1000, 4),
            "dHf298_kJ_per_mol": round((th.dHf + s.h_add) / 1000, 3), "S298_J_per_molK": th.S298,
            "cp_coeffs_abcd": [th.a, th.b, th.c, th.d], "source": th.source, "provisional": th.provisional,
        })
    return rows
