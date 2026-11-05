"""Tags, data upload/quality, edge ingestion, telemetry history and the event stream (WS + polling)."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, WebSocket, WebSocketDisconnect

from ...core.config import get_settings
from ...core.errors import DomainError
from ...core.safety import Mode
from ...core.security import Principal, decode_token
from ...data.csv_excel import UploadError, parse_history
from ...data.synthetic import EPOCH, iso
from ...data.tag_mapping import TAG_INDEX, TAGS
from ...db.models import TagDefinition
from ...db.session import session
from ...schemas import EdgeBatch, ErrorResponse, TagMapRequest
from ...services.reporting import downsample
from .deps import ERR, edge_principal, engineer, metallurgist, ready_svc, svc, viewer

router = APIRouter()


@router.get("/tags", tags=["data"], summary="Tag catalogue (data dictionary) with installation and mapping status")
def tags(s=Depends(svc), p=Depends(viewer)):
    mp = {v["tag"]: {"site_tag": k, **v} for k, v in s.mapper.mapping.items()}
    return {"tags": [{**t.as_dict(), "mapping": mp.get(t.tag)} for t in TAGS],
            "families": sorted({t.family for t in TAGS}),
            "note": "Tags not installed are reported as UNAVAILABLE - never as zero."}


@router.post("/tags/map", tags=["data"], responses=ERR)
def map_tags(body: TagMapRequest, p: Principal = Depends(engineer), s=Depends(svc)):
    from ...core.units import UNITS, UnitError, convert
    done = []
    for m in body.mappings:
        td = TAG_INDEX.get(m.catalogue_tag)
        if td is None:
            raise DomainError("UNKNOWN_TAG", f"unknown catalogue tag {m.catalogue_tag}", 422)
        scale, offset = m.scale, m.offset
        canon = {"kPa(g)": "kPa_g", "degC": "degC", "t/h": "t/h", "Nm3/min": "Nm3/min", "Nm3/h": "Nm3/h", "kW": "kW",
                 "g/Nm3": "g/Nm3"}.get(td.unit)
        if m.site_unit != td.unit and canon and m.site_unit in UNITS:
            try:  # derive linear conversion site unit -> catalogue unit
                a = convert(1.0, m.site_unit, canon) - convert(0.0, m.site_unit, canon)
                b = convert(0.0, m.site_unit, canon)
                scale, offset = a * m.scale, a * m.offset + b
            except UnitError as e:
                raise DomainError("UNIT_MISMATCH", f"{m.site_tag}: {e}", 422)
        elif m.site_unit != td.unit and (m.scale == 1.0 and m.offset == 0.0):
            raise DomainError("UNIT_MISMATCH", f"{m.site_tag}: unit '{m.site_unit}' differs from catalogue unit '{td.unit}' and "
                                               "no automatic conversion exists - provide scale/offset explicitly", 422)
        s.mapper.set(m.site_tag, m.catalogue_tag, m.site_unit, scale, offset)
        done.append({"site_tag": m.site_tag, "catalogue_tag": m.catalogue_tag, "site_unit": m.site_unit,
                     "catalogue_unit": td.unit, "scale": scale, "offset": offset})
        try:
            with session() as db:
                pid = s.cfg["plant"]["id"]
                row = db.query(TagDefinition).filter_by(plant_id=pid, tag=m.catalogue_tag).first()
                if row is None:
                    row = TagDefinition(plant_id=pid, tag=m.catalogue_tag, unit=td.unit, definition=td.as_dict())
                    db.add(row)
                row.site_tag = m.site_tag
                row.definition = {**td.as_dict(), "site_unit": m.site_unit, "scale": scale, "offset": offset}
        except Exception as e:      # mapping stays active in memory; the failure is logged, not hidden
            logging.getLogger("bf.api").warning("tag mapping not persisted: %s", e)
    s.audit(p.sub, p.role, "TAG_MAPPING", f"{len(done)} tags", {"mappings": done})
    return {"mapped": done, "mapping_table": s.mapper.as_list()}


@router.post("/data/upload", tags=["data"], responses={**ERR, 413: {"model": ErrorResponse}})
async def upload(file: UploadFile = File(...), use_for: str = Form("validate"), p: Principal = Depends(metallurgist),
                 s=Depends(svc)):
    """Upload historian data (.csv/.csv.gz/.xlsx). use_for = validate | replay."""
    st = get_settings()
    content = await file.read(int(st.max_upload_mb * 1024 * 1024) + 1)
    if len(content) > st.max_upload_mb * 1024 * 1024:
        raise DomainError("PAYLOAD_TOO_LARGE", f"file exceeds {st.max_upload_mb} MB", 413)
    try:
        df, summary = parse_history(content, file.filename or "upload.csv", s.mapper)
    except UploadError as e:
        raise DomainError("INVALID_UPLOAD", str(e), 422, e.details)
    except Exception as e:
        raise DomainError("INVALID_UPLOAD", f"could not parse file: {type(e).__name__}", 422)
    s.audit(p.sub, p.role, "DATA_UPLOAD", file.filename or "", {"rows": summary["rows"], "use_for": use_for,
                                                               "mapped": summary["mapped_columns"]})
    if use_for == "replay":
        s.require_ready()
        s.load_replay_frame(df)
        summary["replay"] = "loaded - switch mode to HISTORICAL_REPLAY (POST /api/v1/system/mode)"
    return {"summary": summary, "data_origin": "HISTORICAL_FILE (uploaded)"}


@router.get("/data/quality", tags=["data"], summary="Per-tag quality state with reasons, score, reconciliation")
def data_quality(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        now = iso(s.data_t)
        return {"t_utc": now, "score": s.quality.score(now), "tags": s.quality.snapshot(now),
                "reconciliation": s.recon, "active_faults_demo": [vars(f) for f in (s.plant.faults if s.plant else [])
                                                                  if f.end_h > s.plant.model.t / 3600.0]}


@router.post("/data/ingest", tags=["data"], summary="Read-only edge gateway push (service identity, LIVE_MONITOR_ONLY)")
def ingest(body: EdgeBatch, p: Principal = Depends(edge_principal), s=Depends(ready_svc)):
    if s.mode != Mode.LIVE_MONITOR_ONLY.value:
        raise DomainError("NOT_LIVE_MODE", "Edge data is accepted only in LIVE_MONITOR_ONLY mode", 409)
    return s.ingest_edge([r.model_dump() for r in body.readings])


@router.get("/telemetry/latest", tags=["telemetry"])
def telemetry_latest(s=Depends(ready_svc), p=Depends(viewer)):
    with s.lock:
        now = iso(s.data_t)
        snap = s.quality.snapshot(now)
        return {"t_utc": now, "mode": s.mode, "data_origin": s.status()["data_origin"],
                "tags": snap, "label": "MEASURED values from the active data source (synthetic sensors in demo mode)"}


@router.get("/telemetry/history", tags=["telemetry"])
def telemetry_history(start: Optional[str] = None, end: Optional[str] = None, tags: Optional[str] = None,
                      max_points: int = Query(600, ge=10, le=5000), s=Depends(ready_svc), p=Depends(viewer)):
    def t_parse(x):
        try:
            return datetime.fromisoformat(x.replace("Z", "+00:00")).timestamp() - EPOCH.timestamp()
        except ValueError:
            raise DomainError("INVALID_TIME", f"invalid ISO timestamp {x}", 422)
    t0 = t_parse(start) if start else None
    t1 = t_parse(end) if end else None
    if t0 is not None and t1 is not None and t1 <= t0:
        raise DomainError("INVALID_RANGE", "end must be after start", 422)
    want = [t.strip() for t in tags.split(",")] if tags else None
    if want:
        unknown = [t for t in want if t not in TAG_INDEX]
        if unknown:
            raise DomainError("UNKNOWN_TAG", f"unknown tags {unknown}", 422)
    with s.lock:
        rows = [r for r in s.telemetry if (t0 is None or r["t_s"] >= t0) and (t1 is None or r["t_s"] <= t1)]
    if want:
        rows = [{k: v for k, v in r.items() if k in ("t_s", "t_utc") or k.split("__")[0] in want} for r in rows]
    total = len(rows)
    rows = downsample(rows, max_points)
    return {"rows": rows, "total_rows": total, "returned": len(rows), "downsampled": total > len(rows),
            "units": {t.tag: t.unit for t in TAGS if not want or t.tag in want},
            "note": "Values as received with quality flags (<tag>__q); lab values at receipt time with <tag>__t_sample."}


@router.get("/stream/poll", tags=["telemetry"], summary="Polling fallback for the event stream")
def stream_poll(since_seq: int = 0, limit: int = Query(200, ge=1, le=900), s=Depends(svc), p=Depends(viewer)):
    with s.lock:
        evs = [e for e in s.stream if e["seq"] > since_seq]
        oldest = s.stream[0]["seq"] if s.stream else s.seq
    resync = since_seq > 0 and since_seq < oldest - 1
    return {"events": evs[-limit:], "latest_seq": s.seq, "oldest_seq": oldest, "resync_required": resync,
            "server_time_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


_ws_conns = defaultdict(int)


@router.websocket("/stream")
async def stream_ws(ws: WebSocket, token: Optional[str] = None, since_seq: int = 0):
    """Authenticated event stream: hello -> backlog since `since_seq` -> live events, heartbeat 15 s.
    Client messages: {"type": "resync", "since_seq": N} | {"type": "ping"}."""
    st = get_settings()
    host = ws.client.host if ws.client else "unknown"
    principal = None
    if token:
        try:
            pl = decode_token(token)
            principal = Principal(pl["sub"], pl["role"])
        except Exception:
            await ws.close(code=4401)
            return
    elif st.allow_anonymous_viewer:
        principal = Principal("anonymous", "VIEWER", anonymous=True)
    if principal is None:
        await ws.close(code=4401)
        return
    origin = ws.headers.get("origin")
    if origin and st.cors_origins and origin.rstrip("/") not in st.cors_origins and "*" not in st.cors_origins:
        await ws.close(code=4403)
        return
    if _ws_conns[host] >= 5:
        await ws.close(code=4429)
        return
    _ws_conns[host] += 1
    s = svc()
    try:
        await ws.accept()
        await ws.send_json({"type": "hello", "seq": s.seq, "user": principal.as_dict(), "heartbeat_s": 15,
                            "resync": "send {\"type\":\"resync\",\"since_seq\":N}"})
        last = since_seq
        last_hb = time.monotonic()
        msg_budget, budget_t = 30, time.monotonic()
        while True:
            with s.lock:
                oldest = s.stream[0]["seq"] if s.stream else s.seq
                evs = [e for e in s.stream if e["seq"] > last]
            if last and last < oldest - 1:
                await ws.send_json({"type": "resync_required", "oldest_seq": oldest, "latest_seq": s.seq,
                                    "hint": "reload snapshot via REST (/api/v1/twin/state) then resume from latest_seq"})
                last = s.seq
                evs = []
            for e in evs[-200:]:
                await ws.send_json(e)
                last = e["seq"]
            if time.monotonic() - last_hb > 15:
                await ws.send_json({"type": "heartbeat", "seq": s.seq, "server_time_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                    "state": s.state, "mode": s.mode})
                last_hb = time.monotonic()
            try:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=1.0)
                if time.monotonic() - budget_t > 60:
                    msg_budget, budget_t = 30, time.monotonic()
                msg_budget -= 1
                if msg_budget < 0:
                    await ws.close(code=4429)
                    return
                if isinstance(msg, dict) and msg.get("type") == "resync":
                    last = int(msg.get("since_seq", 0))
                elif isinstance(msg, dict) and msg.get("type") == "ping":
                    await ws.send_json({"type": "pong", "seq": s.seq})
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await ws.close(code=1011)
        except Exception:
            pass
    finally:
        _ws_conns[host] = max(_ws_conns[host] - 1, 0)
