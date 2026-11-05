"""Reproduce the documentation figures, state tables and the example output dataset.

    python scripts/make_doc_figures.py            # writes docs/figures/* and data/examples/*

Every number comes from the physics twin started from the shipped initial states (SYNTHETIC data,
illustrative calibration). Charts follow the desk conventions: one y-scale per panel (small multiples),
baseline dashed grey, case in the first categorical colour, legend on every multi-series chart.
"""
from __future__ import annotations

import copy
import csv
import gzip
import hashlib
import json
import pickle
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.physics import outputs as out  # noqa: E402
from app.physics.defaults import PRESETS, build_config  # noqa: E402
from app.physics.zones import volumes  # noqa: E402
from app.services import scenarios as scn  # noqa: E402
from app.services.twin_service import MODEL_VERSION, physics_hash  # noqa: E402

FIG = ROOT / "docs" / "figures"
EX = ROOT / "data" / "examples"
FIG.mkdir(parents=True, exist_ok=True)
EX.mkdir(parents=True, exist_ok=True)

# validated categorical palette (light surface) - same order as the frontend
S1, S2, S3, S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
INK, INK2, GRID, SURF = "#1d2327", "#4a535b", "#d5dade", "#ffffff"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.5, "axes.edgecolor": "#9aa3aa", "axes.labelcolor": INK2,
                     "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
                     "axes.spines.top": False, "axes.spines.right": False, "figure.facecolor": SURF, "axes.facecolor": SURF,
                     "axes.titlesize": 10, "axes.titleweight": "bold", "axes.titlecolor": INK, "legend.frameon": False})
STAMP = "SYNTHETIC - physics twin, illustrative calibration (Industry-X demo). Not plant data."
NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state(preset: str):
    with gzip.open(ROOT / "data" / "demo" / f"initial_state_{preset}.pkl.gz", "rb") as f:
        m = pickle.load(f)
    cfg = build_config(preset)
    for k in ("targets", "constraints", "economics", "carbon", "equipment_limits", "rate_limits_per_15min", "plant"):
        m.cfg[k] = copy.deepcopy(cfg[k])
    return m


def scenario_figure(res: dict, kpis, labels, fname: str, title: str):
    B, K = res["runs"]["baseline"]["trajectory"], res["runs"]["case"]["trajectory"]
    changes = [c["at_h"] for c in res["scenario"].get("changes", [])]
    n = len(kpis)
    cols = 3
    rows = int(np.ceil(n / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(11, 2.55 * rows + 0.8), sharex=True)
    for ax, k, lab in zip(axes.flat, kpis, labels):
        ax.plot(B["t_h"], B[k], color="#8a949c", lw=1.6, ls="--", label="Baseline (no change)")
        ax.plot(K["t_h"], K[k], color=S1, lw=2.0, label="Case")
        for c in changes:
            ax.axvline(c, color="#8a949c", lw=0.8, ls=":")
        ax.set_title(lab, loc="left")
    for ax in list(axes.flat)[n:]:
        ax.axis("off")
    for ax in axes[-1]:
        ax.set_xlabel("Hours from start")
    axes.flat[0].legend(loc="best", fontsize=8.5)
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.text(0.01, 0.005, STAMP + f"  Input hash {res['input_hash'][:12]}.", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.02, 1, 0.97))
    fig.savefig(FIG / fname, dpi=170)
    plt.close(fig)


CFG_NOTE = "shipped default of preset large_4000 (configuration version 1)"


def write_traj_csv(res: dict, path: Path):
    B, K = res["runs"]["baseline"]["trajectory"], res["runs"]["case"]["trajectory"]
    keys = [k for k in K if k != "t_h"]
    with path.open("w", newline="") as f:
        f.write(f"# data_origin=SYNTHETIC; scenario={res['scenario'].get('id')}; input_hash={res['input_hash']}; "
                f"model_version={MODEL_VERSION}; generated_utc={NOW}\n")
        f.write(f"# configuration: {CFG_NOTE}; physics_sha256={physics_hash(build_config('large_4000'))}\n")
        f.write("# units are in the column suffix (_C degC, _pct %, _t_d t/d, _kg_tHM kg/tHM, _kPa kPa, _m m)\n")
        w = csv.writer(f)
        w.writerow(["t_h"] + [f"baseline.{k}" for k in keys] + [f"case.{k}" for k in keys])
        for i, t in enumerate(B["t_h"]):
            w.writerow([t] + [B[k][i] for k in keys] + [K[k][i] if i < len(K[k]) else "" for k in keys])


def streams_csv(m, path: Path, label: str):
    st = out.streams(m)
    with path.open("w", newline="") as f:
        f.write(f"# {label}; data_origin=SYNTHETIC; model_version={MODEL_VERSION}; generated_utc={NOW}; basis: 4-h window\n")
        f.write(f"# configuration: {CFG_NOTE}; physics_sha256={physics_hash(m.cfg)}\n")
        w = csv.writer(f)
        w.writerow(["direction", "stream", "t_per_h_or_kNm3_per_h", "kg_per_tHM_or_Nm3_per_tHM", "T_C", "component", "value", "basis"])
        for direction in ("inputs", "outputs"):
            for s in st[direction]:
                for basis in ("wt_pct", "chem_wt_pct", "vol_pct_dry", "vol_pct"):
                    for comp, v in (s.get(basis) or {}).items():
                        w.writerow([direction, s["stream"], s.get("t_h", s.get("kNm3_h")), s.get("kg_tHM", s.get("Nm3_tHM")), s.get("T_C"), comp, v, basis])
    return st


def main():
    # ---------------------------------------------------------------- preset state table
    rows = []
    keys = [("production_t_d", "Production", "t/d"), ("productivity_t_m3_d", "Productivity", "t/m3/d"),
            ("hmt_C", "Hot metal temperature", "degC"), ("hm_si_pct", "Hot metal Si", "wt%"), ("hm_c_pct", "Hot metal C", "wt%"), ("hm_s_pct", "Hot metal S", "wt%"),
            ("coke_rate_kg_tHM", "Coke rate", "kg/tHM"), ("pci_rate_kg_tHM", "PCI rate", "kg/tHM"), ("fuel_rate_kg_tHM", "Fuel rate", "kg/tHM"),
            ("slag_rate_kg_tHM", "Slag rate", "kg/tHM"), ("slag_B2", "Slag basicity B2", "-"), ("raft_C", "RAFT", "degC"), ("eta_CO_pct", "Top gas eta CO", "%"),
            ("top_gas_T_C", "Top gas temperature (instantaneous)", "degC"), ("pressure_drop_kPa", "Bed pressure drop", "kPa"), ("blast_volume_Nm3_min", "Blast volume", "Nm3/min"),
            ("blast_temp_C", "Hot blast temperature", "degC"), ("o2_enrichment_pct", "Oxygen enrichment", "%"), ("heat_loss_total_MW", "Wall + cooling heat loss", "MW"),
            ("dripping_zone_height_m", "Dripping-zone height", "m"), ("thermal_reserve_T_C", "Thermal reserve zone T", "degC"), ("solution_loss_kgC_tHM", "Solution loss", "kgC/tHM")]
    states = {}
    for p in PRESETS:
        m = load_state(p)
        states[p] = m
        k = out.kpis(m)
        rows.append({"preset": p, **{kk: (k[kk]["value"] if kk in k and isinstance(k[kk], dict) else None) for kk, _, _ in keys}})
        rows[-1]["working_volume_m3"] = volumes(PRESETS[p]["geometry"])["working_volume_m3"]
    keys = [("working_volume_m3", "Working volume", "m3")] + keys
    with (EX / "preset_state_table.csv").open("w", newline="") as f:
        f.write(f"# Quasi-steady states of the four illustrative presets after the shipped warm start; SYNTHETIC; model {MODEL_VERSION}; {NOW}\n")
        w = csv.writer(f)
        w.writerow(["quantity", "unit"] + list(PRESETS))
        for kk, lab, u in keys:
            w.writerow([lab, u] + [r[kk] for r in rows])
    md = ["| Quantity | Unit | " + " | ".join(PRESETS[p]["label"] for p in PRESETS) + " |", "|---|---|" + "---|" * len(PRESETS)]
    for kk, lab, u in keys:
        vals = []
        for r in rows:
            v = r[kk]
            vals.append("-" if v is None else (f"{v:,.0f}" if abs(v) >= 100 else f"{v:.2f}" if abs(v) >= 1 else f"{v:.3f}"))
        md.append(f"| {lab} | {u} | " + " | ".join(vals) + " |")
    (FIG / "preset_state_table.md").write_text("\n".join(md) + "\n")

    # ---------------------------------------------------------------- axial profiles (large)
    m = states["large_4000"]
    pr = out.profiles(m)
    z = np.array(pr["z_m"])
    fig, axes = plt.subplots(1, 4, figsize=(11, 4.6), sharey=True)
    axes[0].plot(pr["T_gas_C"], z, color=S2, lw=2, label="Gas")
    axes[0].plot(pr["T_solid_C"], z, color=S1, lw=2, label="Solids")
    axes[0].set_title("Temperature (degC)", loc="left"); axes[0].legend(fontsize=8.5); axes[0].set_ylabel("Height above tuyeres (m)")
    axes[1].plot(np.array(pr["reduction_degree_pct"]), z, color=S1, lw=2)
    axes[1].set_title("Reduction degree (%)", loc="left")
    axes[2].plot(pr["y_CO_pct"], z, color=S1, lw=2, label="CO"); axes[2].plot(pr["y_CO2_pct"], z, color=S2, lw=2, label="CO2")
    axes[2].plot(pr["y_H2_pct"], z, color=S3, lw=2, label="H2"); axes[2].set_title("Gas (vol %)", loc="left"); axes[2].legend(fontsize=8.5)
    axes[3].plot(pr["pressure_kPa_g"], pr["z_faces_m"], color=S1, lw=2)
    axes[3].set_title("Pressure (kPa g)", loc="left")
    cz = out.cohesive_zone(m)["sectors"][0]
    for ax in axes:
        for zz in (cz.get("z_top_m"), cz.get("z_bottom_m")):
            if zz is not None:
                ax.axhline(zz, color="#8a949c", lw=0.8, ls=":")
    fig.suptitle("Axial state of the large (~4000 m3) preset - cohesive-zone limits dotted", x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.text(0.01, 0.01, STAMP, fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(FIG / "profiles_large.png", dpi=170)
    plt.close(fig)

    # ---------------------------------------------------------------- inputs/outputs table (large)
    st = streams_csv(m, EX / "streams_large_4000.csv", "Input and output streams, large preset")

    # ---------------------------------------------------------------- scenarios
    specs = {s["id"]: s for s in scn.LIBRARY}
    plan = [("S03", ["hmt_C", "hm_si_pct", "raft_C", "production_t_d", "fuel_rate_kg_tHM", "eta_CO_pct"],
             ["Hot metal temperature (degC)", "Hot metal Si (wt%)", "RAFT (degC)", "Production (t/d)", "Fuel rate (kg/tHM)", "Top gas eta CO (%)"]),
            ("S14", ["hmt_C", "hm_si_pct", "thermal_reserve_T_C", "production_t_d", "fuel_rate_kg_tHM", "dripping_zone_height_m"],
             ["Hot metal temperature (degC)", "Hot metal Si (wt%)", "Thermal reserve zone (degC)", "Production (t/d)", "Fuel rate (kg/tHM)", "Dripping-zone height (m)"]),
            ("S02b", ["hmt_C", "raft_C", "coke_rate_kg_tHM", "pci_rate_kg_tHM", "production_t_d", "pressure_drop_kPa"],
             ["Hot metal temperature (degC)", "RAFT (degC)", "Coke rate (kg/tHM)", "PCI rate (kg/tHM)", "Production (t/d)", "Bed pressure drop (kPa)"])]
    summary = []
    for sid, ks, labels in plan:
        res = scn.run_simulation(states["large_4000"], copy.deepcopy(specs[sid]), dt=240.0)
        title = f"Scenario {sid}: {specs[sid]['name']} (large preset)"
        avail = [k for k in ks if k in res["runs"]["case"]["trajectory"]]
        scenario_figure(res, avail, [labels[ks.index(k)] for k in avail], f"scenario_{sid}.png", title)
        write_traj_csv(res, EX / f"scenario_{sid}_trajectories.csv")
        summary.append({"id": sid, "name": specs[sid]["name"], "status": res["runs"]["case"]["status"], "input_hash": res["input_hash"],
                        "max_rel_element_residual": res["runs"]["case"]["max_rel_element_residual"],
                        "max_rel_energy_residual": res["runs"]["case"]["max_rel_energy_residual"],
                        "delta_final": {k: res["delta_final"].get(k) for k in ("hmt_C", "hm_si_pct", "production_t_d", "fuel_rate_kg_tHM", "raft_C", "eta_CO_pct", "pressure_drop_kPa")}})
        print(sid, "done", {k: round(v, 3) for k, v in summary[-1]["delta_final"].items() if v is not None})
    (EX / "scenario_summary.json").write_text(json.dumps({"data_origin": "SYNTHETIC", "model_version": MODEL_VERSION, "generated_utc": NOW,
                                                          "physics_sha256": physics_hash(build_config("large_4000")), "scenarios": summary}, indent=1))
    print("figures:", sorted(p.name for p in FIG.iterdir()))


if __name__ == "__main__":
    main()
