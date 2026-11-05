"""Balance / energy / carbon / economics reports and exports (CSV, XLSX, JSON) with provenance."""
from __future__ import annotations

import json
from typing import Literal, Optional

import pandas as pd
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...core.errors import DomainError
from ...data.csv_excel import frame_to_csv_bytes, frame_to_xlsx_bytes
from ...services import reporting as rp
from ...services.jobs import _json_safe
from .deps import ready_svc, viewer

router = APIRouter()


@router.get("/reports/balance", tags=["reports"], summary="Elemental + energy balance, streams (compositions & weights)")
def balance(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(rp.balance_report(s))


@router.get("/reports/energy", tags=["reports"])
def energy(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(rp.energy_report(s))


@router.get("/reports/carbon", tags=["reports"])
def carbon(boundary: Optional[Literal["BF_GATE_TO_GATE", "BF_PLUS_UTILITIES", "BF_PLUS_UPSTREAM"]] = None,
           s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(rp.carbon_report(s, boundary))


@router.get("/reports/economics", tags=["reports"])
def economics(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        return _json_safe(rp.economics_report(s))


@router.get("/reports/export", tags=["reports"], summary="Download balance / KPI / telemetry data as CSV, XLSX or JSON")
def export(kind: Literal["balance", "kpis", "telemetry", "streams"] = "balance",
           fmt: Literal["csv", "xlsx", "json"] = Query("csv", alias="format"), hours: float = Query(24, gt=0, le=48),
           s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        prov = {k: str(v) for k, v in rp.provenance(s).items()}
        if kind == "balance":
            frames = rp.balance_frames(s)
        elif kind == "streams":
            frames = {"streams": rp.balance_frames(s)["streams"], "compositions": rp.balance_frames(s)["compositions"]}
        elif kind == "kpis":
            frames = {"kpis": rp.kpi_frame(s, hours)}
        else:
            frames = {"telemetry": rp.telemetry_frame(s, hours)}
    prov["units"] = "see column names (unit suffixes) and /api/v1/tags"
    name = f"bf_{kind}_{prov['data_time_utc'].replace(':', '').replace('-', '')}"
    if fmt == "json":
        body = json.dumps(_json_safe({"provenance": prov, **{k: v.to_dict(orient="records") for k, v in frames.items()}}))
        return Response(body, media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
    if fmt == "xlsx":
        return Response(frame_to_xlsx_bytes(frames, prov),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
    if len(frames) == 1:
        df = next(iter(frames.values()))
    else:
        df = pd.concat([f.assign(table=k) for k, f in frames.items()], ignore_index=True)
    return Response(frame_to_csv_bytes(df, prov), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
