"""Burden materials, charge composition, bell-less-top radial distribution and layer tracking.

Material chemistry (wt%, dry basis unless stated) is converted to model species with exact
mass bookkeeping. Analyses must close to 100 +/- 2.5 wt% (validated); the modelled species are
then normalised so that the charged mass equals the weighed mass.
"""
from __future__ import annotations

import math
from typing import Dict, List, Tuple

import numpy as np

from .species import SPECIES, SOLID, IDX_SOLID, ATOMIC_MASS

M = {k: SPECIES[k].molar_mass * 1000.0 for k in SPECIES}  # g/mol
CLOSURE_TOL = 2.5


class ChemistryError(ValueError):
    pass


def ore_species_per_kg(chem: Dict[str, float], moisture_pct: float, kind: str) -> Tuple[np.ndarray, float]:
    """Species moles per kg *wet* ore-type material and the dry analysis closure [wt%]."""
    c = {k: float(v) for k, v in chem.items()}
    n = {}
    fe_tot = c.get("Fe_total", 0.0)
    fe_met = c.get("Fe_met", 0.0)
    feo = c.get("FeO", 0.0)
    s = c.get("S", 0.0)
    fe_in_feo = feo * ATOMIC_MASS["Fe"] / M["FeO"]
    fe_in_fes = s / ATOMIC_MASS["S"] * ATOMIC_MASS["Fe"]
    fe3o4_frac = c.get("Fe3O4_share", 0.0)  # share of remaining oxide Fe present as magnetite
    fe_ox = fe_tot - fe_met - fe_in_feo - fe_in_fes
    if fe_ox < -0.3:
        raise ChemistryError(f"Inconsistent iron analysis: Fe_total {fe_tot} < Fe_met + Fe(FeO) + Fe(FeS)")
    fe_ox = max(fe_ox, 0.0)
    mol_fe_ox = fe_ox / ATOMIC_MASS["Fe"]
    n["Fe"] = fe_met / ATOMIC_MASS["Fe"]
    n["FeO"] = feo / M["FeO"]
    n["Fe3O4"] = mol_fe_ox * fe3o4_frac / 3.0
    n["Fe2O3"] = mol_fe_ox * (1.0 - fe3o4_frac) / 2.0
    n["FeS"] = s / ATOMIC_MASS["S"]
    for ox in ("SiO2", "Al2O3", "MnO", "K2O", "Na2O", "ZnO"):
        n[ox] = c.get(ox, 0.0) / M[ox]
    n["P2O5"] = c.get("P", 0.0) / ATOMIC_MASS["P"] / 2.0
    n["C_ore"] = c.get("C", 0.0) / ATOMIC_MASS["C"]
    cao = c.get("CaO", 0.0) / M["CaO"]
    mgo = c.get("MgO", 0.0) / M["MgO"]
    co2 = c.get("CO2", 0.0) / (ATOMIC_MASS["C"] + 2 * ATOMIC_MASS["O"])  # carbonate CO2
    n_caco3 = min(cao, co2)
    n_mgco3 = min(mgo, max(co2 - n_caco3, 0.0))
    n["CaCO3"], n["CaO"] = n_caco3, cao - n_caco3
    n["MgCO3"], n["MgO"] = n_mgco3, mgo - n_mgco3
    n["H2O_b"] = c.get("LOI_H2O", 0.0) / M["H2O_b"]
    # closure (dry basis): masses of modelled species in 100 g dry
    mass = sum(n[k] * M[k] for k in n)
    closure = mass  # grams per 100 g dry
    if abs(closure - 100.0) > CLOSURE_TOL:
        raise ChemistryError(f"Dry analysis closes to {closure:.2f} wt% (allowed 100 +/- {CLOSURE_TOL}).")
    scale = 100.0 / closure
    vec = np.zeros(len(SOLID))
    dry_frac = 1.0 - moisture_pct / 100.0
    for k, v in n.items():
        vec[IDX_SOLID[k]] = v * scale * 10.0 * dry_frac  # per kg wet: (mol per 100 g dry)*10 g->kg
    vec[IDX_SOLID["H2O_l"]] = moisture_pct * 10.0 / M["H2O_l"]
    return vec, closure


def coke_species_per_kg(coke: Dict[str, float]) -> Tuple[np.ndarray, float]:
    """Coke: ultimate analysis (dry) C,H,N,S,O + ash (with ash chemistry) + moisture (wet basis)."""
    c = coke
    ash = c["ash_pct"]
    ult = {"C": c["C_pct"], "H": c.get("H_pct", 0.3), "N": c.get("N_pct", 1.0), "S": c.get("S_pct", 0.6), "O": c.get("O_pct", 0.5)}
    closure = sum(ult.values()) + ash
    if abs(closure - 100.0) > CLOSURE_TOL:
        raise ChemistryError(f"Coke dry analysis closes to {closure:.2f} wt%.")
    scale = 100.0 / closure
    ash_chem = c.get("ash_chem", {"SiO2": 52.0, "Al2O3": 28.0, "Fe2O3": 8.0, "CaO": 4.0, "MgO": 1.5, "P2O5": 1.0, "K2O": 1.5, "Na2O": 0.5})
    a_sum = sum(ash_chem.values())
    n = {}
    n_O = ult["O"] / ATOMIC_MASS["O"]
    n["CO_k"] = n_O
    n["C_k"] = ult["C"] / ATOMIC_MASS["C"] - n_O
    n["H2_k"] = ult["H"] / (2 * ATOMIC_MASS["H"])
    n["N2_k"] = ult["N"] / (2 * ATOMIC_MASS["N"])
    n["S_k"] = ult["S"] / ATOMIC_MASS["S"]
    for ox, key in (("SiO2", "SiO2_k"), ("Al2O3", "Al2O3_k"), ("Fe2O3", "Fe2O3_k"), ("CaO", "CaO_k"),
                    ("MgO", "MgO_k"), ("P2O5", "P2O5_k"), ("K2O", "K2O_k"), ("Na2O", "Na2O_k")):
        n[key] = ash * ash_chem.get(ox, 0.0) / a_sum / M[key]
    moist = c.get("moisture_pct", 4.0)
    dry_frac = 1.0 - moist / 100.0
    vec = np.zeros(len(SOLID))
    for k, v in n.items():
        vec[IDX_SOLID[k]] = v * scale * 10.0 * dry_frac
    vec[IDX_SOLID["H2O_k"]] = moist * 10.0 / M["H2O_k"]
    return vec, closure


def pci_elements_per_kg(pci: Dict[str, float]) -> Dict[str, float]:
    """PCI coal (as injected): mol of elements per kg + ash oxide moles + moisture + enthalpy data."""
    moist = pci.get("moisture_pct", 1.0)
    dry = 1.0 - moist / 100.0
    ult = {"C": pci["C_pct"], "H": pci["H_pct"], "O": pci["O_pct"], "N": pci["N_pct"], "S": pci["S_pct"]}
    ash = pci["ash_pct"]
    closure = sum(ult.values()) + ash
    if abs(closure - 100.0) > CLOSURE_TOL:
        raise ChemistryError(f"PCI dry analysis closes to {closure:.2f} wt%.")
    sc = 100.0 / closure
    out = {el: ult[el] * sc * 10.0 * dry / ATOMIC_MASS[el] for el in ult}
    ash_chem = pci.get("ash_chem", {"SiO2": 55.0, "Al2O3": 28.0, "Fe2O3": 7.0, "CaO": 5.0, "MgO": 1.5, "P2O5": 0.5, "K2O": 2.0, "Na2O": 1.0})
    a_sum = sum(ash_chem.values())
    for ox in ash_chem:
        key = ox + "_k"
        out["ash_" + ox] = ash * sc * 10.0 * dry * ash_chem[ox] / a_sum / M[key]
    out["H2O"] = moist * 10.0 / M["H2O_l"]
    out["closure"] = closure
    return out


# ------------------------------------------------------------------------------------------
# bell-less top: chute trajectories and radial distribution
# ------------------------------------------------------------------------------------------

def landing_radius(angle_deg: float, chute_len: float, fall_h: float, mu: float = 0.45, v_in: float = 2.5,
                   r_max: float = 1e9) -> float:
    a = math.radians(angle_deg)
    v2 = v_in**2 + 2 * 9.81 * chute_len * max(math.cos(a) - mu * math.sin(a), 0.05)
    v = math.sqrt(v2)
    r0 = chute_len * math.sin(a)
    vr, vz = v * math.sin(a), v * math.cos(a)
    t = (-vz + math.sqrt(vz**2 + 2 * 9.81 * fall_h)) / 9.81
    return min(r0 + vr * t, r_max)


def radial_distribution(matrix: List[dict], R_throat: float, chute_len: float, fall_h: float,
                        sector_edges: np.ndarray, n_points: int = 24) -> dict:
    """Distribute ore and coke volumes over radial sectors from the charging matrix.

    matrix rows: {"angle_deg": a, "coke_rot": nc, "ore_rot": no}. Rotations are proportional
    to the volume deposited at that ring. Each ring deposits a Gaussian band (sigma = 0.07 R).
    Returns sector fractions per material and the radial ore/(ore+coke) volume profile.
    """
    rr = np.linspace(0.0, R_throat, n_points + 1)
    rc = 0.5 * (rr[1:] + rr[:-1])
    ann = np.pi * (rr[1:] ** 2 - rr[:-1] ** 2)
    dep = {"coke": np.zeros(n_points), "ore": np.zeros(n_points)}
    rings = []
    sig = 0.07 * R_throat
    for row in matrix:
        for mat, key, mu in (("coke", "coke_rot", 0.50), ("ore", "ore_rot", 0.40)):
            nrot = float(row.get(key, 0.0))
            if nrot <= 0:
                continue
            rl = landing_radius(row["angle_deg"], chute_len, fall_h, mu=mu, r_max=R_throat)
            w = np.exp(-0.5 * ((rc - rl) / sig) ** 2) * rc  # volume weight ~ circumference
            w = w / max(w.sum(), 1e-12) * nrot
            dep[mat] += w
            rings.append({"material": mat, "angle_deg": row["angle_deg"], "rotations": nrot, "landing_r_m": round(rl, 3)})
    out = {"rings": rings, "r_m": rc.round(3).tolist()}
    sec_frac = {}
    for mat in ("coke", "ore"):
        tot = max(dep[mat].sum(), 1e-12)
        thick = dep[mat] / ann
        out[f"{mat}_relative_thickness"] = (thick / max(thick.mean(), 1e-12)).round(4).tolist()
        fr = []
        for i in range(len(sector_edges) - 1):
            m = (rc / R_throat >= sector_edges[i]) & (rc / R_throat < sector_edges[i + 1] + (1e-9 if i == len(sector_edges) - 2 else 0))
            fr.append(float(dep[mat][m].sum() / tot))
        sec_frac[mat] = np.array(fr)
    t_o = dep["ore"] / ann
    t_c = dep["coke"] / ann
    out["ore_fraction_profile"] = (t_o / np.maximum(t_o + t_c, 1e-12)).round(4).tolist()
    out["sector_fraction"] = {k: v.round(4).tolist() for k, v in sec_frac.items()}
    return out, sec_frac


class LayerTracker:
    """Kinematic tracking of charged ore/coke layer interfaces for visualisation.

    Interfaces are advected with the backend-computed local solid descent velocity; they are
    NOT used by the conservation equations (which are Eulerian).
    """

    def __init__(self, H: float, max_layers: int = 200):
        self.H = H
        self.layers: List[dict] = []   # {"z_top", "z_bot", "kind", "charge_no"}
        self.max_layers = max_layers

    def add_charge(self, charge_no: int, coke_thick: float, ore_thick: float):
        # coke dumped first, ore on top (C-O sequence), placed on the current burden surface
        z0 = self.layers[0]["z_top"] if self.layers else self.H - coke_thick - ore_thick
        self.layers.insert(0, {"z_top": z0 + coke_thick, "z_bot": z0, "kind": "coke", "charge_no": charge_no})
        self.layers.insert(0, {"z_top": z0 + coke_thick + ore_thick, "z_bot": z0 + coke_thick, "kind": "ore",
                               "charge_no": charge_no})
        # the charging logic holds the burden surface at the stockline: re-anchor the column so that
        # accumulated advection error never lets the visual layer stack climb above the stockline
        over = self.layers[0]["z_top"] - self.H
        if over > 0:
            for lyr in self.layers:
                lyr["z_top"] -= over
                lyr["z_bot"] -= over
        self.layers = [l for l in self.layers if l["z_top"] > 0.0][: self.max_layers]

    def seed(self, coke_thick: float, ore_thick: float):
        """Fill the column with alternating layers (initial condition)."""
        self.layers = []
        z, i = 0.0, 0
        while z < self.H:
            self.layers.insert(0, {"z_top": z + coke_thick, "z_bot": z, "kind": "coke", "charge_no": -i})
            self.layers.insert(0, {"z_top": z + coke_thick + ore_thick, "z_bot": z + coke_thick, "kind": "ore", "charge_no": -i})
            z += coke_thick + ore_thick
            i += 1

    def advect(self, z_faces: np.ndarray, u_s_faces: np.ndarray, dt: float, z_cz_bottom: float):
        """u_s_faces: descent speed (m/s, positive downward) at cell faces."""
        keep = []
        for lyr in self.layers:
            for key in ("z_top", "z_bot"):
                z = lyr[key]
                u = float(np.interp(z, z_faces, u_s_faces))
                lyr[key] = z - u * dt
            if lyr["kind"] == "ore" and lyr["z_top"] < z_cz_bottom:
                continue
            if lyr["z_top"] < 0.0:
                continue
            keep.append(lyr)
        self.layers = keep

    def payload(self) -> List[dict]:
        return [{"z_top": round(l["z_top"], 3), "z_bot": round(max(l["z_bot"], 0.0), 3), "kind": l["kind"],
                 "charge_no": l["charge_no"]} for l in self.layers]
