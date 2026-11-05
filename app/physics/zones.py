"""Furnace geometry, axial/radial discretisation and process-zone classification.

Coordinate convention (enforced everywhere):
  z [m] = height measured UPWARD from the tuyere centre-line (z = 0).
  The hearth occupies z < 0; the stockline is at z = H_work.
  Gas superficial velocity u_g > 0 (upward); solid superficial velocity u_s < 0 (downward).
  Array index 0 of the shaft grid is the BOTTOM cell (just above the tuyeres).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np


@dataclass
class Profile:
    z: np.ndarray       # heights of profile vertices [m] (from hearth bottom pad to top of throat)
    r: np.ndarray       # inner radius at vertices [m]
    labels: List[dict]  # named regions with z-ranges


@dataclass
class Grid:
    n_z: int
    n_r: int
    z_faces: np.ndarray          # (n_z+1,)
    z_c: np.ndarray              # (n_z,)
    dz: np.ndarray               # (n_z,)
    area: np.ndarray             # (n_z,) total cross-section at cell centre [m2]
    vol: np.ndarray              # (n_z,) cell volume [m3]
    wall_area: np.ndarray        # (n_z,) inner lateral wall area [m2]
    r_c: np.ndarray              # (n_z,) radius at cell centre
    sector_area_frac: np.ndarray  # (n_r,) fraction of cross-section in each radial sector
    sector_r_edges: np.ndarray   # (n_r+1,) normalised radius edges
    H_work: float


def radius_at(prof: Profile, z_abs):
    return np.interp(z_abs, prof.z, prof.r)


def build_profile(g: dict) -> Profile:
    """Return the inner profile from the geometry dictionary (all lengths in m).

    Absolute heights z_abs start at the hearth pad (0) and increase upward; the tuyere level
    is at z_abs = sump_depth + taphole_to_tuyere.
    """
    Dh, Db, Dt = g["hearth_diameter_m"], g["belly_diameter_m"], g["throat_diameter_m"]
    h_hearth = g["sump_depth_m"] + g["taphole_to_tuyere_m"]
    z1 = h_hearth                          # tuyere level / top of hearth cylinder
    z2 = z1 + g["bosh_height_m"]
    z3 = z2 + g["belly_height_m"]
    z4 = z3 + g["stack_height_m"]
    z5 = z4 + g["throat_height_m"]
    z = np.array([0.0, z1, z2, z3, z4, z5])
    r = np.array([Dh / 2, Dh / 2, Db / 2, Db / 2, Dt / 2, Dt / 2])
    labels = [
        {"name": "hearth", "z_from": 0.0, "z_to": z1},
        {"name": "bosh", "z_from": z1, "z_to": z2},
        {"name": "belly", "z_from": z2, "z_to": z3},
        {"name": "stack", "z_from": z3, "z_to": z4},
        {"name": "throat", "z_from": z4, "z_to": z5},
    ]
    return Profile(z, r, labels)


def frustum_volume(r1, r2, h):
    return np.pi * h / 3.0 * (r1**2 + r1 * r2 + r2**2)


def volumes(g: dict) -> Dict[str, float]:
    prof = build_profile(g)
    z_tuy = prof.z[1]
    z_stock = prof.z[-1] - g["stockline_m"]
    zz = np.linspace(0.0, prof.z[-1], 4001)
    rr = radius_at(prof, zz)
    a = np.pi * rr**2
    inner = float(np.trapezoid(a, zz))
    m = (zz >= z_tuy) & (zz <= z_stock)
    working = float(np.trapezoid(a[m], zz[m]))
    hearth = float(np.pi * (g["hearth_diameter_m"] / 2) ** 2 * z_tuy)
    return {"inner_volume_m3": inner, "working_volume_m3": working, "hearth_volume_m3": hearth,
            "working_height_m": float(z_stock - z_tuy), "total_height_m": float(prof.z[-1]),
            "tuyere_level_abs_m": float(z_tuy)}


def build_grid(g: dict, n_z: int, n_r: int, cluster: float = 0.0, h_top_min: float = 0.0) -> Grid:
    """Axial grid clustered at both ends (steep thermal gradients at the cohesive zone and at the
    burden surface): z(xi) = H*(xi - a/(2 pi) sin(2 pi xi)), 0 <= a < 1. The top cell is enlarged to
    at least ``h_top_min`` so that one charge fits into it (stockline control)."""
    prof = build_profile(g)
    z_tuy = prof.z[1]
    H = prof.z[-1] - g["stockline_m"] - z_tuy
    xi = np.linspace(0.0, 1.0, n_z + 1)
    zf = H * (xi - cluster / (2 * np.pi) * np.sin(2 * np.pi * xi))
    dz = np.diff(zf)
    if h_top_min > dz[-1]:
        rest = (H - h_top_min) / max(np.sum(dz[:-1]), 1e-9)
        dz = np.concatenate([dz[:-1] * rest, [h_top_min]])
        zf = np.concatenate([[0.0], np.cumsum(dz)])
    zc = 0.5 * (zf[1:] + zf[:-1])
    dz = np.diff(zf)
    r_c = radius_at(prof, zc + z_tuy)
    r_f = radius_at(prof, zf + z_tuy)
    vol = frustum_volume(r_f[:-1], r_f[1:], dz)
    slant = np.sqrt(dz**2 + (r_f[1:] - r_f[:-1]) ** 2)
    wall = np.pi * (r_f[:-1] + r_f[1:]) * slant
    if n_r == 1:
        edges = np.array([0.0, 1.0])
    elif n_r == 3:
        edges = np.array([0.0, 0.45, 0.78, 1.0])   # centre / intermediate / wall annuli
    else:
        edges = np.sqrt(np.linspace(0.0, 1.0, n_r + 1))  # equal-area sectors
    frac = np.diff(edges**2)
    return Grid(n_z, n_r, zf, zc, dz, np.pi * r_c**2, vol, wall, r_c, frac, edges, float(H))


ZONE_ORDER = ["preheating_drying", "indirect_reduction", "thermal_reserve", "direct_reduction",
              "cohesive", "active_coke_dripping"]


def classify_zones(Ts_K: np.ndarray, melt_frac: np.ndarray, T_soft_K: float, T_melt_K: float) -> List[str]:
    """Physically based zone label per cell from solid temperature and melting state."""
    out = []
    for T, mf in zip(Ts_K, melt_frac):
        Tc = T - 273.15
        if mf > 0.97 and T >= T_melt_K - 20:
            out.append("active_coke_dripping")
        elif T >= T_soft_K:
            out.append("cohesive")
        elif Tc >= 1050:
            out.append("direct_reduction")
        elif Tc >= 850:
            out.append("thermal_reserve")
        elif Tc >= 400:
            out.append("indirect_reduction")
        else:
            out.append("preheating_drying")
    return out


def geometry_payload(g: dict, grid: Grid) -> dict:
    prof = build_profile(g)
    vols = volumes(g)
    z_tuy = prof.z[1]
    return {
        "profile": {"z_m": (prof.z - z_tuy).round(4).tolist(), "r_m": prof.r.round(4).tolist(),
                    "regions": [{**lb, "z_from": round(lb["z_from"] - z_tuy, 3), "z_to": round(lb["z_to"] - z_tuy, 3)} for lb in prof.labels]},
        "stockline_z_m": round(grid.H_work, 3),
        "taphole_z_m": round(-g["taphole_to_tuyere_m"], 3),
        "hearth_bottom_z_m": round(-z_tuy, 3),
        "n_tuyeres": g["n_tuyeres"], "tuyere_diameter_m": g["tuyere_diameter_m"],
        "n_tapholes": g["n_tapholes"],
        "grid": {"n_z": grid.n_z, "n_r": grid.n_r, "z_faces_m": grid.z_faces.round(4).tolist(),
                 "z_centres_m": grid.z_c.round(4).tolist(), "radius_m": grid.r_c.round(4).tolist(),
                 "sector_r_edges": grid.sector_r_edges.round(4).tolist()},
        **{k: round(v, 3) for k, v in vols.items()},
        "coordinate_convention": "z upward from tuyere centre-line; u_g>0 upward, u_s<0 downward",
    }
