"""System status, modes, auth and configuration endpoints."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm

from ...core.config import get_settings
from ...core.errors import DomainError
from ...core.safety import MODE_INFO
from ...core.security import ROLES, Principal, create_token, current_principal, verify_password
from ...db.models import AuditEvent, PlantConfigVersion, UserRole
from ...db.session import session
from ...physics.defaults import PRESETS, build_config, list_presets
from ...schemas import (ConfigUpdateRequest, ErrorResponse, LoginRequest, ModeChangeRequest, PresetRequest, SpeedRequest,
                        SystemStatus, TokenResponse)
from ...services.config_validation import validate_config
from ...services.twin_service import cfg_hash
from .deps import ERR, admin, engineer, svc, viewer

router = APIRouter()
SCHEMA_PATH = Path(__file__).resolve().parents[3] / "config" / "plant_config.schema.json"


# ------------------------------------------------------------------------------------ status
@router.get("/system/status", response_model=SystemStatus, tags=["system"], summary="Mode, data source, freshness, model health")
def system_status(s=Depends(svc)):
    return s.status()


@router.get("/system/modes", tags=["system"])
def modes(s=Depends(svc)):
    return {"current": s.mode, "modes": MODE_INFO,
            "rules": ["All modes are advisory: no plant-write path exists in this release.",
                      "SUPERVISED_MPC cannot be enabled from a cloud deployment.",
                      "Mode changes require PROCESS_ENGINEER or ADMIN and are audited."]}


@router.post("/system/mode", tags=["system"], responses={**ERR, 403: {"model": ErrorResponse}})
def set_mode(body: ModeChangeRequest, p: Principal = Depends(engineer), s=Depends(svc)):
    s.require_ready()
    s.set_mode(body.mode, p, body.reason)
    return s.status()


@router.post("/system/speed", tags=["system"], summary="Demo simulation speed (simulated s per wall s)")
def set_speed(body: SpeedRequest, p: Principal = Depends(engineer), s=Depends(svc)):
    s.set_speed(body.speed)
    s.audit(p.sub, p.role, "SIM_SPEED", str(body.speed))
    return {"speed": s.speed}


@router.get("/system/events", tags=["system"])
def events(limit: int = 200, s=Depends(svc), p=Depends(viewer)):
    return {"events": list(s.events)[-max(1, min(limit, 800)):]}


@router.get("/system/jobs", tags=["system"])
def jobs(s=Depends(svc), p=Depends(viewer)):
    return {"jobs": sorted(s.jobs.jobs.values(), key=lambda j: j["created_utc"], reverse=True)[:100]}


@router.get("/system/audit", tags=["system"], summary="Audit trail (ADMIN)")
def audit(limit: int = 200, p=Depends(admin)):
    with session() as db:
        rows = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(max(1, min(limit, 2000))).all()
        return {"events": [{"ts": r.ts.isoformat(), "actor": r.actor, "role": r.role, "action": r.action, "target": r.target,
                            "detail": r.detail, "request_id": r.request_id} for r in rows]}


# -------------------------------------------------------------------------------------- auth
def _login(username: str, password: str, s) -> dict:
    with session() as db:
        u = db.query(UserRole).filter_by(username=username, active=True).first()
        ok = bool(u and verify_password(password, u.password_hash))
        role = u.role if u else None
    if not ok:
        s.audit(username[:80], "-", "LOGIN_FAILED")
        raise HTTPException(401, "Invalid username or password")
    tok = create_token(username, role)
    s.audit(username, role, "LOGIN")
    # "bearer" is the OAuth2 token type, not a password
    return {"access_token": tok, "token_type": "bearer", "role": role, "expires_min": get_settings().jwt_expire_min}  # nosec B105


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"], responses={401: {"model": ErrorResponse}})
def login(body: LoginRequest, s=Depends(svc)):
    return _login(body.username, body.password, s)


@router.post("/auth/token", response_model=TokenResponse, tags=["auth"], include_in_schema=True,
             summary="OAuth2 password flow (form) - for the OpenAPI 'Authorize' button")
def token(form: OAuth2PasswordRequestForm = Depends(), s=Depends(svc)):
    return _login(form.username, form.password, s)


@router.get("/auth/me", tags=["auth"])
def me(p: Principal = Depends(current_principal)):
    return {**p.as_dict(), "roles_hierarchy": ROLES}


@router.get("/auth/demo-accounts", tags=["auth"], summary="Demo usernames and roles (never passwords)")
def demo_accounts():
    st = get_settings()
    if not st.demo_mode:
        return {"demo_accounts": [], "note": "DEMO_MODE is off"}
    with session() as db:
        rows = db.query(UserRole).filter_by(demo_account=True, active=True).all()
        accts = [{"username": r.username, "role": r.role} for r in rows]
    return {"demo_accounts": accts,
            "password_source": "DEMO_USER_PASSWORD environment variable of the backend service (not shown here)" if accts else
                               "no demo accounts: DEMO_USER_PASSWORD not set - anonymous VIEWER access only",
            "anonymous_viewer": st.allow_anonymous_viewer}


# ------------------------------------------------------------------------------------ config
@router.get("/config/plant", tags=["config"])
def get_config(s=Depends(svc), p=Depends(viewer)):
    s.require_ready()
    return {"version": s.config_version, "sha256": cfg_hash(s.cfg), "config": s.cfg}


@router.get("/config/plant/versions", tags=["config"])
def config_versions(p=Depends(viewer)):
    with session() as db:
        rows = db.query(PlantConfigVersion).order_by(PlantConfigVersion.version.desc()).limit(50).all()
        return {"versions": [{"version": r.version, "sha256": r.sha256, "author": r.author, "comment": r.comment,
                              "active": r.active, "created_at": r.created_at.isoformat()} for r in rows]}


@router.get("/config/plant/versions/{version}", tags=["config"], responses={404: {"model": ErrorResponse}})
def config_version(version: int, s=Depends(svc), p=Depends(viewer)):
    """A stored configuration version of the active plant in full - export it, or PUT its `config` back to roll back
    (that creates a new version; stored versions are never changed)."""
    plant_id = s.cfg["plant"]["id"]
    with session() as db:
        r = (db.query(PlantConfigVersion)
             .filter(PlantConfigVersion.plant_id == plant_id, PlantConfigVersion.version == version).first())
        if r is None:
            raise DomainError("NOT_FOUND", f"configuration version {version} of plant {plant_id} is not stored", 404)
        return {"version": r.version, "sha256": r.sha256, "author": r.author, "comment": r.comment, "active": r.active,
                "created_at": r.created_at.isoformat(), "config": r.config}


@router.get("/config/schema", tags=["config"])
def config_schema():
    return json.loads(SCHEMA_PATH.read_text())


@router.get("/config/presets", tags=["config"])
def presets():
    return {"presets": list_presets()}


def _store_version(s, cfg: dict, expected: int, author: str, comment: str) -> int:
    with s.lock:
        if expected != s.config_version:
            raise DomainError("VERSION_CONFLICT", f"Config changed meanwhile (current v{s.config_version}, you edited v{expected})",
                              409, {"current_version": s.config_version})
        with session() as db:
            last = db.query(PlantConfigVersion).order_by(PlantConfigVersion.version.desc()).first()
            new_v = (last.version if last else 0) + 1
            for r in db.query(PlantConfigVersion).filter_by(active=True).all():
                r.active = False
            from ...db.models import Plant
            if not db.get(Plant, cfg["plant"]["id"]):
                db.add(Plant(id=cfg["plant"]["id"], name=cfg["plant"]["name"], data_origin="SYNTHETIC"))
                db.flush()
            db.add(PlantConfigVersion(plant_id=cfg["plant"]["id"], version=new_v, config=cfg, sha256=cfg_hash(cfg),
                                      author=author, comment=comment, active=True))
    return new_v


@router.put("/config/plant", status_code=202, tags=["config"], responses={**ERR, 409: {"model": ErrorResponse}})
def put_config(body: ConfigUpdateRequest, p: Principal = Depends(engineer), s=Depends(svc)):
    s.require_ready()
    cfg = validate_config(body.config)
    v = _store_version(s, cfg, body.expected_version, p.sub, body.comment)
    s.audit(p.sub, p.role, "CONFIG_UPDATE", f"v{v}", {"sha256": cfg_hash(cfg), "comment": body.comment})
    s.rebuild(cfg, v)
    return {"version": v, "sha256": cfg_hash(cfg), "status": "ACCEPTED - twin re-initialising (poll /system/status)"}


@router.post("/config/preset", status_code=202, tags=["config"], summary="Switch furnace size/capacity preset (+ overrides)")
def set_preset(body: PresetRequest, p: Principal = Depends(engineer), s=Depends(svc)):
    s.require_ready()
    if body.preset not in PRESETS:
        raise DomainError("UNKNOWN_PRESET", f"Unknown preset {body.preset}", 422, {"available": sorted(PRESETS)})
    cfg = build_config(body.preset)
    for section, vals in (body.overrides or {}).items():
        if section not in ("geometry", "operation", "coke", "pci", "economics", "targets"):
            raise DomainError("INVALID_OVERRIDE", f"section '{section}' cannot be overridden here", 422)
        if not isinstance(vals, dict):
            raise DomainError("INVALID_OVERRIDE", f"section '{section}' must be an object", 422)
        for k, v in vals.items():
            if k not in cfg[section]:
                raise DomainError("INVALID_OVERRIDE", f"unknown key {section}.{k}", 422)
            cfg[section][k] = copy.deepcopy(v)
    if body.overrides:
        cfg["plant"]["preset"] = body.preset + "-custom"
        cfg["plant"]["id"] = f"industry-x-{body.preset}-custom"
        cfg["plant"]["name"] = cfg["plant"]["name"].replace("(illustrative)", "(custom, illustrative)")
    cfg = validate_config(cfg)
    v = _store_version(s, cfg, body.expected_version, p.sub, f"preset {body.preset}" + (" + overrides" if body.overrides else ""))
    s.audit(p.sub, p.role, "CONFIG_PRESET", body.preset, {"overrides": body.overrides, "version": v})
    s.rebuild(cfg, v)
    return {"version": v, "preset": cfg["plant"]["preset"], "status": "ACCEPTED - twin re-initialising (poll /system/status)"}
