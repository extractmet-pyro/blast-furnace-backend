"""Wall heat loss: coupled transient conduction (refractory + accretion + stave + shell),
process-side convection/radiation, water-side forced convection and shell-side natural
convection + radiation.

Each wall segment is a 1-D finite-volume conduction problem through layered materials
(cylindrical for walls, planar for the hearth bottom) solved implicitly (backward Euler, the
discrete energy balance closes exactly):

  C_i dT_i/dt = G_{i-1,i}(T_{i-1}-T_i) - G_{i,i+1}(T_i-T_{i+1})
  inner Robin:  q_in  = h_in A_in (T_proc - T_hotface)
  outer:        q_w   = (h_w * phi_pipe) A_out (T_cold - T_water)        (stave / spray / bottom)
  shell path:   q_sh  = (T_cold - T_amb) / (R_grout + R_shell + 1/((h_nc + h_rad) A))
                h_rad = eps sigma (T_s^2 + T_a^2)(T_s + T_a)  (linearised, lagged one step)
                h_nc  = 1.31 (T_s - T_a)^(1/3)  (turbulent natural convection, vertical surface)
  water side:   Nu = 0.023 Re^0.8 Pr^0.4 (Dittus-Boelter)

Material properties are typical vendor-class values (declared, editable in the plant config).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from scipy.linalg import solve_banded

SIGMA = 5.670374419e-8

MATERIALS: Dict[str, dict] = {
    "skull_accretion": {"k": 1.8, "rho": 2800.0, "cp": 1000.0},
    "carbon_micropore": {"k": 15.0, "rho": 1650.0, "cp": 1250.0},
    "semi_graphite": {"k": 30.0, "rho": 1700.0, "cp": 1250.0},
    "ceramic_cup": {"k": 3.5, "rho": 2700.0, "cp": 1050.0},
    "sic_si3n4": {"k": 18.0, "rho": 2650.0, "cp": 1100.0},
    "high_alumina": {"k": 2.2, "rho": 2600.0, "cp": 1050.0},
    "chamotte": {"k": 1.4, "rho": 2200.0, "cp": 1000.0},
    "castable": {"k": 1.3, "rho": 2300.0, "cp": 1000.0},
    "ramming_mass": {"k": 4.0, "rho": 1800.0, "cp": 1100.0},
    "copper_stave": {"k": 350.0, "rho": 8900.0, "cp": 385.0},
    "cast_iron_stave": {"k": 35.0, "rho": 7100.0, "cp": 520.0},
    "steel_shell": {"k": 45.0, "rho": 7850.0, "cp": 490.0},
}


def water_h(flow_m3_h: float, pipe_d_m: float, n_pipes: float, T_C: float = 40.0) -> float:
    rho, mu, k, cp = 992.0, 6.5e-4, 0.63, 4180.0
    area = n_pipes * math.pi * pipe_d_m**2 / 4.0
    v = max(flow_m3_h / 3600.0 / max(area, 1e-9), 1e-3)
    Re = rho * v * pipe_d_m / mu
    Pr = cp * mu / k
    Nu = 0.023 * Re**0.8 * Pr**0.4 if Re > 2300 else 3.66
    return Nu * k / pipe_d_m


@dataclass
class SegmentResult:
    Q_in: float
    Q_water: float
    Q_shell: float
    dE: float
    T_hot: float
    T_cold: float


class WallSegment:
    def __init__(self, name: str, layers: List[dict], r_in: float, height: float, cooling: dict,
                 h_in: float, planar_area: Optional[float] = None, dx_target: float = 0.06):
        self.name = name
        self.layers = layers
        self.cooling = cooling
        self.h_in = h_in
        self.planar = planar_area is not None
        self.height = height
        self.r_in = r_in
        ks, rhos, cps, xs = [], [], [], [0.0]
        self.layer_of_node: List[str] = []
        for lyr in layers:
            mat = MATERIALS.get(lyr["material"], {})
            k = lyr.get("k_W_mK", mat.get("k", 2.0))
            rho = lyr.get("rho_kg_m3", mat.get("rho", 2500.0))
            cp = lyr.get("cp_J_kgK", mat.get("cp", 1000.0))
            n = max(2, int(math.ceil(lyr["thickness_m"] / dx_target)))
            for _ in range(n):
                xs.append(xs[-1] + lyr["thickness_m"] / n)
                ks.append(k)
                rhos.append(rho)
                cps.append(cp)
                self.layer_of_node.append(lyr["material"])
        self.x_faces = np.array(xs)                 # depth from hot face
        self.k = np.array(ks)
        self.n = len(ks)
        if self.planar:
            A = np.full(self.n + 1, planar_area)
            self.A_faces = A
            vol = planar_area * np.diff(self.x_faces)
            # half-cell resistances (planar)
            dxh = np.diff(self.x_faces) / 2.0
            self.R_half = dxh / (self.k * planar_area)
            self.A_in = planar_area
            self.A_out = planar_area
        else:
            rf = r_in + self.x_faces
            self.A_faces = 2 * math.pi * rf * height
            vol = math.pi * (rf[1:] ** 2 - rf[:-1] ** 2) * height
            rc = 0.5 * (rf[1:] + rf[:-1])
            self.R_half = np.log(rc / rf[:-1]) / (2 * math.pi * self.k * height)  # inner half
            self.R_half_out = np.log(rf[1:] / rc) / (2 * math.pi * self.k * height)
            self.A_in = self.A_faces[0]
            self.A_out = self.A_faces[-1]
        if self.planar:
            self.R_half_out = self.R_half.copy()
        self.C = vol * np.array(rhos) * np.array(cps)
        self.G_int = 1.0 / (self.R_half_out[:-1] + self.R_half[1:])
        self.T = np.full(self.n, 330.0)
        self.T_water = cooling.get("water_inlet_C", 35.0) + 273.15
        self.Q_water_last = 0.0
        self.Q_shell_last = 0.0
        self.T_amb = cooling.get("ambient_C", 35.0) + 273.15
        typ = cooling.get("type", "stave_water")
        self.type = typ
        if typ in ("stave_water", "spray_water", "bottom_water"):
            hw = water_h(cooling.get("water_flow_m3_h_per_m2", 1.2) * self.A_out, cooling.get("pipe_d_m", 0.05),
                         cooling.get("pipes_per_m2", 3.0) * self.A_out)
            self.h_out = hw * cooling.get("pipe_area_ratio", 0.8)
        else:
            self.h_out = 0.0
        self.shell_R = 0.0 if typ == "shell_air" else (
            cooling.get("grout_thickness_m", 0.04) / 1.5 + cooling.get("shell_thickness_m", 0.05) / 45.0)
        self.eps = cooling.get("shell_emissivity", 0.8)
        self.h_in_mult = 1.0

    # ----------------------------------------------------------------------------------
    def init_steady(self, T_proc: float):
        for _ in range(60):
            self.step(T_proc, 1e7)

    def _shell_G(self) -> float:
        Ts = float(self.T[-1]) if self.type == "shell_air" else self.T_water + 10.0
        Ts = max(Ts, self.T_amb + 1.0)
        h_rad = self.eps * SIGMA * (Ts**2 + self.T_amb**2) * (Ts + self.T_amb)
        h_nc = 1.31 * max(Ts - self.T_amb, 1.0) ** (1.0 / 3.0)
        return 1.0 / (self.shell_R / self.A_out + 1.0 / ((h_rad + h_nc) * self.A_out))

    def step(self, T_proc: float, dt: float) -> SegmentResult:
        n = self.n
        G_in = 1.0 / (1.0 / (self.h_in * self.h_in_mult * self.A_in) + self.R_half[0])
        G_w = 0.0
        if self.h_out > 0:
            G_w = 1.0 / (1.0 / (self.h_out * self.A_out) + self.R_half_out[-1])
        G_sh = self._shell_G()
        G_sh = 1.0 / (1.0 / G_sh + self.R_half_out[-1])
        # coolant mean temperature (lagged water heating)
        flow = self.cooling.get("water_flow_m3_h_per_m2", 1.2) * self.A_out
        dTw = self.Q_water_last / max(flow / 3600.0 * 992.0 * 4180.0, 1.0)
        T_c = self.T_water + 0.5 * dTw
        ab = np.zeros((3, n))
        rhs = self.C / dt * self.T
        diag = self.C / dt
        diag = diag.copy()
        diag[:-1] += self.G_int
        diag[1:] += self.G_int
        ab[0, 1:] = -self.G_int
        ab[2, :-1] = -self.G_int
        diag[0] += G_in
        rhs[0] += G_in * T_proc
        diag[-1] += G_w + G_sh
        rhs[-1] += G_w * T_c + G_sh * self.T_amb
        ab[1] = diag
        T_new = solve_banded((1, 1), ab, rhs)
        Q_in = G_in * (T_proc - T_new[0])
        Q_w = G_w * (T_new[-1] - T_c)
        Q_sh = G_sh * (T_new[-1] - self.T_amb)
        dE = float(np.sum(self.C * (T_new - self.T)))
        self.T = T_new
        self.Q_water_last = Q_w
        self.Q_shell_last = Q_sh
        return SegmentResult(Q_in, Q_w, Q_sh, dE, float(T_new[0]), float(T_new[-1]))

    def stored_energy(self) -> float:
        return float(np.sum(self.C * (self.T - 298.15)))

    def temperature_at_depth(self, depth_from_cold_face: float) -> float:
        xc = 0.5 * (self.x_faces[1:] + self.x_faces[:-1])
        x = self.x_faces[-1] - depth_from_cold_face
        return float(np.interp(x, xc, self.T))

    def remaining_lining_estimate(self, T_iso_C: float = 1150.0) -> Optional[float]:
        """Inverse 1-D estimate of the T_iso isotherm depth from the cold face (monitoring proxy)."""
        q = (self.Q_water_last + self.Q_shell_last) / max(self.A_out, 1e-9)
        if q <= 1.0:
            return None
        T_iso = T_iso_C + 273.15
        # integrate thermal resistance from cold face inward until T reaches T_iso
        x_faces = self.x_faces
        T_cold = float(self.T[-1])
        T = T_cold
        depth = 0.0
        for i in range(self.n - 1, -1, -1):
            dx = x_faces[i + 1] - x_faces[i]
            dT = q * dx / self.k[i]
            if T + dT >= T_iso:
                return depth + (T_iso - T) / q * self.k[i]
            T += dT
            depth += dx
        return depth  # isotherm inside the process (lining intact up to hot face)

    def payload(self) -> dict:
        xc = 0.5 * (self.x_faces[1:] + self.x_faces[:-1])
        return {"name": self.name, "depth_m": xc.round(4).tolist(), "T_C": (self.T - 273.15).round(1).tolist(),
                "materials": self.layer_of_node, "Q_water_kW": round(self.Q_water_last / 1e3, 2),
                "Q_shell_kW": round(self.Q_shell_last / 1e3, 3),
                "heat_flux_kW_m2": round((self.Q_water_last + self.Q_shell_last) / max(self.A_out, 1e-9) / 1e3, 3),
                "hot_face_C": round(float(self.T[0]) - 273.15, 1), "cold_face_C": round(float(self.T[-1]) - 273.15, 1),
                "area_m2": round(self.A_in, 2), "cooling": self.type}


def tuyere_loss(n_active: int, q_nom_kW: float, T_rw: float, T_rw_nom: float = 2423.0, T_w: float = 308.0) -> float:
    """Tuyere/cooler heat load [W], scaled linearly with raceway-to-water temperature difference."""
    return n_active * q_nom_kW * 1e3 * max(T_rw - T_w, 0.0) / max(T_rw_nom - T_w, 1.0)
