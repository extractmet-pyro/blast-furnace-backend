"""Gas properties, packed-bed hydraulics (Ergun), heat-transfer coefficients and hydraulic
risk indicators.

Ergun (1952):  dP/dz = 150 mu (1-e)^2 u /(e^3 (phi d)^2) + 1.75 rho (1-e) u^2 /(e^3 phi d)
Layered ore/coke burden is treated as resistances in series; in the cohesive zone softened ore
layers block a growing part of the cross-section so gas is forced through coke slits
(u_eff = u / (1 - 0.85 * x_ore * s)); in the active-coke/dripping region the coke voidage is
reduced by the dynamic liquid hold-up. A plant-calibratable permeability multiplier scales the
result. Hanging/slipping/flooding/fluidisation indicators are PROBABILISTIC PROXIES only.
Wakao & Kaguei (1982):  Nu = 2 + 1.1 Re^0.6 Pr^(1/3) for gas-particle heat transfer.
"""
from __future__ import annotations

import numpy as np

from .species import GAS, TRUE_GAS_MASK, R_GAS, MM_GAS, IDX_GAS

# Sutherland constants (mu0 [Pa s] at 273.15 K, S [K]); others default to N2
_SUTH = {"N2": (1.663e-5, 107.0), "CO": (1.657e-5, 136.0), "CO2": (1.370e-5, 222.0), "H2": (0.841e-5, 72.0),
         "H2O": (0.880e-5, 961.0), "O2": (1.919e-5, 139.0), "CH4": (1.030e-5, 198.0)}
_MU0 = np.array([_SUTH.get(k, _SUTH["N2"])[0] for k in GAS])
_SS = np.array([_SUTH.get(k, _SUTH["N2"])[1] for k in GAS])
_K0 = np.array([0.182 if k == "H2" else (0.0182 if k == "H2O" else 0.0240) for k in GAS])  # W/mK at 273 K


def mole_fractions(F: np.ndarray) -> np.ndarray:
    Fg = np.where(TRUE_GAS_MASK, F, 0.0)
    tot = np.sum(Fg, axis=-1, keepdims=True)
    return Fg / np.maximum(tot, 1e-12)


def viscosity(y: np.ndarray, T) -> np.ndarray:
    T = np.asarray(T, dtype=float)[..., None]
    mu = _MU0 * (T / 273.15) ** 1.5 * (273.15 + _SS) / (T + _SS)
    return np.sum(y * mu, axis=-1)


def conductivity(y: np.ndarray, T) -> np.ndarray:
    T = np.asarray(T, dtype=float)[..., None]
    k = _K0 * (T / 273.15) ** 0.8
    return np.sum(y * k, axis=-1)


def molar_mass(y: np.ndarray) -> np.ndarray:
    return np.sum(y * MM_GAS, axis=-1)


def ergun_gradient(u, rho, mu, eps, d, phi):
    eps = np.clip(eps, 0.05, 0.95)
    lin = 150.0 * mu * (1 - eps) ** 2 * u / (eps**3 * (phi * d) ** 2)
    quad = 1.75 * rho * (1 - eps) * u**2 / (eps**3 * phi * d)
    return lin + quad


def bed_pressure_gradient(u, rho, mu, x_ore, soft, d_ore, d_coke, eps_ore, eps_coke, holdup=0.0):
    """Pa/m for a layered ore/coke bed. x_ore: ore volume fraction, soft: 0..1 softening."""
    u_eff = u / np.maximum(1.0 - 0.85 * x_ore * soft, 0.08)
    ore = ergun_gradient(u_eff, rho, mu, eps_ore * (1 - 0.5 * soft), d_ore, 0.80)
    coke = ergun_gradient(u_eff, rho, mu, eps_coke - holdup, d_coke, 0.72)
    w_ore = x_ore * (1 - soft)
    return w_ore * ore + (1 - w_ore) * coke


def heat_transfer_coefficient(u, rho, mu, kg, cp_mass, d):
    Re = np.maximum(rho * u * d / np.maximum(mu, 1e-9), 1e-6)
    Pr = cp_mass * mu / np.maximum(kg, 1e-9)
    Nu = 2.0 + 1.1 * Re**0.6 * Pr ** (1.0 / 3.0)
    return Nu * kg / d


def min_fluidisation_velocity(d_p, rho_p, rho_g, mu):
    """Wen & Yu (1966)."""
    Ar = d_p**3 * rho_g * (rho_p - rho_g) * 9.81 / mu**2
    Re = np.sqrt(33.7**2 + 0.0408 * Ar) - 33.7
    return Re * mu / (rho_g * d_p)


def flooding_index(u_g, rho_g, eps, d_coke, L_kg_m2s, G_kg_m2s, rho_l=6900.0, mu_l=0.005):
    """Sherwood-type flooding ordinate / abscissa and an empirical limit (PROXY)."""
    a_s = 6.0 * (1 - eps) / (0.72 * d_coke)
    ordinate = u_g**2 * a_s / (9.81 * eps**3) * (rho_g / rho_l) * (mu_l / 0.001) ** 0.2
    flux_ratio = (L_kg_m2s / max(G_kg_m2s, 1e-9)) * np.sqrt(rho_g / rho_l)
    limit = 10 ** (-0.559 - 0.746 * np.log10(max(flux_ratio, 1e-4)) - 0.0959 * np.log10(max(flux_ratio, 1e-4)) ** 2)
    return float(ordinate), float(flux_ratio), float(ordinate / max(limit, 1e-9))


def gas_props(F, T, P):
    """Superficial-flow helpers for a gas flow vector F (mol/s) at T (K) and P (Pa)."""
    y = mole_fractions(F)
    n = np.sum(np.where(TRUE_GAS_MASK, F, 0.0), axis=-1)
    M = molar_mass(y)
    rho = P * M / (R_GAS * T)
    Q = n * R_GAS * T / P
    return y, n, M, rho, Q
