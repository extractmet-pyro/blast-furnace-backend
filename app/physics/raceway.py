"""Tuyere/raceway model: blast, oxygen, moisture, PCI, natural gas and hydrogen injection.

Raceway net chemistry (oxygen-starved, coke excess): every oxygen atom entering the raceway
leaves as CO (or COS / SiO), all hydrogen as H2, all nitrogen as N2. Carbon is supplied by
burnt PCI (burnout eta), injected CH4 and finally by coke drawn from the deadman inventory
(the coke 'parcel' carries its ash, sulfur and residual volatiles in proportion).

PCI enthalpy is obtained from its higher heating value (HHV method), so devolatilisation /
decomposition heat is included automatically. The raceway gas temperature follows from an
enthalpy balance; tuyere cooling losses are deducted. RAFT is reported with the conventional
definition (coke entering at 1500 degC, no SiO formation, no tuyere loss) and is an ESTIMATE,
never a measurement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np

from .species import (GAS, LIQ, SOLID, IDX_GAS, IDX_LIQ, IDX_SOLID, TT_GAS, TT_LIQ, TT_SOLID, MOL_PER_NM3,
                      THERMO, ELEMENTS, ATOMIC_MASS, E_GAS, E_LIQ, E_SOLID, R_GAS, ThermoTable)
from .burden import pci_elements_per_kg
from .heat_transfer import tuyere_loss

_ASH_OX = ["SiO2", "Al2O3", "Fe2O3", "CaO", "MgO", "P2O5", "K2O", "Na2O"]
_TT_ASH = ThermoTable([o + "_k" for o in _ASH_OX])
_TT_H2OL = ThermoTable(["H2O_l"])


@dataclass
class ExternalInputs:
    """External (boundary) streams per second, with enthalpy and element content."""
    gas: np.ndarray                  # mol/s of blast-side gas species (O2, N2, H2O, CH4, H2)
    T_blast: float
    T_inj: float
    pci_kg_s: float
    pci: Dict[str, float]            # elements per kg (as injected)
    pci_H_J_per_kg: float            # absolute enthalpy per kg at injection temperature
    carrier_N2: float                # mol/s
    H_total: float                   # W, enthalpy of all external raceway inputs
    elements: np.ndarray             # mol/s per ELEMENTS
    blast_dry_Nm3_s: float
    o2_frac: float


def external_inputs(op: dict, cfg: dict) -> ExternalInputs:
    V = op["blast_volume_Nm3_min"] / 60.0 if op.get("blast_on", True) else 0.0
    y = 0.2095 + op["o2_enrichment_pct"] / 100.0
    pur = op.get("o2_purity_pct", 99.5) / 100.0
    V_ox = max(V * (y - 0.2095) / max(pur - 0.2095, 1e-6), 0.0)
    V_air = max(V - V_ox, 0.0)
    gas = np.zeros(len(GAS))
    gas[IDX_GAS["O2"]] = (0.2095 * V_air + pur * V_ox) * MOL_PER_NM3
    gas[IDX_GAS["N2"]] = (0.7905 * V_air + (1 - pur) * V_ox) * MOL_PER_NM3
    gas[IDX_GAS["H2O"]] = op["blast_moisture_g_Nm3"] * V / 18.015
    T_b = op["blast_temp_C"] + 273.15
    T_inj = op.get("pci_inject_temp_C", 60.0) + 273.15
    gas[IDX_GAS["CH4"]] = op.get("ng_Nm3_h", 0.0) / 3600.0 * MOL_PER_NM3 * cfg.get("natural_gas", {}).get("CH4_pct", 100.0) / 100.0
    gas[IDX_GAS["H2"]] = op.get("h2_Nm3_h", 0.0) / 3600.0 * MOL_PER_NM3
    pci_kg_s = op.get("pci_t_h", 0.0) * 1000.0 / 3600.0 if op.get("blast_on", True) else 0.0
    pci = pci_elements_per_kg(cfg["pci"])
    carrier = pci_kg_s * cfg["pci"].get("carrier_N2_Nm3_per_kg", 0.03) * MOL_PER_NM3
    # enthalpies
    h_blast = TT_GAS.h(T_b)
    H = float(gas[[IDX_GAS["O2"], IDX_GAS["N2"], IDX_GAS["H2O"]]] @ h_blast[[IDX_GAS["O2"], IDX_GAS["N2"], IDX_GAS["H2O"]]])
    h_inj = TT_GAS.h(T_inj)
    H += float(gas[IDX_GAS["CH4"]] * h_inj[IDX_GAS["CH4"]] + gas[IDX_GAS["H2"]] * h_inj[IDX_GAS["H2"]])
    H += carrier * float(h_inj[IDX_GAS["N2"]])
    hpci = pci_enthalpy_per_kg(cfg["pci"], pci, T_inj)
    H += pci_kg_s * hpci
    el = E_GAS @ gas
    el[ELEMENTS.index("N")] += 2 * carrier
    for e in ("C", "H", "O", "N", "S"):
        el[ELEMENTS.index(e)] += pci_kg_s * pci[e]
    el[ELEMENTS.index("H")] += pci_kg_s * 2 * pci["H2O"]
    el[ELEMENTS.index("O")] += pci_kg_s * pci["H2O"]
    for ox in _ASH_OX:
        k = ox + "_k"
        col = E_SOLID[:, IDX_SOLID[k]]
        el += col * pci_kg_s * pci["ash_" + ox]
    return ExternalInputs(gas, T_b, T_inj, pci_kg_s, pci, hpci, carrier, H, el, V, y)


def pci_enthalpy_per_kg(pci_cfg: dict, pci: dict, T: float) -> float:
    """Absolute enthalpy of 1 kg injected coal at T (HHV method, dry HHV, + moisture)."""
    dry = 1.0 - pci_cfg.get("moisture_pct", 1.0) / 100.0
    h_prod = (pci["C"] * THERMO["CO2"].dHf + pci["H"] / 2.0 * THERMO["H2O_liq"].dHf
              + pci["S"] * (-296830.0))
    ash_h = sum(pci["ash_" + ox] * _TT_ASH.h(298.15)[i] for i, ox in enumerate(_ASH_OX))
    h298 = h_prod + ash_h + pci_cfg["HHV_MJ_kg"] * 1e6 * dry
    h298 += pci["H2O"] * float(_TT_H2OL.h(298.15)[0])
    cp = 1300.0  # J/(kg K), coal + moisture, approximate
    return float(h298 + cp * (T - 298.15))


@dataclass
class RacewayResult:
    gas: np.ndarray
    T_gas: float
    T_ad: float
    raft_std_C: float
    coke_parcel: np.ndarray
    liquids: np.ndarray
    H_liq: float
    H_gas: float
    Q_tuyere: float
    pci_burnout: float
    sio_frac: float
    diag: Dict[str, float] = field(default_factory=dict)


def _solve_T(n_gas, n_liq, H_target, T_guess=2300.0):
    """Find T such that H_gas(T)+H_liq(T) = H_target."""
    T = T_guess
    for _ in range(50):
        f = float(n_gas @ TT_GAS.h(T) + n_liq @ TT_LIQ.h(T)) - H_target
        d = float(n_gas @ TT_GAS.cp(T) + n_liq @ TT_LIQ.cp(T))
        dT = -f / max(d, 1e-6)
        T = float(np.clip(T + dT, 600.0, 3500.0))
        if abs(dT) < 1e-6:
            break
    return T


def raceway(ext: ExternalInputs, dm_n: np.ndarray, T_coke: float, cfg: dict, n_active_tuyeres: int,
            P_blast_Pa: float) -> RacewayResult:
    cal = cfg["calibration"]
    g = ext.gas
    O2 = g[IDX_GAS["O2"]]
    H2O = g[IDX_GAS["H2O"]]
    CH4 = g[IDX_GAS["CH4"]]
    pci = ext.pci
    m = ext.pci_kg_s
    C_pci = m * pci["C"]
    lam = O2 / max(C_pci, 1e-9)
    eta = cal["pci_burnout_eta_max"] * (1.0 - np.exp(-cal["pci_burnout_k"] * lam)) if C_pci > 0 else 1.0
    nC = max(dm_n[IDX_SOLID["C_k"]], 1e-12)
    c = dm_n / nC  # coke parcel ratios per mol C_k (coke-carrier species only)
    from .species import COKE_MASK
    c = np.where(COKE_MASK, c, 0.0)
    ash_pci = {ox: m * pci["ash_" + ox] for ox in _ASH_OX}

    def evaluate(f_sio):
        O_fixed = 2 * O2 + H2O + m * pci["O"] + m * pci["H2O"] + 3 * ash_pci["Fe2O3"] + f_sio * ash_pci["SiO2"]
        dO_dx = c[IDX_SOLID["H2O_k"]] + 3 * c[IDX_SOLID["Fe2O3_k"]] + f_sio * c[IDX_SOLID["SiO2_k"]]
        C_sup = eta * C_pci + CH4
        x = (O_fixed - C_sup) / max(1.0 - dO_dx, 1e-6)
        deficit = 0.0
        if x < 0:
            deficit = -x
            x = 0.0
        out = np.zeros(len(GAS))
        n_cos = x * c[IDX_SOLID["S_k"]] + m * pci["S"]
        O_pool = O_fixed + x * dO_dx
        out[IDX_GAS["COS"]] = n_cos
        # every O atom of the pool binds one C as CO or COS (the second O of volatilised SiO2
        # stays in SiO); coke volatile O leaves as CO with its own carbon
        out[IDX_GAS["CO"]] = O_pool - n_cos + x * c[IDX_SOLID["CO_k"]]
        out[IDX_GAS["SiO"]] = f_sio * (ash_pci["SiO2"] + x * c[IDX_SOLID["SiO2_k"]])
        out[IDX_GAS["H2"]] = H2O + 2 * CH4 + g[IDX_GAS["H2"]] + m * pci["H"] / 2 + m * pci["H2O"] + x * (c[IDX_SOLID["H2_k"]] + c[IDX_SOLID["H2O_k"]])
        out[IDX_GAS["N2"]] = g[IDX_GAS["N2"]] + ext.carrier_N2 + m * pci["N"] / 2 + x * c[IDX_SOLID["N2_k"]]
        out[IDX_GAS["Char"]] = (1 - eta) * C_pci + deficit
        liq = np.zeros(len(LIQ))
        liq[IDX_LIQ["Fe_l"]] = 2 * (x * c[IDX_SOLID["Fe2O3_k"]] + ash_pci["Fe2O3"])
        for ox in ("CaO", "MgO", "Al2O3", "P2O5", "K2O", "Na2O"):
            liq[IDX_LIQ[ox + "_sl"]] = x * c[IDX_SOLID[ox + "_k"]] + ash_pci[ox]
        liq[IDX_LIQ["SiO2_sl"]] = (1 - f_sio) * (x * c[IDX_SOLID["SiO2_k"]] + ash_pci["SiO2"])
        parcel = x * c
        return out, liq, parcel, x, deficit

    # coke parcel enthalpy at actual deadman temperature
    h_sol = TT_SOLID.h(T_coke)
    f_sio = 0.0
    T_gas = 2300.0
    for _ in range(3):
        out, liq, parcel, x, deficit = evaluate(f_sio)
        H_in = ext.H_total + float(parcel @ h_sol)
        T_ad = _solve_T(out, liq, H_in, T_gas)
        Q_t = tuyere_loss(n_active_tuyeres, cal["tuyere_heat_kW"], T_ad)
        T_gas = _solve_T(out, liq, H_in - Q_t, T_ad)
        kin_T = T_gas
        f_new = float(np.clip(cal["sio_vol_k"] * np.exp(-260e3 / R_GAS * (1.0 / kin_T - 1.0 / 2373.0)), 0.0, 0.8))
        if abs(f_new - f_sio) < 1e-4:
            f_sio = f_new
            break
        f_sio = f_new
    out, liq, parcel, x, deficit = evaluate(f_sio)
    H_in = ext.H_total + float(parcel @ h_sol)
    T_ad = _solve_T(out, liq, H_in, T_gas)
    Q_t = tuyere_loss(n_active_tuyeres, cal["tuyere_heat_kW"], T_ad) if x > 0 or ext.blast_dry_Nm3_s > 0 else 0.0
    T_gas = _solve_T(out, liq, H_in - Q_t, T_ad)
    # conventional RAFT: coke at 1500 degC, no SiO, no tuyere loss
    out0, liq0, parcel0, _, _ = evaluate(0.0)
    raft = _solve_T(out0, liq0, ext.H_total + float(parcel0 @ TT_SOLID.h(1773.15)), T_ad) - 273.15
    H_gas = float(out @ TT_GAS.h(T_gas))
    H_liq = float(liq @ TT_LIQ.h(T_gas))
    # diagnostics: tuyere velocity, blast kinetic energy, raceway depth (provisional correlation)
    g_cfg = cfg["geometry"]
    n_t = max(n_active_tuyeres, 1)
    A_t = np.pi * g_cfg["tuyere_diameter_m"] ** 2 / 4.0
    n_blast = float(np.sum(ext.gas)) + ext.carrier_N2
    Q_act = n_blast * R_GAS * ext.T_blast / max(P_blast_Pa, 1e5)
    v_t = Q_act / (n_t * A_t)
    M_b = float((ext.gas @ (TT_GAS.mm)) + ext.carrier_N2 * 0.028014) / max(n_blast, 1e-9)
    rho_b = P_blast_Pa * M_b / (R_GAS * ext.T_blast)
    m_dot_t = rho_b * Q_act / n_t
    bke_kW = 0.5 * m_dot_t * v_t**2 / 1000.0
    ratio = rho_b * v_t**2 / (480.0 * 9.81 * g_cfg["tuyere_diameter_m"])
    depth = 0.55 * g_cfg["tuyere_diameter_m"] * max(ratio, 0.0) ** 0.6
    diag = {
        "bosh_gas_Nm3_min": float(np.sum(out[:-1]) / MOL_PER_NM3 * 60.0),
        "tuyere_velocity_m_s": float(v_t), "blast_kinetic_energy_kW": float(bke_kW), "raceway_depth_m": float(depth),
        "o2_to_pci_c_ratio": float(lam), "oxygen_deficient": bool(deficit > 0), "coke_C_mol_s": float(x),
        "blast_density_kg_m3": float(rho_b), "char_mol_s": float(out[IDX_GAS["Char"]]),
        "bosh_CO_pct": float(100 * out[IDX_GAS["CO"]] / max(np.sum(out[:-1]), 1e-9)),
        "bosh_H2_pct": float(100 * out[IDX_GAS["H2"]] / max(np.sum(out[:-1]), 1e-9)),
        "bosh_N2_pct": float(100 * out[IDX_GAS["N2"]] / max(np.sum(out[:-1]), 1e-9)),
    }
    return RacewayResult(out, T_gas, T_ad, raft, parcel, liq, H_liq, H_gas, Q_t, float(eta), f_sio, diag)
