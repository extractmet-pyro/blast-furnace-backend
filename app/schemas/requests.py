"""Request bodies. All numbers must be finite; units are explicit or canonical (see INPUT_UNITS)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__all__ = ["LoginRequest", "ModeChangeRequest", "SpeedRequest", "UnitValue", "SetpointRequest", "FaultRequest",
           "ChangeSpec", "SimulationRequest", "CompareRequest", "SensitivityRequest", "ForecastRequest", "MPCRequest",
           "AckRequest", "AlarmAckRequest", "TagMapEntry", "TagMapRequest", "EdgeReading", "EdgeBatch",
           "ConfigUpdateRequest", "PresetRequest", "TrainRequest", "IdentifyRequest"]


def _finite(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and not math.isfinite(float(v)):
        raise ValueError("value must be a finite number")
    return v


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(Strict):
    username: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=1, max_length=200)


class ModeChangeRequest(Strict):
    mode: Literal["DEMO_SIMULATION", "OFFLINE_SCENARIO", "HISTORICAL_REPLAY", "LIVE_MONITOR_ONLY",
                  "ADVISORY_SHADOW_MPC", "SUPERVISED_MPC"]
    reason: str = Field("", max_length=500)


class SpeedRequest(Strict):
    speed: float = Field(..., ge=0.1, le=240, description="simulated seconds per wall-clock second")


class UnitValue(Strict):
    value: float
    unit: str

    @field_validator("value")
    @classmethod
    def finite(cls, v):
        return _finite(v)


class SetpointRequest(Strict):
    """Synthetic-plant setpoints (DEMO/SHADOW only). Numbers are in canonical units unless a unit is given."""
    changes: Dict[str, Union[float, int, UnitValue, Dict[str, float]]] = Field(
        ..., examples=[{"blast_temp_C": 1200, "pci_t_h": {"value": 70, "unit": "t/h"}}])
    comment: str = Field("", max_length=500)

    @field_validator("changes")
    @classmethod
    def check(cls, v):
        if not v:
            raise ValueError("at least one change is required")
        for k, x in v.items():
            if isinstance(x, (int, float)):
                _finite(x)
            elif isinstance(x, dict):
                for y in x.values():
                    _finite(y)
        return v


class FaultRequest(Strict):
    kind: Literal["DRIFT", "BIAS", "STUCK", "DROPOUT", "DISORDER"]
    tag: str
    magnitude: float = 0.0
    duration_h: float = Field(2.0, gt=0, le=48)

    @field_validator("magnitude")
    @classmethod
    def finite(cls, v):
        return _finite(v)


class ChangeSpec(Strict):
    """One timed change. `op` entries: key -> ["mul"|"add"|"set"|"restore", value]."""
    at_h: float = Field(..., ge=0, le=72)
    op: Dict[str, List[Any]] = {}
    material: Dict[str, Dict[str, Any]] = {}
    coke: Dict[str, List[Any]] = {}
    calibration: Dict[str, List[Any]] = {}
    tapping: Dict[str, List[Any]] = {}

    @model_validator(mode="after")
    def check_ops(self):
        for group in (self.op, self.coke, self.calibration, self.tapping):
            for k, spec in group.items():
                if not spec or spec[0] not in ("mul", "add", "set", "restore"):
                    raise ValueError(f"{k}: operation must be one of mul/add/set/restore")
                if spec[0] in ("mul", "add") and (len(spec) < 2 or not isinstance(spec[1], (int, float))):
                    raise ValueError(f"{k}: {spec[0]} requires a numeric argument")
                if len(spec) > 1 and isinstance(spec[1], (int, float)):
                    _finite(spec[1])
        return self


class SimulationRequest(Strict):
    scenario_id: Optional[str] = Field(None, examples=["S03"], description="library id (GET /api/v1/scenarios/library)")
    name: Optional[str] = Field(None, max_length=120)
    changes: Optional[List[ChangeSpec]] = None
    duration_h: Optional[float] = Field(None, gt=0, le=72)
    dt_s: float = Field(240.0, ge=60, le=600)
    fidelity: Literal["fast", "hifi"] = "fast"
    start_from: Literal["LIVE_TWIN"] = "LIVE_TWIN"
    idempotency_key: Optional[str] = Field(None, max_length=120)

    @model_validator(mode="after")
    def one_source(self):
        if not self.scenario_id and not self.changes:
            raise ValueError("provide scenario_id or changes")
        return self


class CompareRequest(Strict):
    scenario_ids: List[str] = Field(..., min_length=1, max_length=6)
    duration_h: float = Field(12.0, gt=0, le=48)
    dt_s: float = Field(300.0, ge=120, le=600)
    idempotency_key: Optional[str] = Field(None, max_length=120)


class SensitivityRequest(Strict):
    method: Literal["rom", "physics"] = "rom"
    horizon_h: float = Field(8.0, gt=0, le=24)


class ForecastRequest(Strict):
    horizons_min: List[int] = Field([60, 120, 240], min_length=1, max_length=6)
    targets: List[Literal["hmt", "si"]] = ["hmt", "si"]

    @field_validator("horizons_min")
    @classmethod
    def check_h(cls, v):
        if any(h < 15 or h > 720 for h in v):
            raise ValueError("horizons must be within [15, 720] minutes")
        return sorted(set(v))


class MPCRequest(Strict):
    method: Literal["linear", "nmpc_physics"] = "linear"
    targets: Dict[str, float] = Field({}, examples=[{"hmt_C": 1505.0, "hm_si_pct": 0.45}])
    availability: Dict[str, bool] = Field({}, description="MV availability flags (false = locked / not available)")
    econ_weight: float = Field(0.0, ge=0, le=50)
    run_async: bool = Field(False, description="return 202 + job id (always true for nmpc_physics)")

    @field_validator("targets")
    @classmethod
    def check_t(cls, v):
        allowed = {"hmt_C", "hm_si_pct", "fuel_rate_kg_tHM", "raft_C", "eta_CO_pct"}
        for k, x in v.items():
            if k not in allowed:
                raise ValueError(f"unsupported target '{k}' (allowed {sorted(allowed)})")
            _finite(x)
        if "hmt_C" in v and not (1400 <= v["hmt_C"] <= 1600):
            raise ValueError("hmt_C target outside [1400, 1600] degC")
        if "hm_si_pct" in v and not (0.1 <= v["hm_si_pct"] <= 1.2):
            raise ValueError("hm_si_pct target outside [0.1, 1.2] wt%")
        return v


class AckRequest(Strict):
    decision: Literal["ACKNOWLEDGED", "REJECTED"]
    comment: str = Field("", max_length=1000)


class AlarmAckRequest(Strict):
    comment: str = Field("", max_length=1000)


class TagMapEntry(Strict):
    site_tag: str = Field(..., min_length=1, max_length=128)
    catalogue_tag: str
    site_unit: str
    scale: float = 1.0
    offset: float = 0.0

    @field_validator("scale", "offset")
    @classmethod
    def finite(cls, v):
        return _finite(v)


class TagMapRequest(Strict):
    mappings: List[TagMapEntry] = Field(..., min_length=1, max_length=500)


class EdgeReading(Strict):
    tag: str
    value: Optional[float]
    ts_utc: str = Field(..., description="ISO-8601 UTC sensor timestamp, e.g. 2026-11-04T10:00:00Z")

    @field_validator("value")
    @classmethod
    def finite(cls, v):
        return None if v is None else _finite(v)

    @field_validator("ts_utc")
    @classmethod
    def utc(cls, v):
        from datetime import datetime
        if not (v.endswith("Z") or v.endswith("+00:00")):
            raise ValueError("timestamps must be UTC (suffix Z or +00:00)")
        datetime.fromisoformat(v.replace("Z", "+00:00"))
        return v


class EdgeBatch(Strict):
    readings: List[EdgeReading] = Field(..., min_length=1, max_length=5000)


class ConfigUpdateRequest(Strict):
    config: Dict[str, Any]
    expected_version: int = Field(..., ge=0, description="optimistic concurrency: current version you edited")
    comment: str = Field("", max_length=500)


class PresetRequest(Strict):
    preset: str
    expected_version: int = Field(..., ge=0)
    overrides: Dict[str, Any] = Field({}, description="optional geometry/operation overrides (validated)")


class TrainRequest(Strict):
    targets: List[Literal["hmt", "si"]] = ["hmt", "si"]
    source: Literal["shipped_history", "live_buffer"] = "shipped_history"


class IdentifyRequest(Strict):
    Ts_min: float = Field(15.0, ge=5, le=60)
    horizon_h: float = Field(12.0, ge=4, le=24)
