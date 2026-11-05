"""Shared schema types: structured errors, quantities with units/provenance, job envelopes."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["ErrorDetail", "ErrorResponse", "Quantity", "JobState", "JobInfo", "Provenance"]


class ErrorDetail(BaseModel):
    code: str = Field(..., examples=["OUTSIDE_ENVELOPE"])
    message: str
    details: Optional[Any] = None
    request_id: Optional[str] = None
    path: Optional[str] = None


class ErrorResponse(BaseModel):
    """Every non-2xx response uses this shape (never a disguised 200 OK)."""
    error: ErrorDetail


class Quantity(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: Optional[float] = Field(None, description="null when not computable - never a fabricated zero")
    unit: str
    provenance: str = Field(..., description="MODEL | DERIVED | PROXY | HYBRID | MEASURED")


JobState = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"]


class JobInfo(BaseModel):
    id: str
    kind: str
    status: JobState
    progress: float = Field(..., ge=0, le=1)
    message: str
    idempotency_key: str = ""
    created_by: str
    created_utc: str
    finished_utc: Optional[str] = None
    request: Dict[str, Any] = {}
    links: Dict[str, str] = {}


class Provenance(BaseModel):
    generated_utc: str
    data_time_utc: Optional[str]
    config_version: int
    config_sha256: str
    model_version: str
    data_origin: str
    label: str
    units: Dict[str, str] = {}
