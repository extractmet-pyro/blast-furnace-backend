"""Baseline forecasters every ML model must beat on held-out (chronologically later) data."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


def persistence(X: pd.DataFrame, target: str) -> np.ndarray:
    col = "last_HMT_DIP" if target == "hmt" else "last_HM_SI"
    return X[col].to_numpy(dtype=float)


def physics_only(phys: pd.Series) -> np.ndarray:
    return phys.to_numpy()


class ARXRidge:
    """Linear ARX-type model on lagged inputs and last measurements (ridge regularised)."""

    def __init__(self, alpha: float = 3.0):
        self.model = make_pipeline(StandardScaler(), Ridge(alpha=alpha))
        self.cols = None

    def fit(self, X: pd.DataFrame, y: pd.Series):
        self.cols = list(X.columns)
        self.fill = X.median(numeric_only=True).reindex(self.cols).fillna(0.0)
        self.model.fit(X[self.cols].fillna(self.fill).fillna(0.0), y)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict(X.reindex(columns=self.cols).fillna(self.fill).fillna(0.0))
