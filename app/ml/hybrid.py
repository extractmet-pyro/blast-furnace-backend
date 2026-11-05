"""Hybrid physics + ML forecaster.

  prediction = physics + alpha * clip( g(features), -bound, +bound )

g is a gradient-boosted residual model (scikit-learn HistGradientBoosting; LightGBM/XGBoost are
used automatically when installed and requested). The correction is applied only to OBSERVABLE
outputs (HMT, Si) - never to species inventories - so elemental conservation of the physics
state is untouched. A Mahalanobis out-of-domain gate disables the correction (falls back to
physics) when features leave the training envelope. The shrinkage factor alpha in [0, 1] is chosen
on the VALIDATION block (alpha = 0 means the ML adds nothing and the forecast is physics + observer).
Prediction intervals are split-conformal (absolute residual quantile on the validation block).
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor


def make_regressor(kind: str = "hgb", seed: int = 7):
    if kind == "lightgbm":
        try:
            import lightgbm as lgb  # type: ignore
            return lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31, random_state=seed, verbose=-1)
        except Exception:
            pass
    if kind == "xgboost":
        try:
            import xgboost as xgb  # type: ignore
            return xgb.XGBRegressor(n_estimators=300, learning_rate=0.05, max_depth=5, random_state=seed)
        except Exception:
            pass
    # deliberately conservative: residuals of a calibrated physics model are small and autocorrelated
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=60,
                                         l2_regularization=5.0, early_stopping=True, validation_fraction=0.15,
                                         n_iter_no_change=20, random_state=seed)


class HybridForecaster:
    def __init__(self, target: str, horizon_h: float, bound: float, kind: str = "hgb", seed: int = 7):
        self.target, self.horizon_h, self.bound, self.kind, self.seed = target, horizon_h, bound, kind, seed
        self.reg = make_regressor(kind, seed)
        self.cols = None
        self.mu = None
        self.cov_inv = None
        self.ood_threshold = None
        self.q90 = None
        self.q95 = None
        self.fill = None
        self.alpha = 1.0
        self.alpha_curve = None
        self.algorithm = type(self.reg).__name__

    def _prep(self, X: pd.DataFrame) -> pd.DataFrame:
        Xc = X.reindex(columns=self.cols)
        return Xc.fillna(self.fill).fillna(0.0)

    def fit(self, X: pd.DataFrame, y: pd.Series, phys: pd.Series):
        self.cols = list(X.columns)
        # imputation statistics come from the TRAINING block only (stored with the model)
        self.fill = X.median(numeric_only=True).reindex(self.cols).fillna(0.0)
        resid = (y - phys).clip(-3 * self.bound, 3 * self.bound)
        Xp = self._prep(X)
        self.reg.fit(Xp, resid)
        Z = Xp.to_numpy(dtype=float)
        self.mu = Z.mean(axis=0)
        sd = Z.std(axis=0) + 1e-9
        self.sd = sd
        Zs = np.clip((Z - self.mu) / sd, -50.0, 50.0)
        # OOD gate: Mahalanobis distance in the leading principal subspace of the standardised training
        # features (>= 95 % variance, 3..15 components). A full-covariance distance over ~70 collinear lag
        # features is ill-conditioned and flags almost everything; the PCA form is the standard remedy.
        _, S, Vt = np.linalg.svd(Zs, full_matrices=False)
        var = S ** 2 / max(len(Zs) - 1, 1)
        cum = np.cumsum(var) / max(np.sum(var), 1e-12)
        k = int(min(max(np.searchsorted(cum, 0.95) + 1, 3), 15, len(var)))
        self.pca = Vt[:k]
        self.pca_var = np.maximum(var[:k], 1e-9)
        d = np.sum((Zs @ self.pca.T) ** 2 / self.pca_var, axis=1)
        self.ood_threshold = float(np.quantile(d, 0.999) * 1.5)
        self.cov_inv = None
        return self

    def calibrate(self, X: pd.DataFrame, y: pd.Series, phys: pd.Series):
        """Choose the correction shrinkage on the validation block, then the conformal half-widths."""
        self.alpha = 1.0
        raw = self.predict(X, phys)
        yy, ph = y.to_numpy(dtype=float), phys.to_numpy(dtype=float)
        grid = np.round(np.linspace(0.0, 1.0, 11), 2)
        rmse = [float(np.sqrt(np.nanmean((yy - (ph + a * raw["correction"])) ** 2))) for a in grid]
        self.alpha = float(grid[int(np.nanargmin(rmse))])
        self.alpha_curve = {"alpha": grid.tolist(), "validation_RMSE": rmse}
        pred = self.predict(X, phys)["prediction"]
        err = np.abs(yy - pred)
        self.q90 = float(np.quantile(err, 0.90))
        self.q95 = float(np.quantile(err, 0.95))
        return self

    def mahalanobis(self, X: pd.DataFrame) -> np.ndarray:
        Zs = np.clip((self._prep(X).to_numpy(dtype=float) - self.mu) / self.sd, -50.0, 50.0)
        if getattr(self, "pca", None) is not None:
            return np.sum((Zs @ self.pca.T) ** 2 / self.pca_var, axis=1)
        return np.einsum("ij,jk,ik->i", Zs, self.cov_inv, Zs)          # artefacts from earlier releases

    def predict(self, X: pd.DataFrame, phys: pd.Series) -> Dict[str, np.ndarray]:
        corr = getattr(self, "alpha", 1.0) * np.clip(self.reg.predict(self._prep(X)), -self.bound, self.bound)
        d = self.mahalanobis(X)
        ood = d > self.ood_threshold
        corr = np.where(ood, 0.0, corr)
        pred = phys.to_numpy() + corr
        half = self.q95 if self.q95 is not None else np.nan
        return {"prediction": pred, "correction": corr, "ood": ood, "lower95": pred - half, "upper95": pred + half,
                "mahalanobis": d}
