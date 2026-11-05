"""Response contracts for the core endpoints (rich engineering payloads are documented dicts)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["SystemStatus", "TokenResponse", "ForecastPrediction", "ForecastResponse", "RecommendationSummary",
           "HealthResponse"]


class HealthResponse(BaseModel):
    status: str
    version: str


class SystemStatus(BaseModel):
    model_config = ConfigDict(extra="allow")
    api_version: str
    app_version: str
    model_version: str
    state: str
    mode: str
    data_origin: str
    write_disabled: bool = Field(..., description="always true in this release")
    data_fresh: bool
    model_health: str = Field(..., description="HEALTHY only if fresh data, closed balances, GOOD critical tags, model OK")
    health_reasons: List[str]
    balance_status: str
    data_quality_score: Optional[float]
    last_good_data_utc: Optional[str]
    data_time_utc: Optional[str]
    config_version: int
    label: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    expires_min: int


class ForecastPrediction(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Optional[float]
    unit: str
    lower95: Optional[float]
    upper95: Optional[float]
    status: str = Field(..., description="HYBRID | PHYSICS_PLUS_OBSERVER | OOD_FALLBACK_TO_PHYSICS | FAILED")


class ForecastResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    run_id: str
    mode: str
    model_version: str
    data_origin: str
    timestamp_utc: str
    horizons: List[Dict[str, Any]]
    balance_quality: Dict[str, Any]
    recommendation_eligible: bool
    warnings: List[str]


class RecommendationSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: str
    mode: str
    created_utc: str
    expires_utc: str
    advisory_only: bool = True
    plant_write: str
    reasons: List[str]
