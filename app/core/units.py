"""Unit-validation layer used at API boundaries (Pint-like, dependency-free).

Internally all computation is SI (K, Pa, mol, kg, J, W, s). Inputs must declare a unit from the
registry below; unknown units, non-finite numbers and dimension mismatches raise UnitError.
Conventions: Nm3 at 0 degC, 101.325 kPa (44.615 mol/Nm3); gauge pressure is explicit (kPa_g);
O2 enrichment = O2 vol% of dry blast above 20.95 %; humidity per Nm3 of DRY blast.
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

P_ATM = 101325.0
MOL_PER_NM3 = 101325.0 / (8.314462618 * 273.15)


class UnitError(ValueError):
    pass


# unit -> (dimension, factor, offset): SI = value * factor + offset
UNITS: Dict[str, Tuple[str, float, float]] = {
    "K": ("temperature", 1.0, 0.0), "degC": ("temperature", 1.0, 273.15), "degF": ("temperature", 5 / 9, 273.15 - 32 * 5 / 9),
    "Pa": ("pressure_abs", 1.0, 0.0), "kPa": ("pressure_abs", 1e3, 0.0), "bar": ("pressure_abs", 1e5, 0.0),
    "kPa_g": ("pressure_abs", 1e3, P_ATM), "bar_g": ("pressure_abs", 1e5, P_ATM), "kg_cm2_g": ("pressure_abs", 98066.5, P_ATM),
    "Pa_diff": ("pressure_diff", 1.0, 0.0), "kPa_diff": ("pressure_diff", 1e3, 0.0),
    "kg": ("mass", 1.0, 0.0), "t": ("mass", 1000.0, 0.0),
    "kg/s": ("mass_flow", 1.0, 0.0), "t/h": ("mass_flow", 1000.0 / 3600.0, 0.0), "kg/h": ("mass_flow", 1 / 3600.0, 0.0),
    "t/d": ("mass_flow", 1000.0 / 86400.0, 0.0),
    "mol/s": ("molar_flow", 1.0, 0.0), "Nm3/h": ("molar_flow", MOL_PER_NM3 / 3600.0, 0.0),
    "Nm3/min": ("molar_flow", MOL_PER_NM3 / 60.0, 0.0), "Nm3/s": ("molar_flow", MOL_PER_NM3, 0.0),
    "J": ("energy", 1.0, 0.0), "kJ": ("energy", 1e3, 0.0), "MJ": ("energy", 1e6, 0.0), "GJ": ("energy", 1e9, 0.0),
    "kWh": ("energy", 3.6e6, 0.0), "kcal": ("energy", 4184.0, 0.0),
    "W": ("power", 1.0, 0.0), "kW": ("power", 1e3, 0.0), "MW": ("power", 1e6, 0.0),
    "m": ("length", 1.0, 0.0), "mm": ("length", 1e-3, 0.0),
    "s": ("time", 1.0, 0.0), "min": ("time", 60.0, 0.0), "h": ("time", 3600.0, 0.0),
    "%": ("fraction", 0.01, 0.0), "wt%": ("fraction", 0.01, 0.0), "vol%": ("fraction", 0.01, 0.0), "-": ("fraction", 1.0, 0.0),
    "g/Nm3": ("humidity", 1.0, 0.0), "kg/tHM": ("specific_mass", 1.0, 0.0), "Nm3/tHM": ("specific_volume", 1.0, 0.0),
    "deg": ("angle", 1.0, 0.0),
}


def to_si(value: float, unit: str) -> float:
    if unit not in UNITS:
        raise UnitError(f"Unsupported unit '{unit}'")
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise UnitError("Value must be a finite number")
    _, f, o = UNITS[unit]
    return float(value) * f + o


def from_si(value: float, unit: str) -> float:
    if unit not in UNITS:
        raise UnitError(f"Unsupported unit '{unit}'")
    _, f, o = UNITS[unit]
    return (float(value) - o) / f


def convert(value: float, unit_from: str, unit_to: str) -> float:
    d1, d2 = UNITS.get(unit_from, (None,))[0], UNITS.get(unit_to, (None,))[0]
    if d1 is None or d2 is None:
        raise UnitError(f"Unsupported unit conversion {unit_from} -> {unit_to}")
    if d1 != d2:
        raise UnitError(f"Dimension mismatch: {unit_from} ({d1}) vs {unit_to} ({d2})")
    return from_si(to_si(value, unit_from), unit_to)


def dimension(unit: str) -> str:
    if unit not in UNITS:
        raise UnitError(f"Unsupported unit '{unit}'")
    return UNITS[unit][0]


# canonical units of operating inputs (API contract)
INPUT_UNITS = {
    "blast_volume_Nm3_min": "Nm3/min", "blast_temp_C": "degC", "blast_moisture_g_Nm3": "g/Nm3",
    "o2_enrichment_pct": "%", "pci_t_h": "t/h", "ng_Nm3_h": "Nm3/h", "h2_Nm3_h": "Nm3/h",
    "top_pressure_kPa_g": "kPa_g", "coke_batch_t": "t", "ore_batch_t": "t",
}
