"""Dynamic blast-furnace model: conserved multi-zone / axial finite-volume integrator.

State (per radial sector r, axial cell j; j = 0 is the bottom cell just above the tuyeres):
  s_n[r,j,k]   solid species moles (ore- and coke-carrier)
  s_H[r,j]     solid enthalpy [J]   (absolute: formation + sensible)  ->  s_T by inversion
  s_V[r,j,m]   bulk volume by charged material m [m3]
  deadman node (dm_n, dm_H, dm_V), hearth pools (metal, slag), refractory temperatures.
The gas phase is quasi-steady (residence ~ s vs solids ~ h) and is marched upward each step.

Each step conserves every element exactly (stoichiometric transfers, positivity limiters) and
energy exactly (enthalpy states, all exchanges booked on both sides); the residuals reported
by `last_balance` are therefore numerical-roundoff level unless a limiter or guard fires.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from . import thermodynamics as th
from .species import (GAS, SOLID, LIQ, IDX_GAS, IDX_SOLID, IDX_LIQ, TT_GAS, TT_SOLID, TT_LIQ, ORE_MASK, COKE_MASK,
                      HM_MASK, SLAG_MASK, TRUE_GAS_MASK, MM_SOLID, MM_LIQ, MM_GAS, ELEMENTS, E_GAS, E_SOLID, E_LIQ,
                      MOL_PER_NM3, R_GAS, SPECIES)
from .reactions import REACTIONS, COMPILED
from .zones import build_grid, classify_zones, geometry_payload, volumes
from .burden import ore_species_per_kg, coke_species_per_kg, radial_distribution, LayerTracker
from .heat_transfer import WallSegment
from .raceway import external_inputs, raceway
from .dripping import dripping_node, melt_conversion
from .hearth import Hearth
from . import gas_flow as gf
from . import slag_metal as sm
from .material_balance import balance_table, gas_composition, AM
from . import energy_balance as eb
from .raceway import pci_enthalpy_per_kg
from .outputs import History, step_stream_record

P_ATM = 101325.0
_ORE_IDX = np.where(ORE_MASK)[0]
_COKE_IDX = np.where(COKE_MASK)[0]


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


class ModelEnvelopeError(RuntimeError):
    """Raised when the state leaves the model's validated envelope (no silent extrapolation)."""


@dataclass
class StepRecord:
    t: float
    dt: float
    el_in: np.ndarray
    el_out: np.ndarray
    el_acc: np.ndarray
    E_in: float
    E_out: float
    E_acc: float
    terms: Dict[str, float]
    extents: Dict[str, float]
    events: List[dict] = field(default_factory=list)


class BlastFurnaceModel:
    """Hybrid-ready first-principles BF model. `fidelity` = 'fast' (multi-zone) or 'hifi' (axial FV, radial sectors)."""

    def __init__(self, cfg: dict, fidelity: Optional[str] = None, seed: int = 0):
        self.cfg = copy.deepcopy(cfg)
        m = self.cfg["model"]
        self.fidelity = fidelity or m.get("fidelity", "fast")
        if self.fidelity == "hifi":
            mm = m["hifi"]
        else:
            mm = m
        n_z, n_r, dt, n_sub = mm["n_z"], mm["n_r"], mm["dt_s"], mm["n_sub"]
        self.op = self.cfg["operation"]
        self._prepare_materials()
        _, V_ch0, _ = self.charge_composition()
        A_thr = math.pi * (self.cfg["geometry"]["throat_diameter_m"] / 2.0) ** 2
        self.grid = build_grid(self.cfg["geometry"], n_z, n_r, mm.get("grid_cluster", 0.0),
                               h_top_min=1.7 * float(V_ch0.sum()) / A_thr)
        self.n_z, self.n_r, self.dt, self.n_sub = n_z, n_r, float(dt), int(n_sub)
        self.vol = volumes(self.cfg["geometry"])
        self.rng = np.random.default_rng(seed)
        self.t = 0.0
        self.step_no = 0
        self.charge_no = 0
        self._build_walls()
        self.hearth = Hearth(self.cfg, {"sump_wall": self.w_sump, "hearth_bottom": self.w_bottom})
        self.layers = LayerTracker(self.grid.H_work)
        self.events: List[dict] = []
        self.last: Dict[str, object] = {}
        self.cum = {"in": {}, "out": {}, "el_in": np.zeros(len(ELEMENTS)), "el_out": np.zeros(len(ELEMENTS)),
                    "hm_t": 0.0, "slag_t": 0.0, "time_s": 0.0}
        self._init_state()
        self.win = eb.new_window()
        self._win_E0 = self.inventory_energy()
        self.hist = History(maxlen=int(26 * 3600 / self.dt) + 10)
        self.last_charge = {}

    def reset_window(self):
        self.win = eb.new_window()
        self._win_E0 = self.inventory_energy()

    # ------------------------------------------------------------------------------------
    # materials, charge composition
    # ------------------------------------------------------------------------------------
    def _prepare_materials(self):
        cfg = self.cfg
        self.mat_ids = list(cfg["materials"].keys()) + ["coke"]
        self.mat = {}
        for mid, md in cfg["materials"].items():
            vec, clo = ore_species_per_kg(md["chem"], md["moisture_pct"], md["kind"])
            self.mat[mid] = {"vec": vec, "closure": clo, "rho": md["bulk_density_t_m3"] * 1000.0,
                             "d": md["size_mm"] / 1000.0, "eps": md["voidage"], "RI": md.get("RI", 1.0),
                             "Ts": md["softening_C"] + 273.15, "Tm": md["melting_C"] + 273.15,
                             "iron": md["kind"] in ("sinter", "pellet", "lump", "hbi", "dri")}
        cvec, cclo = coke_species_per_kg(cfg["coke"])
        ck = cfg["coke"]
        self.mat["coke"] = {"vec": cvec, "closure": cclo, "rho": ck["bulk_density_t_m3"] * 1000.0,
                            "d": ck["size_mm"] / 1000.0, "eps": ck["voidage"], "RI": 1.0, "Ts": 9999.0, "Tm": 9999.0,
                            "iron": False}
        self.n_mat = len(self.mat_ids)
        self.mat_rho = np.array([self.mat[k]["rho"] for k in self.mat_ids])
        self.mat_d = np.array([self.mat[k]["d"] for k in self.mat_ids])
        self.mat_eps = np.array([self.mat[k]["eps"] for k in self.mat_ids])
        self.mat_RI = np.array([self.mat[k]["RI"] for k in self.mat_ids])
        self.mat_Ts = np.array([self.mat[k]["Ts"] for k in self.mat_ids])
        self.mat_Tm = np.array([self.mat[k]["Tm"] for k in self.mat_ids])
        self.mat_iron = np.array([self.mat[k]["iron"] for k in self.mat_ids])
        self.i_coke = self.mat_ids.index("coke")
        self.ore_mat_mask = np.array([k != "coke" for k in self.mat_ids])

    def charge_composition(self, op=None):
        """Species moles, masses and bulk volumes of one charge (coke batch + ore batch + fluxes)."""
        op = op or self.op
        mix = op["burden_mix"]
        tot = sum(v for v in mix.values() if v > 0)
        if tot <= 0:
            raise ValueError("Burden mix is empty")
        masses = {k: 0.0 for k in self.mat_ids}
        for k, f in mix.items():
            if f > 0:
                if k not in self.mat:
                    raise ValueError(f"Unknown burden material '{k}'")
                masses[k] += op["ore_batch_t"] * 1000.0 * f / tot
        for k, kg in op.get("flux_kg_per_charge", {}).items():
            if kg > 0:
                masses[k] += kg
        masses["coke"] = op["coke_batch_t"] * 1000.0
        n = np.zeros(len(SOLID))
        V = np.zeros(self.n_mat)
        for i, k in enumerate(self.mat_ids):
            if masses[k] > 0:
                n += self.mat[k]["vec"] * masses[k]
                V[i] = masses[k] / self.mat[k]["rho"]
        return n, V, masses

    # ------------------------------------------------------------------------------------
    def _build_walls(self):
        cfg = self.cfg
        g = cfg["geometry"]
        cal = cfg["calibration"]
        H = self.grid.H_work
        self.walls: List[WallSegment] = []
        for j in range(self.n_z):
            frac = self.grid.z_c[j] / H
            zone = next((w for w in cfg["walls"]["shaft"] if w["frac_from"] <= frac < w["frac_to"] + 1e-9),
                        cfg["walls"]["shaft"][-1])
            seg = WallSegment(f"{zone['name']}[{j}]", zone["layers"], self.grid.r_c[j], self.grid.dz[j], zone["cooling"],
                              zone["h_in_W_m2K"] * cal.get("wall_h_in_mult", 1.0))
            seg.zone = zone["name"]
            self.walls.append(seg)
        hw = cfg["walls"]
        Rh = g["hearth_diameter_m"] / 2.0
        self.w_sump = WallSegment("sump_wall", hw["sump_wall"]["layers"], Rh, g["sump_depth_m"],
                                  hw["sump_wall"]["cooling"], hw["sump_wall"]["h_in_W_m2K"])
        self.w_upper = WallSegment("upper_hearth_wall", hw["upper_hearth_wall"]["layers"], Rh, g["taphole_to_tuyere_m"],
                                   hw["upper_hearth_wall"]["cooling"], hw["upper_hearth_wall"]["h_in_W_m2K"])
        self.w_bottom = WallSegment("hearth_bottom", hw["hearth_bottom"]["layers"], Rh, 1.0, hw["hearth_bottom"]["cooling"],
                                    hw["hearth_bottom"]["h_in_W_m2K"], planar_area=math.pi * Rh**2)

    # ------------------------------------------------------------------------------------
    def _distribution(self):
        g = self.cfg["geometry"]
        R_t = g["throat_diameter_m"] / 2.0
        fall_h = g["stockline_m"] + 1.0
        out, sec = radial_distribution(self.op["charging_matrix"], R_t, g.get("chute_length_m", 4.0), fall_h,
                                       self.grid.sector_r_edges)
        return out, sec

    def _init_state(self):
        nr, nz = self.n_r, self.n_z
        n_ch, V_ch, masses = self.charge_composition()
        self.radial, sec = self._distribution()
        if nr == 1:
            f_ore = np.array([1.0])
            f_coke = np.array([1.0])
        else:
            f_ore, f_coke = sec["ore"], sec["coke"]
        self.f_ore_sec, self.f_coke_sec = f_ore, f_coke
        area_f = self.grid.sector_area_frac
        V_ore_ch = V_ch[self.ore_mat_mask].sum()
        V_coke_ch = V_ch[self.i_coke]
        w = V_ch * self.mat_rho * self.mat_iron
        self._Ts_charge = float(np.sum(w * self.mat_Ts) / max(np.sum(w), 1e-12))
        self._Tm_charge = float(np.sum(w * self.mat_Tm) / max(np.sum(w), 1e-12))
        self.s_n = np.zeros((nr, nz, len(SOLID)))
        self.s_V = np.zeros((nr, nz, self.n_mat))
        self.s_H = np.zeros((nr, nz))
        self.s_T = np.zeros((nr, nz))
        H = self.grid.H_work
        n_ore_ch = np.where(ORE_MASK, n_ch, 0.0)
        n_coke_ch = np.where(COKE_MASK, n_ch, 0.0)
        for r in range(nr):
            # volume share of ore in this sector from charging distribution
            vo = V_ore_ch * f_ore[r] / area_f[r]
            vc = V_coke_ch * f_coke[r] / area_f[r]
            xo = vo / (vo + vc)
            for j in range(nz):
                Vc = self.grid.vol[j] * area_f[r]
                Vo_cell = Vc * xo
                Tz = self._initial_T(self.grid.z_c[j] / H)
                # initial cohesive/dripping structure: unmelted ore fraction falls from 1 at the
                # softening temperature to 0 at the melting temperature; the volume freed is coke
                f_unmelted = float(np.clip((self._Tm_charge - Tz) / max(self._Tm_charge - self._Ts_charge, 1.0), 0.0, 1.0))
                Vo_cell *= f_unmelted
                Vc_cell = Vc - Vo_cell
                ore = n_ore_ch / V_ore_ch * Vo_cell
                coke = n_coke_ch / V_coke_ch * Vc_cell
                ore = self._preconditioned_ore(ore, Tz)
                if Tz > 1150:
                    coke = self._devolatilised(coke)
                self.s_n[r, j] = ore + coke
                self.s_V[r, j] = np.where(self.ore_mat_mask, V_ch / max(V_ore_ch, 1e-12) * Vo_cell, 0.0)
                self.s_V[r, j, self.i_coke] = Vc_cell
                self.s_T[r, j] = Tz
        self.s_H = TT_SOLID.H_mix(self.s_n, self.s_T)
        # deadman: whole hearth bulk volume filled with coke
        g = self.cfg["geometry"]
        # active-coke / raceway-feeding node: a fraction of the hearth coke volume (the stagnant
        # deadman core exchanges heat with the hearth pools but does not feed the raceways)
        Vdm = math.pi * g["hearth_diameter_m"] ** 2 / 4.0 * (g["sump_depth_m"] + g["taphole_to_tuyere_m"]) \
            * self.cfg["calibration"].get("active_coke_frac", 0.30)
        self.dm_V_target = Vdm
        self.dm_V = Vdm
        self.dm_n = self._devolatilised(n_coke_ch / V_coke_ch * Vdm)
        self.dm_T = 1800.0
        self.dm_H = float(TT_SOLID.H_mix(self.dm_n, self.dm_T))
        self.hearth.initialise(1773.0)
        for j, w in enumerate(self.walls):
            w.init_steady(float(self.s_T[-1, j]) - 30.0)
        self.w_upper.init_steady(self.dm_T)
        # gas split, pressures
        self.w_gas = area_f.copy()
        P_top = self.op["top_pressure_kPa_g"] * 1000.0 + P_ATM
        self.P_faces = P_top + np.linspace(1.5e5 * min(self.vol["working_volume_m3"] / 4000.0, 1.0) + 0.3e5, 0.0, nz + 1)
        self.P_blast = self.P_faces[0] + 0.15e5
        self.F_faces = np.zeros((nr, nz + 1, len(GAS)))
        self.Tg_faces = np.zeros((nr, nz + 1)) + 500.0
        self.V_top_target = self.s_V[:, -1, :].sum(axis=1).copy()
        self.layers.seed(V_coke_ch / (math.pi * (g["throat_diameter_m"] / 2) ** 2),
                         V_ore_ch / (math.pi * (g["throat_diameter_m"] / 2) ** 2))
        self.rates = {}
        self.u_s_faces = np.zeros((nr, nz + 1))

    @staticmethod
    def _initial_T(zr: float) -> float:
        """Initial solid temperature profile with a thermal-reserve plateau (initial condition only)."""
        pts_z = [0.0, 0.10, 0.22, 0.30, 0.62, 0.85, 1.0]
        pts_T = [1850.0, 1760.0, 1500.0, 1270.0, 1220.0, 800.0, 380.0]
        return float(np.interp(zr, pts_z, pts_T))

    def _preconditioned_ore(self, ore: np.ndarray, T: float) -> np.ndarray:
        """Initial-condition only: reduction state consistent with the local temperature."""
        o = ore.copy()
        i = IDX_SOLID
        fe = 2 * o[i["Fe2O3"]] + 3 * o[i["Fe3O4"]] + o[i["FeO"]]
        if T > 750:
            o[i["Fe2O3"]] = 0.0
            o[i["Fe3O4"]] = 0.0
            if T < 900:
                o[i["Fe3O4"]] = fe / 3.0
                o[i["FeO"]] = 0.0
            else:
                fmet = float(np.clip((T - 1000.0) / 400.0, 0.0, 0.97))
                o[i["FeO"]] = fe * (1 - fmet)
                o[i["Fe"]] += fe * fmet
        if T > 1100:
            o[i["CaO"]] += o[i["CaCO3"]]
            o[i["MgO"]] += o[i["MgCO3"]]
            o[i["CaCO3"]] = o[i["MgCO3"]] = 0.0
        if T > 420:
            o[i["H2O_l"]] = 0.0
        if T > 700:
            o[i["H2O_b"]] = 0.0
        if T > 1200:
            o[i["ZnO"]] = 0.0
        return o

    @staticmethod
    def _devolatilised(coke: np.ndarray) -> np.ndarray:
        c = coke.copy()
        for k in ("H2_k", "N2_k", "CO_k", "H2O_k"):
            c[IDX_SOLID[k]] = 0.0
        return c

    # ------------------------------------------------------------------------------------
    # inventories
    # ------------------------------------------------------------------------------------
    def inventory_elements(self) -> np.ndarray:
        return E_SOLID @ self.s_n.reshape(-1, len(SOLID)).sum(axis=0) + E_SOLID @ self.dm_n + E_LIQ @ self.hearth.n

    def inventory_energy(self) -> float:
        walls = sum(w.stored_energy() for w in self.walls) + self.w_sump.stored_energy() + \
            self.w_upper.stored_energy() + self.w_bottom.stored_energy()
        return float(np.sum(self.s_H) + self.dm_H + self.hearth.H_hm + self.hearth.H_sl + walls)

    # ------------------------------------------------------------------------------------
    # main step
    # ------------------------------------------------------------------------------------
    def set_inputs(self, **kw):
        for k, v in kw.items():
            if k not in self.op:
                raise KeyError(f"Unknown operating input '{k}'")
            self.op[k] = v

    def step(self, dt: Optional[float] = None) -> StepRecord:
        dt = float(dt or self.dt)
        cfg, op, cal = self.cfg, self.op, self.cfg["calibration"]
        nr, nz = self.n_r, self.n_z
        terms: Dict[str, float] = {}
        ext_ = {}
        events: List[dict] = []
        el0 = self.inventory_elements()
        E0 = self.inventory_energy()
        dm_mass0 = float(np.sum(self.dm_n * MM_SOLID))
        dm_V0 = self.dm_V
        el_in = np.zeros(len(ELEMENTS))
        el_out = np.zeros(len(ELEMENTS))
        E_in = E_out = 0.0

        # (1) charging ---------------------------------------------------------------------
        ch = self._charging(events)
        self.last_charge = ch
        el_in += ch["el"]
        E_in += ch["H"]
        el_out += ch["dust_el"]
        E_out += ch["dust_H"]
        terms["charge_H_W"] = ch["H"] / dt
        terms["dust_H_W"] = ch["dust_H"] / dt

        # (2) raceway ----------------------------------------------------------------------
        ext = external_inputs(op, cfg)
        n_active = max(cfg["geometry"]["n_tuyeres"] - int(op.get("n_tuyeres_closed", 0)), 1)
        el_in += ext.elements * dt
        E_in += ext.H_total * dt
        self.last_ext = ext
        if ext.blast_dry_Nm3_s > 0:
            rw = raceway(ext, self.dm_n, self.dm_T, cfg, n_active, self.P_blast)
            h_dm = TT_SOLID.h(self.dm_T)
            self.dm_n -= rw.coke_parcel * dt
            self.dm_H -= float(rw.coke_parcel @ h_dm) * dt
            E_out += rw.Q_tuyere * dt
            ext_["RW_C"] = float(rw.diag["coke_C_mol_s"] * dt)
        else:
            rw = None
        self.last_rw = rw

        # (3) melting in shaft cells ----------------------------------------------------------
        melt_liq = np.zeros(len(LIQ))
        melt_H = 0.0
        melt_gas = np.zeros((nr, nz, len(GAS)))
        self.melt_rate = np.zeros((nr, nz))
        self.soft = np.zeros((nr, nz))
        dV_ore = np.zeros((nr, nz))
        dV_coke = np.zeros((nr, nz))
        Ts_cell, Tm_cell = self._cell_soft_melt()
        for r in range(nr):
            for j in range(nz):
                T = self.s_T[r, j]
                phi = _smoothstep((T - Ts_cell[r, j]) / max(Tm_cell[r, j] - Ts_cell[r, j], 1.0))
                self.soft[r, j] = phi
                k = phi / cal.get("melt_tau_s", 1800.0) + (1.0 / 600.0 if T > Tm_cell[r, j] + 30 else 0.0)
                if k <= 0:
                    continue
                frac = min(k * dt, 0.95)
                n_m = np.where(ORE_MASK, self.s_n[r, j], 0.0) * frac
                if np.sum(n_m) <= 0:
                    continue
                liq, gas, cneed = melt_conversion(n_m)
                for nm_, key_ in (("M_caco3", "CaCO3"), ("M_mgco3", "MgCO3"), ("M_h2o_l", "H2O_l"), ("M_h2o_b", "H2O_b")):
                    ext_[nm_] = ext_.get(nm_, 0.0) + float(n_m[IDX_SOLID[key_]])
                cneed = min(cneed, 0.9 * self.s_n[r, j, IDX_SOLID["C_k"]])
                if cneed < -1e-12:
                    cneed = 0.0
                self.s_n[r, j] -= n_m
                self.s_n[r, j, IDX_SOLID["C_k"]] -= cneed
                # melts drain from the softened ore layers AT (or just above) their melting temperature:
                # liquid cannot leave below the burden melting point - when the cell is colder the
                # latent/sensible heat is drawn from the cell (exact bookkeeping via s_H), which
                # self-limits melting; excess sensible heat of a hotter cell stays with the coke.
                T_l = float(np.clip(T, Tm_cell[r, j], Tm_cell[r, j] + cal.get("melt_exit_superheat_K", 25.0)))
                Hl = float(liq @ TT_LIQ.h(T_l))
                Hg = float(gas @ TT_GAS.h(T))
                self.s_H[r, j] -= Hl + Hg
                melt_liq += liq / dt
                melt_H += Hl / dt
                melt_gas[r, j] += gas / dt
                self.melt_rate[r, j] = float(np.sum(liq[HM_MASK] * MM_LIQ[HM_MASK])) / dt
                dV_ore[r, j] += frac * self.s_V[r, j, self.ore_mat_mask].sum()
                self.s_V[r, j, self.ore_mat_mask] *= (1 - frac)
                if cneed > 0:
                    c0 = self.s_n[r, j, IDX_SOLID["C_k"]] + cneed
                    fc = cneed / max(c0, 1e-12)
                    dV_coke[r, j] += fc * self.s_V[r, j, self.i_coke]
                    self.s_V[r, j, self.i_coke] *= (1 - fc)
                    ext_["M_conv_C"] = ext_.get("M_conv_C", 0.0) + cneed
        self.melt_liq = melt_liq

        # (4) dripping / deadman node ---------------------------------------------------------
        liq_in = melt_liq.copy()
        H_liq_in = melt_H
        if rw is not None:
            liq_in += rw.liquids
            H_liq_in += rw.H_liq
            gas_rw, T_rw = rw.gas, rw.T_gas
        else:
            gas_rw, T_rw = np.zeros(len(GAS)), self.dm_T
        self.h_drip = self._dripping_height()
        dr = dripping_node(gas_rw, T_rw, liq_in, H_liq_in, self.dm_n, self.dm_T, cfg, dt, self.h_drip)
        self.dm_n -= dr.dm_consumed * dt
        self.dm_H += dr.Q_node * dt
        for kk, vv in dr.rates.items():
            ext_[kk] = ext_.get(kk, 0.0) + vv * dt
        self.last_drip = dr

        # (5) hearth ---------------------------------------------------------------------------
        p_g = self.P_faces[0] - P_ATM
        hr = self.hearth.step(dr.liq_out, dr.H_liq_out, dr.T_liq_out, self.dm_T, dt, p_g, self.t)
        self.dm_H -= hr.Q_from_dm * dt
        uw = self.w_upper.step(self.dm_T, dt)
        self.dm_H -= uw.Q_in * dt
        E_out += (hr.Q_water + hr.Q_shell + uw.Q_water + uw.Q_shell) * dt
        el_out += (E_LIQ @ hr.tapped) * dt
        E_out += (hr.H_tapped_hm + hr.H_tapped_sl) * dt
        terms["hearth_wall_loss_W"] = hr.Q_water + hr.Q_shell + uw.Q_water + uw.Q_shell
        terms["tapped_H_W"] = hr.H_tapped_hm + hr.H_tapped_sl
        for ev in hr.events:
            ev["t_s"] = self.t
            events.append(ev)
        self.last_hearth = hr
        # deadman temperature after its exchanges
        self.dm_T = float(TT_SOLID.T_from_H(self.dm_n, np.array(self.dm_H), np.array(self.dm_T)))

        # (6) gas march ------------------------------------------------------------------------
        self.s_T = TT_SOLID.T_from_H(self.s_n, self.s_H, self.s_T)  # after charging & melting
        F0 = dr.gas_out + hr.gas
        H0 = float(dr.gas_out @ TT_GAS.h(dr.T_gas_out)) + hr.H_gas
        T0 = float(TT_GAS.T_from_H(F0, np.array(H0), np.array(dr.T_gas_out)))
        march = self._gas_march(F0, H0, T0, melt_gas, dt, ext_)
        el_out += (E_GAS @ march["top_F"]) * dt
        E_out += march["top_H"] * dt
        E_out += march["Q_wall_out"] * dt
        terms["top_gas_H_W"] = march["top_H"]
        terms["shaft_wall_loss_W"] = march["Q_wall_out"]
        terms["tuyere_loss_W"] = rw.Q_tuyere if rw is not None else 0.0

        # (7) descent -----------------------------------------------------------------------------
        dV_coke += march["dV_coke"]
        V_dm_cur = dm_V0 * float(np.sum(self.dm_n * MM_SOLID)) / max(dm_mass0, 1e-9)  # constant bulk density
        self._descent(dV_ore, dV_coke, dt, V_dm_cur)

        # (8) temperatures ------------------------------------------------------------------------
        self.s_T = TT_SOLID.T_from_H(self.s_n, self.s_H, self.s_T)
        self.dm_T = float(TT_SOLID.T_from_H(self.dm_n, np.array(self.dm_H), np.array(self.dm_T)))
        if not np.all(np.isfinite(self.s_T)) or np.min(self.s_n) < -1e-6:
            raise ModelEnvelopeError("Non-physical state (negative inventory or non-finite temperature)")

        # (9) hydraulics --------------------------------------------------------------------------
        self._hydraulics(march, rw)

        # (10) balances ----------------------------------------------------------------------------
        el1 = self.inventory_elements()
        E1 = self.inventory_energy()
        rec = StepRecord(self.t, dt, el_in, el_out, el1 - el0, E_in, E_out, E1 - E0, terms, ext_, events)
        self._accumulate(rec, ch, ext, hr, march)
        self.last_record = rec
        self.hist.add(step_stream_record(self))
        self.layers.advect(self.grid.z_faces, np.mean(self.u_s_faces, axis=0), dt, self._cz_bottom())
        self.t += dt
        self.step_no += 1
        self.events.extend(events)
        self.events = self.events[-500:]
        return rec

    # ------------------------------------------------------------------------------------
    def _cell_soft_melt(self):
        V = self.s_V
        w = V * self.mat_rho * self.mat_iron
        tot = np.sum(w, axis=-1)
        Ts = np.where(tot > 0, np.sum(w * self.mat_Ts, axis=-1) / np.maximum(tot, 1e-12), 1450.0)
        Tm = np.where(tot > 0, np.sum(w * self.mat_Tm, axis=-1) / np.maximum(tot, 1e-12), 1720.0)
        # FeO-rich primary slag softens earlier (cold-furnace mechanism) - provisional
        fe_o = self.s_n[..., IDX_SOLID["FeO"]]
        fe_t = fe_o + self.s_n[..., IDX_SOLID["Fe"]] + 1e-12
        Ts = Ts - 120.0 * np.clip((fe_o / fe_t - 0.15) / 0.5, 0.0, 1.0)
        return Ts, Tm

    def _cell_bed(self, r, j):
        V = self.s_V[r, j]
        Vt = max(V.sum(), 1e-12)
        x_ore = float(V[self.ore_mat_mask].sum() / Vt)
        vo = V[self.ore_mat_mask]
        if vo.sum() > 1e-9:
            d_ore = float(vo.sum() / np.sum(vo / self.mat_d[self.ore_mat_mask]))
            eps_ore = float(np.sum(vo * self.mat_eps[self.ore_mat_mask]) / vo.sum())
            ri = float(np.sum(vo * self.mat_rho[self.ore_mat_mask] * self.mat_RI[self.ore_mat_mask]) /
                       np.sum(vo * self.mat_rho[self.ore_mat_mask]))
        else:
            d_ore, eps_ore, ri = 0.015, 0.40, 1.0
        return x_ore, d_ore, eps_ore, ri

    # ------------------------------------------------------------------------------------
    def _charging(self, events):
        op = self.op
        g = self.cfg["geometry"]
        A_t = math.pi * (g["throat_diameter_m"] / 2.0) ** 2
        cur = self.s_V[:, -1, :].sum(axis=1)
        deficit = float(np.sum(self.V_top_target - cur))
        n_ch, V_ch, masses = self.charge_composition()
        V_charge = float(V_ch.sum())
        self.stockline_m = self.grid.H_work - max(deficit, 0.0) / A_t  # height above tuyere
        out = {"el": np.zeros(len(ELEMENTS)), "H": 0.0, "dust_el": np.zeros(len(ELEMENTS)), "dust_H": 0.0,
               "masses": {}, "charged": False}
        if deficit < 0.92 * V_charge or not op.get("blast_on", True) and deficit < 1.5 * V_charge:
            return out
        T_ch = op.get("charge_temperature_C", 30.0) + 273.15
        dcfg = self.cfg["dust"]
        dust = np.where(ORE_MASK, n_ch * dcfg["ore_dust_frac"], n_ch * dcfg["coke_dust_frac"])
        n_in = n_ch - dust
        h = TT_SOLID.h(T_ch)
        out["el"] = E_SOLID @ n_ch
        out["H"] = float(n_ch @ h)
        out["dust_el"] = E_SOLID @ dust
        out["dust_H"] = float(dust @ h)
        out["dust_n"] = dust
        out["n_full"] = n_ch
        out["masses"] = masses
        out["charged"] = True
        self.radial, sec = self._distribution()
        if self.n_r > 1:
            self.f_ore_sec, self.f_coke_sec = sec["ore"], sec["coke"]
        V_in = V_ch * (1 - np.where(self.ore_mat_mask, self.cfg["dust"]["ore_dust_frac"], self.cfg["dust"]["coke_dust_frac"]))
        for r in range(self.n_r):
            fo = self.f_ore_sec[r] if self.n_r > 1 else 1.0
            fc = self.f_coke_sec[r] if self.n_r > 1 else 1.0
            add = np.where(ORE_MASK, n_in * fo, n_in * fc)
            self.s_n[r, -1] += add
            self.s_H[r, -1] += float(add @ h)
            self.s_V[r, -1] += np.where(self.ore_mat_mask, V_in * fo, V_in * fc)
        self.charge_no += 1
        thick_c = V_ch[self.i_coke] / A_t
        thick_o = V_ch[self.ore_mat_mask].sum() / A_t
        self.layers.add_charge(self.charge_no, thick_c, thick_o)
        events.append({"type": "CHARGE", "charge_no": self.charge_no, "t": self.t,
                       "masses_t": {k: round(v / 1000.0, 3) for k, v in masses.items() if v > 0},
                       "stockline_m_below_zero": round(max(deficit, 0.0) / A_t + g["stockline_m"], 3)})
        return out

    # ------------------------------------------------------------------------------------
    def _gas_march(self, F0, H0, T0, melt_gas, dt, ext_):
        cfg, cal = self.cfg, self.cfg["calibration"]
        nr, nz = self.n_r, self.n_z
        kmul = cal.get("kinetic_multipliers", {})
        hmul = cal.get("heat_transfer_mult", 1.0)
        cri_f = cfg["coke"].get("CRI", 24.0) / 24.0
        F = np.outer(self.w_gas, F0)
        Hg = self.w_gas * H0
        Tg = np.full(nr, T0)
        self.F_faces[:, 0] = F
        self.Tg_faces[:, 0] = Tg
        area_f = self.grid.sector_area_frac
        rates = {k: np.zeros((nr, nz)) for k in ("R_hem_CO", "R_mag_CO", "R_wus_CO", "R_hem_H2", "R_mag_H2", "R_wus_H2",
                                                 "R_boud", "R_wg", "R_wgs", "R_calc", "R_mgcalc", "R_dry", "R_dry_k",
                                                 "R_dehyd", "R_vm_H2", "R_vm_N2", "R_vm_CO", "R_zn", "R_sio_ox",
                                                 "R_char_boud", "R_char_wg", "R_dr_hem", "R_dr_mag")}
        Qgs_cell = np.zeros((nr, nz))
        Qw_cell = np.zeros(nz)
        dV_coke = np.zeros((nr, nz))
        Q_wall_out = 0.0
        self.wall_results = []
        for j in range(nz):
            Ts = self.s_T[:, j].copy()
            hTs = TT_GAS.h(Ts)
            # melt gas products enter at solid temperature
            if np.any(melt_gas[:, j]):
                F = F + melt_gas[:, j]
                Hg = Hg + np.sum(melt_gas[:, j] * hTs, axis=1)
            avail = self.s_n[:, j].copy()
            C0 = avail[:, IDX_SOLID["C_k"]].copy()
            S = np.zeros((nr, len(SOLID)))
            Qrx = np.zeros(nr)
            P = 0.5 * (self.P_faces[j] + self.P_faces[j + 1])
            Vs = self.grid.vol[j] * area_f
            As = self.grid.area[j] * area_f
            beds = [self._cell_bed(r, j) for r in range(nr)]
            x_ore = np.array([b[0] for b in beds])
            d_ore = np.array([b[1] for b in beds])
            eps_ore = np.array([b[2] for b in beds])
            ri = np.array([b[3] for b in beds])
            d_c = self.mat_d[self.i_coke]
            eps_c = self.mat_eps[self.i_coke]
            d_bed = 1.0 / (x_ore / d_ore + (1 - x_ore) / d_c)
            eps_bed = x_ore * eps_ore + (1 - x_ore) * eps_c
            size_f = (0.015 / d_ore) ** 1.0
            f = 1.0 / self.n_sub
            for _sub in range(self.n_sub):
                Tg = TT_GAS.T_from_H(F, Hg, Tg)
                hTg = TT_GAS.h(Tg)
                Ft = np.sum(np.where(TRUE_GAS_MASK, F, 0.0), axis=1)
                Ft = np.maximum(Ft, 1e-9)
                y_q, n_q, M_q, rho_q, Q_q = gf.gas_props(F, Tg, P)
                tau_g = Vs * eps_bed / np.maximum(Q_q, 1e-9)

                def apply(name, x, solid_side=True):
                    nonlocal F, Hg, Qrx
                    cr = COMPILED[name]
                    xx = x[:, None]
                    if cr.g_idx.size:
                        dF = xx * cr.g_nu
                        F[:, cr.g_idx] += dF
                        if solid_side:
                            # reactants leave gas at Tg, products enter at Ts
                            dH = np.sum(np.where(dF < 0, dF * hTg[:, cr.g_idx], dF * hTs[:, cr.g_idx]), axis=1)
                            Hg = Hg + dH
                            Qrx = Qrx - dH
                    if cr.s_idx.size:
                        S[:, cr.s_idx] += xx * cr.s_nu
                        avail[:, cr.s_idx] += xx * cr.s_nu * dt
                    rates[name][:, j] += x

                def lim_solid(name, x):
                    cr = COMPILED[name]
                    for idx, nu in zip(cr.s_idx, cr.s_nu):
                        if nu < 0:
                            x = np.minimum(x, 0.9 * np.maximum(avail[:, idx], 0.0) / (-nu * dt))
                    return np.maximum(x, 0.0)

                def k_of(name, T):
                    return REACTIONS[name].kinetics.k(T) * kmul.get(name, 1.0)

                # --- drying, dehydration, volatiles
                for nm, key in (("R_dry", "H2O_l"), ("R_dry_k", "H2O_k")):
                    x = k_of(nm, Ts) * np.clip((Ts - 373.15) / 15.0, 0.0, 1.0) * avail[:, IDX_SOLID[key]] * f
                    apply(nm, lim_solid(nm, x))
                x = k_of("R_dehyd", Ts) * avail[:, IDX_SOLID["H2O_b"]] * f
                apply("R_dehyd", lim_solid("R_dehyd", x))
                for nm, key in (("R_vm_H2", "H2_k"), ("R_vm_N2", "N2_k"), ("R_vm_CO", "CO_k")):
                    x = k_of(nm, Ts) * avail[:, IDX_SOLID[key]] * f
                    apply(nm, lim_solid(nm, x))
                # --- carbonates
                for nm, key, kind in (("R_calc", "CaCO3", "calc"), ("R_mgcalc", "MgCO3", "mgcalc")):
                    n_c = avail[:, IDX_SOLID[key]]
                    if np.any(n_c > 0):
                        p_eq = th.p_co2_calcination(Ts, kind)
                        pco2 = F[:, IDX_GAS["CO2"]] / Ft * P
                        drive = np.clip(1.0 - pco2 / np.maximum(p_eq, 1e-9), 0.0, 1.0)
                        x = k_of(nm, Ts) * n_c * drive * f
                        apply(nm, lim_solid(nm, x))
                # --- zinc
                x = k_of("R_zn", Ts) * avail[:, IDX_SOLID["ZnO"]] * np.clip((Ts - 1100.0) / 100.0, 0.0, 1.0) * f
                x = np.minimum(x, 0.5 * F[:, IDX_GAS["CO"]])
                apply("R_zn", lim_solid("R_zn", x))
                # --- indirect reduction (CO and H2), stage-wise
                feS = 2 * avail[:, IDX_SOLID["Fe2O3"]] + 3 * avail[:, IDX_SOLID["Fe3O4"]] + avail[:, IDX_SOLID["FeO"]] + avail[:, IDX_SOLID["Fe"]] + 1e-12
                conv = {"Fe2O3": 1.0 - 2 * avail[:, IDX_SOLID["Fe2O3"]] / feS,
                        "Fe3O4": (avail[:, IDX_SOLID["FeO"]] + avail[:, IDX_SOLID["Fe"]]) / feS,
                        "FeO": avail[:, IDX_SOLID["Fe"]] / feS}
                beta_d = cal.get("reduction_diffusion_beta", 5.0)
                for nm, ox, red, oxg, eq, nu_ox in (
                        ("R_hem_CO", "Fe2O3", "CO", "CO2", "hem_CO", 3), ("R_hem_H2", "Fe2O3", "H2", "H2O", "hem_H2", 3),
                        ("R_mag_CO", "Fe3O4", "CO", "CO2", "mag_CO", 1), ("R_mag_H2", "Fe3O4", "H2", "H2O", "mag_H2", 1),
                        ("R_wus_CO", "FeO", "CO", "CO2", "wus_CO", 1), ("R_wus_H2", "FeO", "H2", "H2O", "wus_H2", 1)):
                    n_ox = avail[:, IDX_SOLID[ox]]
                    if not np.any(n_ox > 1e-12):
                        continue
                    if ox in ("Fe3O4",):
                        Teff = np.maximum(Ts, 843.0)  # below 570 degC magnetite -> Fe path neglected
                    else:
                        Teff = Ts
                    K = th.K(eq, Teff)
                    Fr, Fo = F[:, IDX_GAS[red]], F[:, IDX_GAS[oxg]]
                    x_eq = np.maximum((K * Fr - Fo) / (1.0 + K), 0.0)
                    DF = x_eq / Ft
                    # product-layer diffusion slow-down (shrinking-core, mixed control):
                    # k_eff = k / (1 + beta ((1-X)^(-1/3) - 1)), X = local stage conversion
                    X = conv[ox]
                    diff = 1.0 + beta_d * (np.maximum(1.0 - X, 1e-4) ** (-1.0 / 3.0) - 1.0)
                    x = k_of(nm, Ts) * size_f * ri * n_ox * DF * f / diff
                    x = np.minimum(x, 0.95 * x_eq)
                    apply(nm, lim_solid(nm, x))
                # --- high-temperature direct reduction of residual higher oxides
                for nm, ox in (("R_dr_hem", "Fe2O3"), ("R_dr_mag", "Fe3O4")):
                    n_ox = avail[:, IDX_SOLID[ox]]
                    if np.any(n_ox > 0):
                        x = k_of(nm, Ts) * n_ox * f * (Ts > 1173.0)
                        apply(nm, lim_solid(nm, x))
                # --- carbon gasification by CO2 / H2O (coke) and char
                Pb = P / 1e5
                for nm, oxg, prod2, eqk in (("R_boud", "CO2", None, "boud"), ("R_wg", "H2O", "H2", "wg")):
                    Kb = th.K(eqk, Ts)
                    Ft = np.maximum(np.sum(np.where(TRUE_GAS_MASK, F, 0.0), axis=1), 1e-9)
                    xm = self._eq_extent_gasification(F, Ft, Kb, Pb, nm)
                    nC = avail[:, IDX_SOLID["C_k"]]
                    x = k_of(nm, Ts) * cri_f * nC * (xm / Ft) * f
                    x = np.minimum(x, 0.95 * xm)
                    apply(nm, lim_solid(nm, x))
                for nm, oxg, eqk, kref in (("R_char_boud", "CO2", "boud", 0.15), ("R_char_wg", "H2O", "wg", 0.30)):
                    nch = F[:, IDX_GAS["Char"]]
                    if np.any(nch > 0):
                        Kb = th.K(eqk, Tg)
                        Ft = np.maximum(np.sum(np.where(TRUE_GAS_MASK, F, 0.0), axis=1), 1e-9)
                        xm = self._eq_extent_gasification(F, Ft, Kb, Pb, "R_boud" if nm == "R_char_boud" else "R_wg")
                        kk = kref * np.exp(-180e3 / R_GAS * (1.0 / Tg - 1.0 / 1273.0)) * kmul.get(nm, 1.0)
                        x = np.minimum((1 - np.exp(-kk * tau_g * f)) * nch, 0.95 * xm)
                        apply(nm, np.maximum(x, 0.0), solid_side=False)
                # --- water-gas shift (gas phase, Fe-catalysed relaxation)
                Kw = th.K("wgs", Tg)
                a, b, c = (1 - Kw), (F[:, IDX_GAS["CO2"]] + F[:, IDX_GAS["H2"]] + Kw * (F[:, IDX_GAS["CO"]] + F[:, IDX_GAS["H2O"]])), \
                    (F[:, IDX_GAS["CO2"]] * F[:, IDX_GAS["H2"]] - Kw * F[:, IDX_GAS["CO"]] * F[:, IDX_GAS["H2O"]])
                x_eq = self._wgs_root(a, b, c, F)
                phi = 1 - np.exp(-k_of("R_wgs", Tg) * tau_g * f * 10.0)
                apply("R_wgs", phi * x_eq, solid_side=False)
                # --- SiO re-oxidation in the bosh/lower shaft
                nsio = F[:, IDX_GAS["SiO"]]
                if np.any(nsio > 0):
                    x = np.clip((1900.0 - Tg) / 300.0, 0.0, 1.0) * 0.7 * f * nsio
                    x = np.minimum(x, 0.5 * F[:, IDX_GAS["CO"]])
                    apply("R_sio_ox", x)
                # --- gas-solid heat exchange (plug flow through the sub-slice)
                Tg = TT_GAS.T_from_H(F, Hg, Tg)
                y, n_g, M, rho, Q = gf.gas_props(F, Tg, P)
                u = Q / As
                mu = gf.viscosity(y, Tg)
                kg = gf.conductivity(y, Tg)
                cp_m = np.sum(F * TT_GAS.cp(Tg), axis=1) / np.maximum(np.sum(F * MM_GAS, axis=1), 1e-12)
                h = gf.heat_transfer_coefficient(u, rho, mu, kg, cp_m, d_bed) * hmul
                a_v = 6.0 * (1 - eps_bed) / (0.75 * d_bed)
                Cg = np.sum(F * TT_GAS.cp(Tg), axis=1)
                NTU = h * a_v * Vs * f / np.maximum(Cg, 1e-9)
                Tg_new = Ts + (Tg - Ts) * np.exp(-NTU)
                Hg_new = np.sum(F * TT_GAS.h(Tg_new), axis=1)
                Q = Hg - Hg_new
                Hg = Hg_new
                Tg = Tg_new
                Qgs_cell[:, j] += Q
            # --- wall heat loss from outer sector (half from gas, half from solids)
            ro = nr - 1
            T_proc = 0.5 * (Tg[ro] + Ts[ro])
            wr = self.walls[j].step(float(T_proc), dt)
            self.wall_results.append(wr)
            Hg[ro] -= 0.5 * wr.Q_in
            Qw_cell[j] = wr.Q_in
            Q_wall_out += wr.Q_water + wr.Q_shell
            Tg = TT_GAS.T_from_H(F, Hg, Tg)
            # --- apply solid sources and enthalpy
            self.s_n[:, j] += S * dt
            neg = self.s_n[:, j] < 0
            if np.any(neg):
                if np.min(self.s_n[:, j]) < -1e-6 * max(1.0, float(np.max(np.abs(self.s_n[:, j])))):
                    raise ModelEnvelopeError("Negative solid inventory in shaft cell")
                self.s_n[:, j] = np.where(neg, 0.0, self.s_n[:, j])
            self.s_H[:, j] += (Qrx + Qgs_cell[:, j]) * dt
            self.s_H[ro, j] -= 0.5 * wr.Q_in * dt
            consumed = np.maximum(C0 - self.s_n[:, j, IDX_SOLID["C_k"]], 0.0)
            fc = consumed / np.maximum(C0, 1e-12)
            dV_coke[:, j] = fc * self.s_V[:, j, self.i_coke]
            self.s_V[:, j, self.i_coke] *= (1 - fc)
            self.F_faces[:, j + 1] = F
            self.Tg_faces[:, j + 1] = Tg
        for k, v in rates.items():
            ext_[k] = float(np.sum(v) * dt)
        self.rates = rates
        self.Qgs_cell = Qgs_cell
        self.Qw_cell = Qw_cell
        top_F = F.sum(axis=0)
        top_H = float(np.sum(Hg))
        return {"top_F": top_F, "top_H": top_H, "top_F_sec": F.copy(), "top_T_sec": Tg.copy(),
                "Q_wall_out": Q_wall_out, "dV_coke": dV_coke}

    @staticmethod
    def _eq_extent_gasification(F, Ft, K, Pb, nm):
        """Forward equilibrium extent for C + CO2 = 2CO or C + H2O = CO + H2 (quadratic in x)."""
        CO, CO2, H2, H2O = F[:, IDX_GAS["CO"]], F[:, IDX_GAS["CO2"]], F[:, IDX_GAS["H2"]], F[:, IDX_GAS["H2O"]]
        if nm == "R_boud":
            a = 4 * Pb + K
            b = 4 * Pb * CO - K * CO2 + K * Ft
            c = Pb * CO**2 - K * CO2 * Ft
            lim = CO2
        else:
            a = Pb + K
            b = Pb * (CO + H2) + K * (Ft - H2O)
            c = Pb * CO * H2 - K * H2O * Ft
            lim = H2O
        disc = np.maximum(b * b - 4 * a * c, 0.0)
        x = (-b + np.sqrt(disc)) / (2 * a)
        return np.clip(x, 0.0, lim)

    @staticmethod
    def _wgs_root(a, b, c, F):
        CO, CO2, H2, H2O = F[:, IDX_GAS["CO"]], F[:, IDX_GAS["CO2"]], F[:, IDX_GAS["H2"]], F[:, IDX_GAS["H2O"]]
        lo = -np.minimum(CO2, H2)
        hi = np.minimum(CO, H2O)
        x = np.where(np.abs(a) < 1e-12, -c / np.where(np.abs(b) < 1e-12, 1.0, b), 0.0)
        disc = np.maximum(b * b - 4 * a * c, 0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            r1 = (-b + np.sqrt(disc)) / (2 * a)
            r2 = (-b - np.sqrt(disc)) / (2 * a)
        cand = np.where((r1 >= lo - 1e-12) & (r1 <= hi + 1e-12), r1, r2)
        x = np.where(np.abs(a) < 1e-12, x, cand)
        return np.clip(np.nan_to_num(x), lo, hi)

    # ------------------------------------------------------------------------------------
    def _descent(self, dV_ore, dV_coke, dt, V_dm_cur):
        nr, nz = self.n_r, self.n_z
        cal = self.cfg["calibration"]
        need = max(self.dm_V_target - V_dm_cur, 0.0)
        # descent distribution over sectors: the wall annulus above the raceway descends fastest
        if nr == 1:
            wr = np.array([1.0])
        else:
            a = self.grid.sector_area_frac
            ws = cal.get("descent_wall_frac", 0.55)
            wr = np.concatenate([a[:-1] / a[:-1].sum() * (1 - ws), [ws]])
        Q = np.zeros((nr, nz + 1))  # volumetric flow across face j (from cell j down to j-1); face 0 -> deadman
        Q[:, 0] = wr * need / dt
        for j in range(1, nz + 1):
            Q[:, j] = Q[:, j - 1] + (dV_ore[:, j - 1] + dV_coke[:, j - 1]) / dt
        # donor-cell (upwind) transfers, sub-cycled to keep the Courant number <= 0.45
        Vb0 = self.s_V.sum(axis=2)
        cfl = float(np.max(Q[:, :nz] * dt / np.maximum(Vb0, 1e-12)))
        n_adv = max(1, int(math.ceil(cfl / 0.45)))
        if n_adv > 50:
            raise ModelEnvelopeError("Burden descent Courant number too large; reduce time step")
        sdt = dt / n_adv
        moved_V_dm = 0.0
        for _ in range(n_adv):
            Vb = self.s_V.sum(axis=2)
            frac = np.clip(Q[:, :nz] * sdt / np.maximum(Vb, 1e-12), 0.0, 0.95)
            mv_n = self.s_n * frac[:, :, None]
            mv_H = self.s_H * frac
            mv_V = self.s_V * frac[:, :, None]
            self.s_n -= mv_n
            self.s_H -= mv_H
            self.s_V -= mv_V
            self.s_n[:, :-1] += mv_n[:, 1:]
            self.s_H[:, :-1] += mv_H[:, 1:]
            self.s_V[:, :-1] += mv_V[:, 1:]
            self.dm_n += mv_n[:, 0].sum(axis=0)
            self.dm_H += float(np.sum(mv_H[:, 0]))
            moved_V_dm += float(np.sum(mv_V[:, 0]))
        self.dm_V = V_dm_cur + moved_V_dm
        A_sec = self.grid.area[None, :] * self.grid.sector_area_frac[:, None]
        A_f = np.concatenate([A_sec[:, :1], A_sec], axis=1)
        self.u_s_faces = Q / np.maximum(A_f, 1e-9)
        self.Q_descent = Q

    def _dripping_height(self) -> float:
        """Height of the contiguous coke-only (ore-free) region above the tuyeres [m] (area-weighted).

        Continuous in the cell ore fractions: h = sum_j dz_j * prod_{i<=j} g(x_i), g(x) = clip(1 - x/0.3, 0, 1),
        so an ore-rich cell blocks everything above it and no threshold jump occurs when a burden
        layer crosses a cell face (a discontinuous definition produced spurious HMT steps)."""
        h = 0.0
        for r in range(self.n_r):
            V = self.s_V[r]
            x = V[:, self.ore_mat_mask].sum(axis=1) / np.maximum(V.sum(axis=1), 1e-12)
            g = np.clip(1.0 - x / 0.3, 0.0, 1.0)
            prod, hr_ = 1.0, 0.0
            for j in range(self.n_z):
                prod *= g[j]
                if prod < 1e-4:
                    break
                hr_ += self.grid.dz[j] * prod
            h += hr_ * self.grid.sector_area_frac[r]
        return float(h)

    def _cz_bottom(self):
        soft = self.soft.mean(axis=0) if hasattr(self, "soft") else np.zeros(self.n_z)
        idx = np.where(soft > 0.97)[0]
        return float(self.grid.z_faces[idx.max() + 1]) if idx.size else 0.0

    # ------------------------------------------------------------------------------------
    def _hydraulics(self, march, rw):
        cfg = self.cfg
        nr, nz = self.n_r, self.n_z
        P_top = self.op["top_pressure_kPa_g"] * 1000.0 + P_ATM
        perm = cfg["calibration"].get("permeability_mult", 1.0)
        dP = np.zeros((nr, nz))
        d_c = self.mat_d[self.i_coke]
        eps_c = self.mat_eps[self.i_coke]
        liq_flux = float(np.sum(self.melt_liq * MM_LIQ)) / max(self.grid.area[0], 1e-9)  # kg/m2/s
        holdup = float(np.clip(0.02 + 0.6 * liq_flux, 0.0, 0.12))
        P = np.full(nr, P_top)
        self.P_faces_sec = np.zeros((nr, nz + 1))
        self.P_faces_sec[:, nz] = P_top
        self.u_g = np.zeros((nr, nz))
        for j in range(nz - 1, -1, -1):
            Fm = 0.5 * (self.F_faces[:, j] + self.F_faces[:, j + 1])
            Tm = 0.5 * (self.Tg_faces[:, j] + self.Tg_faces[:, j + 1])
            y, n, M, rho, Q = gf.gas_props(Fm, Tm, P)
            A = self.grid.area[j] * self.grid.sector_area_frac
            u = Q / A
            mu = gf.viscosity(y, Tm)
            for r in range(nr):
                x_ore, d_ore, eps_ore, _ = self._cell_bed(r, j)
                ho = holdup if self.soft[r, j] > 0.97 else 0.0
                grad = gf.bed_pressure_gradient(u[r], rho[r], mu[r], x_ore, self.soft[r, j], d_ore, d_c, eps_ore, eps_c, ho)
                dP[r, j] = grad * self.grid.dz[j] * perm
            P = P + dP[:, j]
            self.P_faces_sec[:, j] = P
            self.u_g[:, j] = u
            if j == 0:
                bottom = (float(np.mean(u)), float(np.mean(rho)))
            if j == nz - 1:
                top = (float(np.mean(u)), float(np.mean(rho)), float(np.mean(mu)))
        # hydraulic risk PROXIES (diagnostic only, no feedback on the state): Sherwood-type flooding ratio in the
        # dripping zone and the stockline gas velocity relative to minimum fluidisation of 3 mm burden fines
        try:
            u0, rho0 = bottom
            ordn, flux_ratio, flood_ratio = gf.flooding_index(u0, rho0, eps_c, d_c, liq_flux, rho0 * u0)
            ut, rhot, mut = top
            u_mf = float(gf.min_fluidisation_velocity(0.003, 3600.0, rhot, mut))
            self.hyd_flags = {"flooding_ratio": float(flood_ratio), "fluidisation_ratio": ut / max(u_mf, 1e-9),
                              "liquid_flux_kg_m2_s": liq_flux}
        except Exception:      # indicator only: never let a diagnostic stop the model
            self.hyd_flags = {}
        w = self.w_gas
        self.P_faces = self.P_faces_sec.T @ w
        dP_tot = np.sum(dP, axis=1)
        # tuyere / bustle losses
        if rw is not None:
            v_t = rw.diag["tuyere_velocity_m_s"]
            dP_tuy = 0.5 * rw.diag["blast_density_kg_m3"] * v_t**2 * 1.15 + 3.0e3
        else:
            dP_tuy = 0.0
        self.P_blast = float(self.P_faces[0] + dP_tuy)
        self.dP_cells = dP
        self.dP_tuyere = dP_tuy
        self.holdup = holdup
        # gas redistribution between sectors (equal pressure drop), relaxed
        if nr > 1:
            Fs = np.sum(np.where(TRUE_GAS_MASK, self.F_faces[:, 0], 0.0), axis=1)
            R = dP_tot / np.maximum(Fs, 1e-9) ** 1.8
            tgt = R ** (-1 / 1.8)
            tgt = tgt / tgt.sum()
            self.w_gas = 0.75 * self.w_gas + 0.25 * tgt
            self.w_gas /= self.w_gas.sum()

    # ------------------------------------------------------------------------------------
    def _accumulate(self, rec, ch, ext, hr, march):
        c = self.cum
        dt = rec.dt
        w = self.win
        w["t"] += dt
        if ch.get("charged"):
            n_full = ch["dust_n"] + 0.0
            w["charged_n"] = w["charged_n"] + ch["n_full"]
            w["charged_H"] += ch["H"]
            w["dust_n"] = w["dust_n"] + ch["dust_n"]
            w["dust_H"] += ch["dust_H"]
        g = ext.gas.copy()
        g[IDX_GAS["N2"]] += ext.carrier_N2
        w["ext_gas_n"] = w["ext_gas_n"] + g * dt
        w["ext_H"] += ext.H_total * dt
        pci = ext.pci
        pci_F = pci_enthalpy_per_kg(self.cfg["pci"], pci, 298.15) * ext.pci_kg_s * dt
        ash_F = sum(pci["ash_" + ox] * eb.dhf(ox + "_k") for ox in ("SiO2", "Al2O3", "Fe2O3", "CaO", "MgO", "P2O5", "K2O", "Na2O")) * ext.pci_kg_s * dt
        moist_F = pci["H2O"] * eb.dhf("H2O_l") * ext.pci_kg_s * dt
        w["ext_F"] += float(g @ eb.DHF_GAS) * dt + pci_F
        w["pci_F_org"] += pci_F - ash_F - moist_F
        w["pci_h2o_mol"] += pci["H2O"] * ext.pci_kg_s * dt
        w["pci_ash_Fe2O3_mol"] += pci["ash_Fe2O3"] * ext.pci_kg_s * dt
        w["pci_kg"] += ext.pci_kg_s * dt
        w["top_n"] = w["top_n"] + march["top_F"] * dt
        w["top_H"] += march["top_H"] * dt
        w["tap_n"] = w["tap_n"] + hr.tapped * dt
        w["tap_H_hm"] += hr.H_tapped_hm * dt
        w["tap_H_sl"] += hr.H_tapped_sl * dt
        w["Q_shaft"] += march["Q_wall_out"] * dt
        w["Q_hearth"] += rec.terms["hearth_wall_loss_W"] * dt
        w["Q_tuyere"] += rec.terms["tuyere_loss_W"] * dt
        w["dH_inv"] += rec.E_acc
        w["melt_hm_kg"] += float(np.sum(self.last_drip.liq_out[HM_MASK] * MM_LIQ[HM_MASK])) * dt
        for k, v in rec.extents.items():
            w["ext"][k] = w["ext"].get(k, 0.0) + v
        c["el_in"] += rec.el_in
        c["el_out"] += rec.el_out
        c["time_s"] += dt
        if ch.get("charged"):
            for k, v in ch["masses"].items():
                if v > 0:
                    c["in"][k + "_t"] = c["in"].get(k + "_t", 0.0) + v / 1000.0
            c["out"]["dust_t"] = c["out"].get("dust_t", 0.0) + float(np.sum(ch["dust_n"] * MM_SOLID)) / 1000.0
        c["in"]["blast_dry_kNm3"] = c["in"].get("blast_dry_kNm3", 0.0) + ext.blast_dry_Nm3_s * dt / 1000.0
        c["in"]["pci_t"] = c["in"].get("pci_t", 0.0) + ext.pci_kg_s * dt / 1000.0
        c["in"]["oxygen_kNm3"] = c["in"].get("oxygen_kNm3", 0.0) + (ext.blast_dry_Nm3_s * (ext.o2_frac - 0.2095)
                                                                     / max(self.op.get("o2_purity_pct", 99.5) / 100 - 0.2095, 1e-6)) * dt / 1000.0
        m_hm = float(np.sum(hr.tapped[HM_MASK] * MM_LIQ[HM_MASK])) * dt / 1000.0
        m_sl = float(np.sum(hr.tapped[SLAG_MASK] * MM_LIQ[SLAG_MASK])) * dt / 1000.0
        c["out"]["hot_metal_t"] = c["out"].get("hot_metal_t", 0.0) + m_hm
        c["out"]["slag_t"] = c["out"].get("slag_t", 0.0) + m_sl
        c.setdefault("tapped_n", np.zeros(len(LIQ)))
        c["tapped_n"] = c["tapped_n"] + hr.tapped * dt
        c.setdefault("topgas_n", np.zeros(len(GAS)))
        c["topgas_n"] = c["topgas_n"] + march["top_F"] * dt
        c.setdefault("melt_hm_t", 0.0)
        prod_hm = float(np.sum(self.last_drip.liq_out[HM_MASK] * MM_LIQ[HM_MASK])) * dt / 1000.0
        c["melt_hm_t"] += prod_hm

    # ------------------------------------------------------------------------------------
    # convenience runners
    # ------------------------------------------------------------------------------------
    def run(self, hours: float, dt: Optional[float] = None, callback=None):
        dt = dt or self.dt
        n = int(round(hours * 3600.0 / dt))
        recs = []
        for _ in range(n):
            rec = self.step(dt)
            recs.append(rec)
            if callback:
                callback(self, rec)
        return recs

    def warm_start(self, hours: float = 36.0, coke_excess: float = 1.10, dt: float = 300.0, ramp_frac: float = 0.5):
        """Blow-in style initialisation: start with excess coke and ramp to the nominal batch."""
        nominal = self.op["coke_batch_t"]
        n = int(round(hours * 3600.0 / dt))
        for i in range(n):
            frac = i / max(n - 1, 1)
            mult = coke_excess if frac < (1 - ramp_frac) else coke_excess + (1.0 - coke_excess) * (frac - (1 - ramp_frac)) / ramp_frac
            self.op["coke_batch_t"] = nominal * mult
            self.step(dt)
        self.op["coke_batch_t"] = nominal

    def clone(self) -> "BlastFurnaceModel":
        return copy.deepcopy(self)
