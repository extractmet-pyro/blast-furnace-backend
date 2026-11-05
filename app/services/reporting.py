"""Report assembly (balance / energy / carbon / economics) and exports with provenance.

Every report carries: generated UTC, data time, config version + SHA-256, model version, data
origin label and units. Exports (CSV/XLSX/JSON) repeat that header so a file taken out of the
system can always be traced back to the configuration and model revision that produced it."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

from ..core.config import MODEL_VERSION
from ..data.synthetic import iso
from ..physics import outputs as out
from ..physics.species import ELEMENTS
from .economics import carbon, economics


def provenance(svc) -> Dict[str, object]:
    from .twin_service import cfg_hash
    return {"generated_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "data_time_utc": iso(svc.data_t), "config_version": svc.config_version, "config_sha256": cfg_hash(svc.cfg),
            "model_version": MODEL_VERSION, "mode": svc.mode, "data_origin": "SYNTHETIC" if svc.mode != "LIVE_MONITOR_ONLY" else "LIVE_EDGE",
            "plant": svc.cfg["plant"]["name"],
            "label": "Model-computed values (physics twin with illustrative calibration) - not plant measurements"}


def balance_report(svc) -> dict:
    m = svc.twin
    bal = out.balances(m)
    hb = out.heat_balance(m)
    st = out.streams(m)
    hist = list(svc.twin_hist)[-360:]
    res_hist = {"t_utc": [r["t_utc"] for r in hist], "max_rel_element_residual": [r.get("el_res_max") for r in hist],
                "energy_residual_pct": [r.get("E_res_pct") for r in hist]}
    cum = bal["cumulative_elements_kg"]
    closure = []
    for e in ELEMENTS:
        c = cum[e]
        closure.append({"element": e, "cum_in_kg": c["in"], "cum_out_kg": c["out"]})
    return {"provenance": provenance(svc), "step": bal["step"], "tolerances": bal["tolerances"],
            "cumulative_elements_kg": closure, "conventional_heat_balance": hb, "streams": st,
            "residual_history": res_hist,
            "boundaries": {"system": "furnace envelope: stockline to hearth pad, shell outer surface",
                           "inputs": "charged burden/coke/flux (as charged, wet), hot blast incl. O2 and moisture, PCI/NG/H2 at injection T",
                           "outputs": "top gas at stockline, dust (charge fines + char), tapped hot metal and slag, heat losses (shell, staves, tuyeres, hearth)",
                           "accumulation": "solid, gas-held-up (quasi-steady), deadman, hearth liquid and refractory enthalpy",
                           "reference": "absolute enthalpy (formation at 298.15 K + sensible); Nm3 at 0 degC/101.325 kPa"}}


def energy_report(svc) -> dict:
    m = svc.twin
    hb = out.heat_balance(m)
    k = out.kpis(m)
    sp = out.specific(m, 4.0)
    econ = economics(m)
    walls = k.get("_walls_MW", {})
    links = []
    for r in hb.get("rows", []):
        if r["side"] == "supply":
            links.append({"source": r["item"], "target": "Blast furnace", "value_GJ_tHM": r["GJ_per_tHM"]})
        elif r["side"] == "demand":
            links.append({"source": "Blast furnace", "target": r["item"], "value_GJ_tHM": r["GJ_per_tHM"]})
    return {"provenance": provenance(svc), "heat_balance": hb, "sankey_links": links,
            "wall_losses_MW": walls, "heat_loss_total_MW": k["heat_loss_total_MW"], "heat_loss_GJ_tHM": k["heat_loss_GJ_tHM"],
            "top_gas": out.top_gas(m), "top_gas_GJ_tHM": econ["top_gas_GJ_tHM"], "stove_GJ_tHM": econ["stove_GJ_tHM"],
            "specific": sp, "energy_intensity": carbon(m)["energy_GJ_tHM"]}


def carbon_report(svc, boundary: str = None) -> dict:
    return {"provenance": provenance(svc), **carbon(svc.twin, boundary)}


def economics_report(svc) -> dict:
    return {"provenance": provenance(svc), **economics(svc.twin)}


def kpi_frame(svc, hours: float = 24) -> pd.DataFrame:
    n = int(hours * 3600 / svc.twin.dt)
    return pd.DataFrame(list(svc.twin_hist)[-n:])


def telemetry_frame(svc, hours: float = 24, tags: List[str] = None) -> pd.DataFrame:
    n = int(hours * 3600 / svc.twin.dt)
    df = pd.DataFrame(list(svc.telemetry)[-n:])
    if tags and not df.empty:
        cols = ["t_s", "t_utc"] + [c for c in df.columns if c.split("__")[0] in tags]
        df = df[[c for c in cols if c in df.columns]]
    return df


def balance_frames(svc) -> Dict[str, pd.DataFrame]:
    rep = balance_report(svc)
    el = pd.DataFrame(rep["step"]["elements"])
    st = rep["streams"]
    rows = []
    for s in st["inputs"]:
        rows.append({"direction": "IN", "stream": s["stream"], **{k: v for k, v in s.items() if isinstance(v, (int, float))}})
    for s in st["outputs"]:
        rows.append({"direction": "OUT", "stream": s["stream"], **{k: v for k, v in s.items() if isinstance(v, (int, float))}})
    comp = []
    for s in st["inputs"] + st["outputs"]:
        for key in ("chem_wt_pct", "wt_pct", "vol_pct_dry", "vol_pct_wet"):
            for sp_, v in (s.get(key) or {}).items():
                if isinstance(v, (int, float)):
                    comp.append({"stream": s["stream"], "basis": key, "component": sp_, "value": v})
    hb_rows = rep["conventional_heat_balance"].get("rows", [])
    return {"elements_step": el, "streams": pd.DataFrame(rows), "compositions": pd.DataFrame(comp),
            "heat_balance": pd.DataFrame(hb_rows), "cumulative": pd.DataFrame(rep["cumulative_elements_kg"])}


def downsample(rows: List[dict], max_points: int) -> List[dict]:
    if len(rows) <= max_points:
        return rows
    idx = np.linspace(0, len(rows) - 1, max_points).round().astype(int)
    # keep rows carrying laboratory values (sparse) in addition to the stride
    keep = set(idx.tolist())
    for i, r in enumerate(rows):
        if any(k in r and r[k] is not None for k in ("HMT_DIP", "HM_SI", "HM_S", "SLAG_B2")):
            keep.add(i)
    return [rows[i] for i in sorted(keep)]
