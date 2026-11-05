"""Thermal/chemistry forecasts with intervals, model registry and ML diagnostics."""
from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends

from ...core.config import MODEL_VERSION
from ...core.errors import DomainError
from ...data.synthetic import iso
from ...ml.features import live_row
from ...ml.training import train_all
from ...mpc.verifier import trust_gate
from ...physics import outputs as out
from ...schemas import ForecastRequest, ForecastResponse, TrainRequest
from ...services.jobs import _json_safe
from ...services.twin_service import DEMO_DIR
from .deps import engineer, job_links, ready_svc, svc, viewer

router = APIRouter()
TARGET_META = {"hmt": ("hot_metal_temperature", "hmt_C", "degC", 4), "si": ("hot_metal_silicon", "hm_si_pct", "wt%", 5)}


def _phys_band(meta: dict, horizon_h: float, sd_bias: float, target: str) -> float:
    """95 % half-width for physics(+observer): test-set RMSE of the physics baseline when a trained
    model exists for this horizon, else an illustrative growth law (declared in the response)."""
    try:
        rmse = meta["metrics"]["test"]["physics_only"]["RMSE"]
        if rmse:
            return 1.96 * float(rmse)
    except Exception:
        pass
    growth = 2.5 if target == "hmt" else 0.015
    return 1.96 * float(np.sqrt(sd_bias ** 2 + (growth * horizon_h) ** 2))


@router.post("/forecast", response_model=ForecastResponse, tags=["forecasts"],
             summary="HMT and Si forecasts: physics twin (open loop at current setpoints) + observer + hybrid ML")
def forecast(body: ForecastRequest, s=Depends(ready_svc), p=Depends(viewer)):
    warnings = []
    with s.lock:
        twin = s.twin.clone()
        theta = s.ekf.theta.copy()
        P = s.ekf.P.copy()
        df = s._history_frame(hours=14)
        ctx = s.mpc_context()
        models = dict(s.ml_models)
        metas = dict(s.ml_meta)
        mode = s.mode
        status = s.status()
    bal = out.balances(twin)["step"]
    gate = trust_gate(ctx)
    hz_h = [h / 60.0 for h in body.horizons_min]
    dt = 300.0
    t_now = 0.0
    traj = {}
    try:
        for h in hz_h:
            while t_now < h * 3600 - 1e-6:
                twin.step(dt)
                t_now += dt
            k = out.kpis(twin)
            traj[h] = {"hmt_C": k["hmt_C"]["value"], "hm_si_pct": k["hm_si_pct"]["value"]}
        phys_ok = True
    except Exception as e:
        phys_ok = False
        warnings.append(f"physics forecast left the model envelope: {e}")
    out_h = []
    for h in hz_h:
        preds = {}
        for tgt in body.targets:
            name, key, unit, ib = TARGET_META[tgt]
            bias = float(theta[ib])
            sd_bias = float(np.sqrt(P[ib, ib]))
            mkey = f"{tgt}@{float(h)}"
            meta = metas.get(mkey, {})
            if not phys_ok or traj.get(h, {}).get(key) is None:
                preds[name] = {"value": None, "unit": unit, "lower95": None, "upper95": None, "status": "FAILED",
                               "reason": "physics forecast not available"}
                continue
            phys_f = traj[h][key] + bias
            band = _phys_band(meta, h, sd_bias, tgt)
            item = {"value": phys_f, "unit": unit, "lower95": phys_f - band, "upper95": phys_f + band,
                    "status": "PHYSICS_PLUS_OBSERVER", "physics_forecast": traj[h][key], "observer_bias": bias,
                    "method": "physics twin clone run open-loop at current setpoints + EKF bias state",
                    "interval_method": "test RMSE of physics baseline (synthetic)" if meta.get("metrics") else
                                       "illustrative growth law sqrt(sd_bias^2 + (g*h)^2)"}
            hf = models.get(mkey)
            if hf is not None and len(df) >= 30:
                try:
                    X, phys_now = live_row(df, tgt)
                    r = hf.predict(X, phys_now)
                    item["hybrid"] = {"value": float(r["prediction"][0]), "lower95": float(r["lower95"][0]),
                                      "upper95": float(r["upper95"][0]), "correction": float(r["correction"][0]),
                                      "ood": bool(r["ood"][0]), "model_id": meta.get("model_id"), "scope": meta.get("scope"),
                                      "approval": meta.get("approval_status"),
                                      "basis": "physics(now) + bounded ML residual -> measurement at t+h"}
                    promoted = bool(meta.get("beats_baselines_on_test"))
                    item["hybrid"]["promoted"] = promoted
                    if r["ood"][0]:
                        item["status"] = "OOD_FALLBACK_TO_PHYSICS"
                        warnings.append(f"{mkey}: features outside training domain - ML correction disabled")
                    elif promoted:
                        item.update({"value": float(r["prediction"][0]), "lower95": float(r["lower95"][0]),
                                     "upper95": float(r["upper95"][0]), "status": "HYBRID"})
                    else:
                        # governance: an ML model that did not beat the physics and persistence baselines on the
                        # held-out test block is shown in shadow only and never replaces the physics forecast
                        item["status"] = "PHYSICS_PLUS_OBSERVER_ML_SHADOW"
                except Exception as e:
                    warnings.append(f"{mkey}: hybrid model failed ({type(e).__name__}) - physics only")
            elif hf is None:
                item["hybrid"] = {"status": "NO_TRAINED_MODEL_FOR_HORIZON"}
            preds[name] = item
        out_h.append({"horizon_minutes": int(round(h * 60)), "predictions": preds})
    if any(m.get("scope") == "DEMO-SYNTHETIC" for m in metas.values()):
        warnings.append("ML models are DEMO-SYNTHETIC (trained on simulator data) - NOT approved for plant use")
    if status["data_origin"] in ("SYNTHETIC", "SYNTHETIC_OR_LIVE"):
        warnings.append("Synthetic demonstration data - not a physical furnace")
    resp = {"run_id": uuid.uuid4().hex, "mode": mode, "model_version": MODEL_VERSION, "data_origin": status["data_origin"],
            "timestamp_utc": status["data_time_utc"], "horizons": out_h,
            "balance_quality": {"status": bal["status"], "max_element_residual_pct":
                                max(abs(r["residual_pct_of_throughput"]) for r in bal["elements"]) if bal["elements"] else None,
                                "energy_residual_pct_of_basis": bal["energy"]["residual_pct_of_basis"]},
            "recommendation_eligible": bool(gate["passed"]), "trust_gate": gate, "warnings": warnings}
    return _json_safe(resp)


@router.get("/forecasts/live", tags=["forecasts"], summary="Latest cyclic hybrid forecasts (computed every 8 twin steps)")
def forecasts_live(s=Depends(ready_svc), p=Depends(viewer)):
    return s.forecasts or {"items": {}, "note": "no forecast computed yet"}


@router.get("/models/registry", tags=["models"])
def registry(s=Depends(svc), p=Depends(viewer)):
    items = []
    for m in s.registry.list():
        items.append({k: m.get(k) for k in ("model_id", "target", "horizon_h", "algorithm", "hybrid_form", "scope",
                                             "approval_status", "data_origin", "dataset_rows", "dataset_hash", "created_utc",
                                             "seed", "packages", "beats_baselines_on_test", "label", "correction_bound",
                                             "correction_shrinkage_alpha", "physics_base")})
    return {"models": items, "loaded": sorted(s.ml_models),
            "policy": "Models trained on synthetic data are labelled DEMO-SYNTHETIC and are never approved for plant use. "
                      "A model replaces the physics + observer forecast only if it beats the physics and persistence "
                      "baselines on the held-out test block; otherwise it runs in shadow."}


@router.get("/models/diagnostics", tags=["models"], summary="Chronological test metrics vs baselines, coverage, drift, importance")
def diagnostics(s=Depends(svc), p=Depends(viewer)):
    out_ = []
    for m in s.registry.list():
        out_.append({k: m.get(k) for k in ("model_id", "target", "horizon_h", "metrics", "split", "beats_baselines_on_test",
                                             "feature_importance", "drift", "ood_threshold_mahalanobis",
                                             "interval_half_width_95", "scope", "approval_status", "label")})
    live = None
    try:
        df = s.history_frame(hours=24)
        if not df.empty and s.ml_models:
            from ...ml.drift import drift_report
            from ...ml.features import feature_frame, model_columns
            X = feature_frame(df)
            hf = next(iter(s.ml_models.values()))
            cols = [c for c in hf.cols if c in X.columns and not c.startswith("age_")][:15]
            ref = pd.DataFrame([hf.mu], columns=hf.cols)[cols]
            live = {"rows": int(len(X)), "feature_means_vs_training": {c: {"live": float(np.nanmean(X[c])), "train": float(ref[c].iloc[0])}
                                                                        for c in cols}}
    except Exception as e:
        live = {"error": str(e)[:200]}
    return _json_safe({"models": out_, "live_feature_check": live})


@router.post("/models/train", status_code=202, tags=["models"], summary="Retrain hybrid models (chronological split) as a job")
def train(body: TrainRequest, p=Depends(engineer), s=Depends(ready_svc)):
    if body.source == "shipped_history":
        path = DEMO_DIR / "history.csv.gz"
        if not path.exists():
            raise DomainError("NO_TRAINING_DATA", "shipped history not found", 409)
        df = pd.read_csv(path)
        origin = "SYNTHETIC"
    else:
        df = s.history_frame(hours=48)
        origin = "SYNTHETIC" if s.mode in ("DEMO_SIMULATION", "ADVISORY_SHADOW_MPC") else "SITE"
        if len(df) < 600:
            raise DomainError("INSUFFICIENT_DATA", f"only {len(df)} rows in the live buffer (need >= 600)", 409)

    def job(cb):
        cb(0.05, "building features")
        res = train_all(df, s.registry, origin)
        s.reload_ml()
        return {"status": "SUCCEEDED", "models": res}
    j = s.jobs.submit("models:train", body.model_dump(), job, p.sub)
    s.audit(p.sub, p.role, "MODEL_TRAINING", j["id"], body.model_dump())
    return {**j, "links": job_links(j["id"])}
