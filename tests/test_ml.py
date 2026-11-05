"""ML: causality (no future leakage), chronological split with embargo, reproducibility, baselines,
interval coverage, OOD fail-safe, drift detection, provenance/approval labels."""
import numpy as np
import pandas as pd
import pytest

from app.ml.drift import drift_report
from app.ml.features import TOLERANCE_S, build_features, chronological_split, feature_frame, target_series
from app.ml.hybrid import HybridForecaster
from app.ml.registry import ModelRegistry
from app.ml.training import train_target


def synthetic_frame(n=2500, seed=3):
    """Toy history with the live-frame layout: MVs, measurements, physics columns, lab results at
    receipt time with sampling-time columns. True HMT = physics + 6 + 0.05 * (BL_TEMP - 1180) + noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) * 120.0
    bt = 1180 + np.cumsum(rng.normal(0, 0.6, n))
    phys = 1500 + 0.1 * (bt - 1180) + rng.normal(0, 2, n)
    df = pd.DataFrame({"t_s": t, "t_utc": pd.to_datetime(t, unit="s", origin="2026-10-01").strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "BL_TEMP": bt, "BL_FLOW": 6600 + rng.normal(0, 10, n), "BL_MOIST": 20 + rng.normal(0, 0.5, n),
                       "BL_O2": 4.5 + rng.normal(0, 0.02, n), "PCI_RATE": 70 + rng.normal(0, 0.5, n), "COKE_BATCH": 22.9,
                       "TG_CO": 23 + rng.normal(0, 0.2, n), "TG_CO2": 22 + rng.normal(0, 0.2, n), "TG_TEMP": 160 + rng.normal(0, 5, n),
                       "phys_hmt_C": phys, "phys_si_pct": 0.4 + 0.002 * (bt - 1180)})
    true = phys + 6 + 0.05 * (bt - 1180)
    df["HMT_DIP"] = np.nan
    df["HMT_DIP__t_sample"] = np.nan
    for i in range(0, n, 8):                      # dip every 16 min, received 2 min after sampling
        if i + 1 < n:
            df.loc[i + 1, "HMT_DIP"] = true[i] + rng.normal(0, 3)
            df.loc[i + 1, "HMT_DIP__t_sample"] = t[i]
    return df


def test_features_are_causal():
    df = synthetic_frame(1500)
    X1 = feature_frame(df)
    df2 = df.copy()
    k = 900
    num = df2.select_dtypes("number").columns.drop("t_s")
    df2.loc[k + 1:, num] = df2.loc[k + 1:, num] * 1.37 + 11.0      # alter the FUTURE only
    X2 = feature_frame(df2)
    pd.testing.assert_frame_equal(X1.iloc[: k + 1], X2.iloc[: k + 1])


def test_lab_values_used_at_receipt_time_only():
    df = synthetic_frame(200)
    X = feature_frame(df)
    first_rx = df.index[df["HMT_DIP"].notna()][0]
    assert X["last_HMT_DIP"].iloc[:first_rx].isna().all()
    assert np.isfinite(X["last_HMT_DIP"].iloc[first_rx])


def test_target_aligned_by_sampling_time():
    df = synthetic_frame(400)
    y = target_series(df, 1.0, "hmt")
    i = int(np.where(y.notna())[0][0])
    t_target = df["t_s"].iloc[i] + 3600
    samp = df["HMT_DIP__t_sample"].dropna()
    assert np.min(np.abs(samp - t_target)) <= TOLERANCE_S


def test_chronological_split_with_embargo():
    sp = chronological_split(1000, embargo=50)
    assert sp["train"].max() < sp["val"].min() and sp["val"].max() < sp["test"].min()
    assert sp["val"].min() - sp["train"].max() > 50


def test_training_reproducible_and_compared_with_baselines(tmp_path):
    df = synthetic_frame(2500)
    r1 = train_target(df, "hmt", 1.0, ModelRegistry(tmp_path / "a"), "SYNTHETIC", seed=11)
    r2 = train_target(df, "hmt", 1.0, ModelRegistry(tmp_path / "b"), "SYNTHETIC", seed=11)
    assert r1["status"] == "TRAINED"
    t1, t2 = r1["metrics"]["test"], r2["metrics"]["test"]
    assert t1["hybrid"]["RMSE"] == pytest.approx(t2["hybrid"]["RMSE"], rel=1e-9)
    for b in ("physics_only", "persistence", "arx_ridge"):
        assert t1[b]["RMSE"] is not None
    assert t1["hybrid"]["RMSE"] < t1["physics_only"]["RMSE"]          # learns the known residual structure
    assert 0.0 <= t1["interval95_coverage"] <= 1.0 and t1["interval95_coverage"] > 0.8


def test_provenance_and_approval_labels(tmp_path):
    reg = ModelRegistry(tmp_path / "r")
    r = train_target(synthetic_frame(2000), "hmt", 1.0, reg, "SYNTHETIC", seed=5)
    meta = reg.list()[0]
    assert meta["scope"] == "DEMO-SYNTHETIC" and meta["approval_status"].startswith("NOT_APPROVED")
    for k in ("dataset_hash", "seed", "packages", "created_utc", "split"):
        assert k in meta


def test_ood_falls_back_to_physics():
    df = synthetic_frame(3000)
    X, y, phys = build_features(df, 1.0, "hmt")
    hf = HybridForecaster("hmt", 1.0, 40.0, seed=1).fit(X.iloc[:2000], y.iloc[:2000], phys.iloc[:2000])
    hf.calibrate(X.iloc[2000:2400], y.iloc[2000:2400], phys.iloc[2000:2400])
    Xo = X.iloc[[-1]].copy() * 50.0                     # far outside the training domain
    out = hf.predict(Xo, phys.iloc[[-1]])
    assert bool(out["ood"][0]) and out["prediction"][0] == pytest.approx(float(phys.iloc[-1]))


def test_correction_is_bounded():
    df = synthetic_frame(3000)
    X, y, phys = build_features(df, 1.0, "hmt")
    hf = HybridForecaster("hmt", 1.0, 5.0, seed=1).fit(X, y + 300.0, phys)    # absurd residuals
    out = hf.predict(X.iloc[:50], phys.iloc[:50])
    assert np.all(np.abs(out["correction"]) <= 5.0 + 1e-9)


def test_drift_detection():
    rng = np.random.default_rng(0)
    a = pd.DataFrame({"x": rng.normal(0, 1, 3000)})
    b = pd.DataFrame({"x": rng.normal(1.5, 1, 3000)})
    rep = drift_report(a, b, ["x"])
    assert rep["drifted"] and "x" in rep["drifted"]


def test_correction_shrinkage_is_chosen_on_validation():
    """With a learnable bias the shrinkage stays large; with pure noise it collapses toward zero, so the
    hybrid can never be much worse than physics + observer on data like its validation block."""
    df = synthetic_frame(2500)
    X, y, phys = build_features(df, 1.0, "hmt")
    sp = chronological_split(len(X), embargo=30)
    hf = HybridForecaster("hmt", 1.0, 40.0).fit(X.iloc[sp["train"]], y.iloc[sp["train"]], phys.iloc[sp["train"]])
    hf.calibrate(X.iloc[sp["val"]], y.iloc[sp["val"]], phys.iloc[sp["val"]])
    assert 0.0 <= hf.alpha <= 1.0 and hf.alpha >= 0.5          # the +6 degC offset is real signal
    rng = np.random.default_rng(1)
    noise = pd.Series(phys.to_numpy() + rng.normal(0, 4, len(phys)), index=phys.index)   # target = physics + noise
    hn = HybridForecaster("hmt", 1.0, 40.0).fit(X.iloc[sp["train"]], noise.iloc[sp["train"]], phys.iloc[sp["train"]])
    hn.calibrate(X.iloc[sp["val"]], noise.iloc[sp["val"]], phys.iloc[sp["val"]])
    assert hn.alpha <= 0.3
    assert len(hn.alpha_curve["alpha"]) == len(hn.alpha_curve["validation_RMSE"]) == 11


def test_physics_base_prefers_observer_corrected_column():
    df = synthetic_frame(600)
    df["phys_hmt_corrected_C"] = df["phys_hmt_C"] + 4.0
    X, y, phys = build_features(df, 1.0, "hmt")
    assert np.allclose(phys.to_numpy(), X["phys_hmt_corrected_C"].to_numpy())
    df2 = synthetic_frame(600)                                   # older data without observer columns
    X2, _, phys2 = build_features(df2, 1.0, "hmt")
    assert np.allclose(phys2.to_numpy(), X2["phys_hmt_C"].to_numpy())
