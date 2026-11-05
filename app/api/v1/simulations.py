"""Simulation jobs (202 + job id), scenario library, comparison, sensitivity and studies."""
from __future__ import annotations

import copy
from typing import Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from ...core.errors import DomainError
from ...physics.burden import ChemistryError
from ...physics.integrator import BlastFurnaceModel
from ...schemas import CompareRequest, ErrorResponse, JobInfo, SensitivityRequest, SimulationRequest
from ...services import scenarios as scn
from ...services import studies
from ...services.twin_service import SETPOINT_KEYS
from .deps import ERR, engineer, job_links, metallurgist, ready_svc, svc, viewer

router = APIRouter()


def _check_envelope(s, spec: dict):
    """Dry-run the timed operation changes and reject anything outside the equipment envelope."""
    lim = s.cfg["equipment_limits"]
    op = copy.deepcopy(s.twin.op)
    base = copy.deepcopy(op)
    for ch in sorted(spec.get("changes", []), key=lambda c: c["at_h"]):
        for k, sp in ch.get("op", {}).items():
            if k not in SETPOINT_KEYS:
                raise DomainError("UNKNOWN_SETPOINT", f"'{k}' cannot be changed in a scenario", 422)
            scn._apply(op, k, sp, base)
            v = op[k]
            if k in lim and isinstance(v, (int, float)) and not isinstance(v, bool):
                lo, hi = lim[k]
                if not (lo - 1e-9 <= v <= hi + 1e-9):
                    raise DomainError("OUTSIDE_ENVELOPE", f"scenario sets {k}={v:.3f} outside equipment envelope [{lo}, {hi}]",
                                      422, {"at_h": ch["at_h"]})
            if k == "burden_mix":
                if abs(sum(v.values()) - 1.0) > 1e-3 or set(v) - set(s.cfg["materials"]):
                    raise DomainError("INVALID_MIX", "burden_mix must reference configured materials and sum to 1", 422)
        oc = op["ore_batch_t"] / max(op["coke_batch_t"], 1e-9)
        lo, hi = lim.get("ore_coke_ratio", [3.2, 6.0])
        if not (lo <= oc <= hi):
            raise DomainError("OUTSIDE_ENVELOPE", f"ore/coke ratio {oc:.2f} outside [{lo}, {hi}]", 422)
        for mat in ch.get("material", {}):
            if mat not in s.cfg["materials"]:
                raise DomainError("UNKNOWN_MATERIAL", f"unknown material {mat}", 422)


def _job_response(job: dict, kind: str = "simulations"):
    return JSONResponse(status_code=202, content={**job, "links": job_links(job["id"], kind)})


@router.get("/scenarios/library", tags=["simulations"], summary="Predefined what-if scenarios (S01-S14)")
def library():
    return {"scenarios": scn.LIBRARY, "kpis": scn.KPI_KEYS}


@router.post("/simulations", status_code=202, response_model=JobInfo, tags=["simulations"],
             responses={**ERR, 202: {"description": "Accepted - poll the job"}, 422: {"model": ErrorResponse}})
def create_simulation(body: SimulationRequest, idempotency_key: Optional[str] = Header(None),
                      p=Depends(metallurgist), s=Depends(ready_svc)):
    if body.scenario_id:
        lib = scn.LIB_INDEX.get(body.scenario_id)
        if lib is None:
            raise DomainError("UNKNOWN_SCENARIO", f"unknown scenario {body.scenario_id}", 422, {"available": list(scn.LIB_INDEX)})
        spec = copy.deepcopy(lib)
        if body.duration_h:
            spec["duration_h"] = body.duration_h
    else:
        spec = {"id": "CUSTOM", "name": body.name or "custom scenario", "type": "simulation",
                "duration_h": body.duration_h or 12.0, "changes": [c.model_dump() for c in body.changes]}
    idem = idempotency_key or body.idempotency_key or ""
    kind = spec.get("type", "simulation")
    with s.lock:
        base = s.twin.clone()
        plant_clone = s.plant.model.clone() if s.plant else None
        cfg = copy.deepcopy(s.cfg)
    if kind == "simulation":
        _check_envelope(s, spec)
        if body.fidelity == "hifi":
            fn = lambda cb: _run_hifi(cfg, spec, body.dt_s, cb)
        else:
            fn = lambda cb: scn.run_simulation(base, spec, dt=body.dt_s, progress=cb)
    elif kind == "sensor":
        fn = lambda cb: studies.sensor_scenario(plant_clone, base, cfg, spec["faults"], spec["duration_h"], progress=cb)
    elif kind == "pareto":
        fn = lambda cb: studies.pareto(s, progress=cb)
    elif kind == "mpc_infeasible":
        fn = lambda cb: studies.infeasible_demo(s)
    else:
        raise DomainError("UNSUPPORTED", f"scenario type {kind}", 422)
    job = s.jobs.submit("simulation:" + kind, {"scenario": spec, "dt_s": body.dt_s, "fidelity": body.fidelity}, fn, p.sub, idem)
    s.audit(p.sub, p.role, "SIMULATION_SUBMITTED", job["id"], {"scenario": spec.get("id"), "fidelity": body.fidelity})
    return _job_response(job)


def _run_hifi(cfg: dict, spec: dict, dt: float, cb):
    """Hi-fidelity run (axial FV, 3 radial sectors): warm-started at the configured operating point
    inside the job (minutes of CPU), then baseline + case as for the fast model."""
    m = BlastFurnaceModel(cfg, fidelity="hifi")
    cb(0.02, "hi-fi warm start (30 h simulated blow-in)")
    m.warm_start(hours=30.0)
    cb(0.3, "hi-fi warm start complete")
    res = scn.run_simulation(m, spec, dt=max(dt, m.dt), progress=lambda p_, msg: cb(0.3 + 0.7 * p_, msg))
    res["fidelity"] = "hifi (28 axial x 3 radial cells)"
    return res


@router.get("/simulations", tags=["simulations"])
def list_simulations(s=Depends(svc), p=Depends(viewer)):
    js = [j for j in s.jobs.jobs.values() if j["kind"].startswith("simulation") or j["kind"].startswith("study")]
    return {"jobs": sorted(js, key=lambda j: j["created_utc"], reverse=True)[:100]}


@router.get("/simulations/{job_id}", response_model=JobInfo, tags=["simulations"], responses={404: {"model": ErrorResponse}})
def get_simulation(job_id: str, s=Depends(svc), p=Depends(viewer)):
    j = s.jobs.get(job_id)
    if not j:
        raise DomainError("NOT_FOUND", "job not found", 404)
    return {**j, "links": job_links(job_id)}


@router.get("/simulations/{job_id}/results", tags=["simulations"], responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def get_results(job_id: str, s=Depends(svc), p=Depends(viewer)):
    j = s.jobs.get(job_id)
    if not j:
        raise DomainError("NOT_FOUND", "job not found", 404)
    if j["status"] in ("PENDING", "RUNNING"):
        raise DomainError("NOT_READY", f"job is {j['status']} ({j['progress'] * 100:.0f} %)", 409, {"progress": j["progress"]})
    res = s.jobs.result(job_id)
    if j["status"] != "SUCCEEDED":
        raise DomainError("JOB_" + j["status"], j["message"], 422, {"error": (res or {}).get("error")})
    return res


@router.delete("/simulations/{job_id}", tags=["simulations"])
def cancel(job_id: str, s=Depends(svc), p=Depends(metallurgist)):
    if not s.jobs.cancel(job_id):
        raise DomainError("NOT_CANCELLABLE", "job not found or already finished", 409)
    return {"id": job_id, "status": "CANCELLING"}


@router.post("/scenarios/compare", status_code=202, response_model=JobInfo, tags=["simulations"])
def compare(body: CompareRequest, idempotency_key: Optional[str] = Header(None), p=Depends(metallurgist), s=Depends(ready_svc)):
    specs = []
    for sid in body.scenario_ids:
        lib = scn.LIB_INDEX.get(sid)
        if lib is None or lib.get("type") != "simulation":
            raise DomainError("UNKNOWN_SCENARIO", f"{sid} is not a simulation scenario", 422)
        specs.append(copy.deepcopy(lib))
        _check_envelope(s, lib)
    with s.lock:
        base = s.twin.clone()
    job = s.jobs.submit("study:compare", body.model_dump(), lambda cb: studies.compare(base, specs, body.duration_h, body.dt_s, cb),
                        p.sub, idempotency_key or body.idempotency_key or "")
    return _job_response(job)


@router.post("/scenarios/sensitivity", tags=["simulations"], responses={202: {"description": "physics method runs as a job"}})
def sensitivity(body: SensitivityRequest, p=Depends(metallurgist), s=Depends(ready_svc)):
    if body.method == "rom":
        if s.rom is None:
            raise DomainError("ROM_UNAVAILABLE", "identify the ROM first (POST /api/v1/mpc/identify)", 409)
        return studies.sensitivity_rom(s.rom)
    with s.lock:
        base = s.twin.clone()
    job = s.jobs.submit("study:sensitivity", body.model_dump(),
                        lambda cb: studies.sensitivity_physics(base, body.horizon_h, progress=cb), p.sub)
    return _job_response(job)
