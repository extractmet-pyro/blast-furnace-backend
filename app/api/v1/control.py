"""Advisory MPC (recommend / review / acknowledge - audit only), ROM identification, alarms.

There is deliberately NO endpoint that writes to a plant system. Acknowledging a recommendation
changes its audit state only (tests/test_safety.py)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ...core.errors import DomainError
from ...data.synthetic import iso
from ...mpc.constraints import build_constraints
from ...mpc.predictions import CV_DEFS, MV_DEFS, identify
from ...schemas import AckRequest, AlarmAckRequest, ErrorResponse, IdentifyRequest, MPCRequest, RecommendationSummary
from ...services.jobs import _json_safe
from ...services.twin_service import DEMO_DIR
from .deps import ERR, engineer, job_links, operator, ready_svc, svc, viewer

router = APIRouter()


@router.post("/mpc/recommend", tags=["mpc"], responses={**ERR, 202: {"description": "job accepted (async/NMPC)"}},
             summary="Compute an ADVISORY recommendation (trust gate -> QP -> physics verification)")
def recommend(body: MPCRequest, p=Depends(operator), s=Depends(ready_svc)):
    if body.availability:
        unknown = set(body.availability) - set(MV_DEFS)
        if unknown:
            raise DomainError("UNKNOWN_MV", f"unknown MVs {sorted(unknown)}", 422)
    if body.run_async or body.method == "nmpc_physics":
        j = s.jobs.submit("mpc:" + body.method, body.model_dump(),
                          lambda cb: s.recommend(user=p.sub, method=body.method, targets=body.targets or None,
                                                 availability=body.availability or None, econ_weight=body.econ_weight),
                          p.sub)
        return JSONResponse(status_code=202, content={**j, "links": job_links(j["id"])})
    rec = s.recommend(user=p.sub, method=body.method, targets=body.targets or None, availability=body.availability or None,
                      econ_weight=body.econ_weight)
    s.audit(p.sub, p.role, "MPC_RECOMMEND", rec["id"], {"status": rec["status"], "method": body.method})
    return rec


@router.get("/mpc/recommendations", tags=["mpc"])
def list_recs(limit: int = 50, s=Depends(svc), p=Depends(viewer)):
    recs = list(s.recs.values())[-max(1, min(limit, 300)):]
    return {"recommendations": [RecommendationSummary(**{k: r.get(k) for k in ("id", "status", "mode", "created_utc",
                                                                             "expires_utc", "plant_write", "reasons")},
                                                      advisory_only=True).model_dump() | {"requested_by": r.get("requested_by"),
                                                                                         "explanation": r.get("explanation")}
                                for r in reversed(recs)]}


@router.get("/mpc/recommendations/{rid}", tags=["mpc"], responses={404: {"model": ErrorResponse}})
def get_rec(rid: str, s=Depends(svc), p=Depends(viewer)):
    return s.get_rec(rid)


@router.post("/mpc/recommendations/{rid}/acknowledge", tags=["mpc"], responses={**ERR, 404: {"model": ErrorResponse}},
             summary="Operator decision - changes AUDIT STATE ONLY (no plant write)")
def ack_rec(rid: str, body: AckRequest, p=Depends(operator), s=Depends(svc)):
    rec = s.acknowledge_rec(rid, p, body.decision, body.comment)
    return {"id": rid, "status": rec["status"], "audit": rec["audit"], "plant_write": "NONE - recommendation only"}


@router.get("/mpc/model", tags=["mpc"], summary="Identified step-response model (gains, t63 delays, coefficients)")
def mpc_model(s=Depends(ready_svc), p=Depends(viewer)):
    if s.rom is None:
        return {"available": False, "hint": "POST /api/v1/mpc/identify (PROCESS_ENGINEER)"}
    return {"available": True, **_json_safe(s.rom.as_dict()), "mv_defs": MV_DEFS, "cv_defs": CV_DEFS}


@router.get("/mpc/constraints", tags=["mpc"])
def mpc_constraints(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        cs = build_constraints(s.cfg, s.twin.op)
    return {"constraints": _json_safe(cs.as_dict()), "rate_limits_per_15min": s.cfg["rate_limits_per_15min"],
            "equipment_limits": s.cfg["equipment_limits"], "cv_limits": s.cfg["constraints"],
            "policy": "MV bounds/rate limits and HARD CV limits are never relaxed; SOFT limits use penalised slack."}


@router.post("/mpc/identify", status_code=202, tags=["mpc"], summary="Re-identify the ROM by step tests on twin clones (job)")
def mpc_identify(body: IdentifyRequest, p=Depends(engineer), s=Depends(ready_svc)):
    with s.lock:
        base = s.twin.clone()

    def job(cb):
        rom = identify(base, Ts_s=body.Ts_min * 60, horizon_h=body.horizon_h, progress=cb)
        d = rom.as_dict()
        s.set_rom(d, source=f"identified {rom.identified_utc} by {p.sub} (physics-twin step tests)")
        return {"status": "SUCCEEDED", "rom": _json_safe(d)}
    j = s.jobs.submit("mpc:identify", body.model_dump(), job, p.sub)
    s.audit(p.sub, p.role, "ROM_IDENTIFY", j["id"], body.model_dump())
    return JSONResponse(status_code=202, content={**j, "links": job_links(j["id"])})


@router.get("/mpc/safety-case", tags=["mpc"])
def safety_case():
    return {"advisory_only": True, "plant_write_path": "ABSENT", "allow_plant_writes_setting": "must be false (startup refuses true)",
            "acknowledge_effect": "audit state only",
            "release_checks": ["trust gate (data quality, freshness, balances, observer, alarms, envelope)",
                               "QP with hard MV bounds/rates and hard CV limits", "independent physics re-simulation",
                               "expiry after 30 min", "role OPERATOR+ to acknowledge"],
            "future_gateway": "Any setpoint transfer requires a separately engineered, safety-assessed on-premises gateway "
                              "under DCS ownership (stage G4) - not part of this software."}


# --------------------------------------------------------------------------------------- alarms
@router.get("/alarms", tags=["alarms"])
def alarms(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return {"active": list(s.alarms.active.values()), "log": list(s.alarms.log)[-200:],
                "classification": "event detection with evidence - root cause NOT proven"}


@router.post("/alarms/{alarm_id}/acknowledge", tags=["alarms"], responses={404: {"model": ErrorResponse}})
def ack_alarm(alarm_id: str, body: AlarmAckRequest, p=Depends(operator), s=Depends(ready_svc)):
    with s.lock:
        a = s.alarms.acknowledge(alarm_id, p.sub, iso(s.data_t))
    if not a:
        raise DomainError("NOT_FOUND", "alarm not active or unknown", 404)
    s.audit(p.sub, p.role, "ALARM_ACK", alarm_id, {"code": a["code"], "comment": body.comment})
    return a
