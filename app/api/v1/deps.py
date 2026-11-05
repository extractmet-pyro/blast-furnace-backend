"""Shared API dependencies."""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException

from ...core.config import get_settings
from ...core.security import Principal, current_principal, require
from ...services.twin_service import TwinService, get_service

ERR = {401: {"description": "Unauthenticated"}, 403: {"description": "Forbidden (role)"},
       409: {"description": "Domain/state conflict"}, 422: {"description": "Validation error"},
       503: {"description": "Twin not ready"}}


def svc() -> TwinService:
    return get_service()


def ready_svc() -> TwinService:
    s = get_service()
    s.require_ready()
    return s


viewer = require("VIEWER")
operator = require("OPERATOR")
metallurgist = require("METALLURGIST")
engineer = require("PROCESS_ENGINEER")
admin = require("ADMIN")


async def edge_principal(x_edge_token: Optional[str] = Header(None)) -> Principal:
    """Service identity for the read-only edge gateway (constant-time token check)."""
    s = get_settings()
    if not s.edge_ingest_token:
        raise HTTPException(403, "Edge ingestion disabled (EDGE_INGEST_TOKEN not configured)")
    if not x_edge_token or not hmac.compare_digest(x_edge_token, s.edge_ingest_token):
        raise HTTPException(401, "Invalid edge token")
    return Principal("edge-gateway", "SERVICE_INGEST")


def job_links(jid: str, kind: str = "simulations") -> dict:
    return {"self": f"/api/v1/{kind}/{jid}", "results": f"/api/v1/{kind}/{jid}/results"}
