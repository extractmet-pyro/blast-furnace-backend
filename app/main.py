"""FastAPI application: Blast Furnace Digital Twin, Process Simulator & ADVISORY Supervisory Control.

Run:  uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
Docs: /docs (Swagger UI), /redoc, /openapi.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .core.config import API_VERSION, APP_VERSION, MODEL_VERSION, get_settings
from .core.errors import (DomainError, domain_error_handler, http_error_handler, unhandled_error_handler,
                          validation_error_handler)
from .core.security import RateLimiter, SecurityHeaders, hash_password
from .db.models import UserRole
from .db.session import init_db, session
from .services.twin_service import get_service


class JsonFormatter(logging.Formatter):
    def format(self, r: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(r.created)), "level": r.levelname, "logger": r.name,
             "msg": r.getMessage()}
        if r.exc_info:
            d["exc"] = self.formatException(r.exc_info)[-2000:]
        return json.dumps(d)


def _logging():
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(get_settings().log_level.upper())


DEMO_USERS = [("viewer", "VIEWER"), ("operator", "OPERATOR"), ("metallurgist", "METALLURGIST"),
              ("engineer", "PROCESS_ENGINEER"), ("admin", "ADMIN")]


def seed_demo_users():
    """Demo accounts exist only when DEMO_MODE=true AND DEMO_USER_PASSWORD is provided (never a default password)."""
    s = get_settings()
    log = logging.getLogger("bf.auth")
    if not (s.demo_mode and s.demo_user_password):
        log.info("no demo accounts created (DEMO_MODE=%s, DEMO_USER_PASSWORD %s)", s.demo_mode,
                 "set" if s.demo_user_password else "not set")
        return
    if len(s.demo_user_password) < 10:
        log.warning("DEMO_USER_PASSWORD shorter than 10 characters - demo accounts NOT created")
        return
    with session() as db:
        for u, role in DEMO_USERS:
            row = db.query(UserRole).filter_by(username=u).first()
            if row is None:
                db.add(UserRole(username=u, role=role, password_hash=hash_password(s.demo_user_password), demo_account=True))
            elif row.demo_account:
                row.password_hash = hash_password(s.demo_user_password)
                row.role = role
    log.info("demo accounts ensured: %s", ", ".join(u for u, _ in DEMO_USERS))


@asynccontextmanager
async def lifespan(app: FastAPI):
    _logging()
    s = get_settings()
    log = logging.getLogger("bf.main")
    if s.allow_plant_writes:
        raise RuntimeError("ALLOW_PLANT_WRITES=true is not supported - this release has no plant-write path.")
    if s.is_production and s.ephemeral_jwt_secret:
        log.warning("JWT_SECRET not set: tokens are signed with an ephemeral key and become invalid on restart")
    if not s.demo_mode and (s.ephemeral_jwt_secret or len(s.jwt_secret) < 32):
        # outside demo mode a missing or short signing key is a deployment error, not a warning
        raise RuntimeError("DEMO_MODE=false requires JWT_SECRET of at least 32 characters.")
    init_db(create=True)
    seed_demo_users()
    svc = get_service()
    svc.start(background=True)
    log.info("startup complete (env=%s, demo=%s, tier=%s, db=%s)", s.app_env, s.demo_mode, s.deployment_tier,
             s.database_url.split(":")[0])
    yield
    svc.stop()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="Blast Furnace Digital Twin - Extractmet",
        version=APP_VERSION,
        description=("First-principles blast-furnace ironmaking digital twin, process simulator and **advisory** supervisory "
                     "control (MPC recommendations only - no plant-write path exists). Demo data are SYNTHETIC "
                     "('Industry-X'); nothing here is connected to a physical furnace."),
        lifespan=lifespan,
        openapi_tags=[{"name": n} for n in ("system", "auth", "config", "data", "telemetry", "twin", "thermochemistry",
                                            "simulations", "forecasts", "models", "mpc", "alarms", "reports")],
    )
    app.add_middleware(GZipMiddleware, minimum_size=2048)
    app.add_middleware(RateLimiter, per_min=s.rate_limit_per_min)
    app.add_middleware(SecurityHeaders)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID", "X-Edge-Token"],
                       expose_headers=["X-Request-ID", "Content-Disposition"], max_age=600)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    from .api.v1 import control, forecasts, physics, reports, simulations, status, telemetry
    for mod in (status, telemetry, physics, simulations, forecasts, control, reports):
        app.include_router(mod.router, prefix=f"/api/{API_VERSION}")

    @app.get("/", include_in_schema=False)
    def root():
        return {"service": "blast-furnace-backend", "version": APP_VERSION, "docs": "/docs", "status": f"/api/{API_VERSION}/system/status",
                "advisory_only": True}

    @app.get("/health", tags=["system"], summary="Liveness (process up)")
    def health():
        return {"status": "ok", "version": APP_VERSION}

    @app.get("/ready", tags=["system"], summary="Readiness: twin initialised and DB reachable")
    def ready():
        svc = get_service()
        db_ok = True
        try:
            from sqlalchemy import text
            with session() as db:
                db.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        ok = svc.state == "RUNNING" and db_ok
        body = {"ready": ok, "twin_state": svc.state, "twin_message": svc.state_msg, "database": "ok" if db_ok else "unreachable"}
        from fastapi.responses import JSONResponse
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/metrics", tags=["system"], response_class=PlainTextResponse, summary="Prometheus text metrics (no secrets)")
    def metrics():
        svc = get_service()
        st = svc.status() if svc.twin else {}
        jobs = svc.jobs.jobs.values()
        lines = [
            "# TYPE bf_twin_ready gauge", f"bf_twin_ready {1 if svc.state == 'RUNNING' else 0}",
            "# TYPE bf_stream_seq counter", f"bf_stream_seq {svc.seq}",
            "# TYPE bf_data_fresh gauge", f"bf_data_fresh {1 if st.get('data_fresh') else 0}",
            "# TYPE bf_balance_ok gauge", f"bf_balance_ok {1 if st.get('balance_status') == 'OK' else 0}",
            "# TYPE bf_data_quality_score gauge", f"bf_data_quality_score {st.get('data_quality_score') or 0}",
            "# TYPE bf_active_alarms gauge", f"bf_active_alarms {st.get('active_alarms', 0)}",
            "# TYPE bf_jobs gauge",
            *[f'bf_jobs{{status="{k}"}} {sum(1 for j in jobs if j["status"] == k)}'
              for k in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")],
            "# TYPE bf_persistence_errors_total counter",
            *[f'bf_persistence_errors_total{{record="{k.replace(" ", "_")}"}} {v}' for k, v in sorted(svc.persist_errors.items())],
            "# TYPE bf_plant_writes_enabled gauge", "bf_plant_writes_enabled 0",
            f'# model_version {MODEL_VERSION}',
        ]
        return "\n".join(lines) + "\n"

    return app


app = create_app()
