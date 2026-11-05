"""Background job manager for CPU-heavy work (simulations, scenarios, ROM identification,
NMPC, observer sensitivities, ML training). Jobs never block the API event loop.

States: PENDING -> RUNNING -> SUCCEEDED | FAILED | CANCELLED (progress 0..1, message).
Persistence: job rows in `simulation_jobs`, results in `scenario_results`. In-process thread
pool by default; a Redis/RQ worker tier is NOT implemented in this release (documented).

Safeguards: at most `max_pending` queued jobs (further submissions get 503 QUEUE_FULL); a job
that waits longer than `queue_timeout_s` before starting, or runs longer than `timeout_s`
(checked cooperatively at every progress report), ends FAILED with a TimeoutError message;
only the newest `keep` finished jobs stay in memory - older ones are served from the database.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from ..core.errors import DomainError
from ..db.models import ScenarioInput, ScenarioResult, SimulationJob
from ..db.session import session

log = logging.getLogger("bf.jobs")

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class JobManager:
    def __init__(self, workers: int = 2, timeout_s: Optional[float] = None, queue_timeout_s: Optional[float] = None,
                 max_pending: Optional[int] = None, keep: Optional[int] = None):
        self.pool = ThreadPoolExecutor(max_workers=max(workers, 1), thread_name_prefix="bf-job")
        self.jobs: Dict[str, dict] = {}
        self.results: Dict[str, dict] = {}
        self.cancel_flags: Dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.timeout_s = float(timeout_s if timeout_s is not None else _env_float("JOB_TIMEOUT_S", 1800.0))
        self.queue_timeout_s = float(queue_timeout_s if queue_timeout_s is not None else _env_float("JOB_QUEUE_TIMEOUT_S", 900.0))
        self.max_pending = int(max_pending if max_pending is not None else _env_float("JOB_MAX_PENDING", 16))
        self.keep = int(keep if keep is not None else _env_float("JOB_KEEP_IN_MEMORY", 200))
        self._queued_at: Dict[str, float] = {}
        self._started_at: Dict[str, float] = {}

    def submit(self, kind: str, request: dict, fn: Callable, user: str, idem: str = "") -> dict:
        with self.lock:
            if idem:
                for j in self.jobs.values():
                    if j["idempotency_key"] == idem and j["kind"] == kind:
                        return j
            pending = sum(1 for j in self.jobs.values() if j["status"] == "PENDING")
            if pending >= self.max_pending:
                raise DomainError("QUEUE_FULL", f"{pending} jobs are already queued; try again when one finishes", 503,
                                  {"max_pending": self.max_pending})
            jid = uuid.uuid4().hex[:16]
            job = {"id": jid, "kind": kind, "status": "PENDING", "progress": 0.0, "message": "queued",
                   "idempotency_key": idem, "request": request, "created_by": user,
                   "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "finished_utc": None}
            self.jobs[jid] = job
            self.cancel_flags[jid] = threading.Event()
            self._queued_at[jid] = time.monotonic()
        try:
            with session() as db:
                db.add(SimulationJob(id=jid, kind=kind, status="PENDING", request=request, created_by=user,
                                     idempotency_key=idem))
                db.flush()          # parent row first: there is no ORM relationship to order the inserts
                db.add(ScenarioInput(job_id=jid, spec=request, input_hash=hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()))
        except Exception as e:      # best effort: the job still runs, but the failure is visible in the log
            log.warning("could not persist job %s: %s", jid, e)
        self.pool.submit(self._run, jid, fn)
        return job

    def _progress(self, jid):
        def cb(p: float, msg: str = ""):
            j = self.jobs[jid]
            j["progress"] = round(float(min(max(p, 0.0), 1.0)), 3)
            j["message"] = msg
            if self.cancel_flags[jid].is_set():
                raise RuntimeError("cancelled by user")
            started = self._started_at.get(jid)
            if started is not None and time.monotonic() - started > self.timeout_s:
                raise TimeoutError(f"job ran longer than JOB_TIMEOUT_S={self.timeout_s:g} s")
        return cb

    def _run(self, jid, fn):
        j = self.jobs[jid]
        now = time.monotonic()
        self._started_at[jid] = now
        j["status"] = "RUNNING"
        j["message"] = "running"
        try:
            waited = now - self._queued_at.get(jid, now)
            if waited > self.queue_timeout_s:
                raise TimeoutError(f"job waited {waited:.0f} s in the queue (JOB_QUEUE_TIMEOUT_S={self.queue_timeout_s:g} s)")
            if self.cancel_flags[jid].is_set():
                raise RuntimeError("cancelled by user before start")
            res = fn(self._progress(jid))
            self.results[jid] = res
            j["status"] = "SUCCEEDED" if res.get("status") not in ("FAILED",) else "FAILED"
            j["progress"] = 1.0
            j["message"] = res.get("message", "done") if isinstance(res.get("message"), str) else "done"
        except Exception as e:
            j["status"] = "CANCELLED" if (isinstance(e, RuntimeError) and "cancelled" in str(e)) else "FAILED"
            j["message"] = f"{type(e).__name__}: {e}"
            self.results[jid] = {"status": j["status"], "error": str(e), "trace": traceback.format_exc()[-2000:]}
        j["finished_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            with session() as db:
                row = db.get(SimulationJob, jid)
                if row:
                    row.status, row.progress, row.message = j["status"], j["progress"], j["message"][:2000]
                    row.finished_at = datetime.now(timezone.utc)
                db.add(ScenarioResult(job_id=jid, result=_json_safe(self.results[jid])))
        except Exception as e:
            log.warning("could not persist result of job %s: %s", jid, e)
        self._prune()

    def _prune(self):
        """Keep only the newest `keep` finished jobs in memory (all of them remain in the database)."""
        with self.lock:
            done = [k for k, v in self.jobs.items() if v["status"] not in ("PENDING", "RUNNING")]
            if len(done) <= self.keep:
                return
            done.sort(key=lambda k: self.jobs[k]["created_utc"])
            for k in done[:len(done) - self.keep]:
                for d in (self.jobs, self.results, self.cancel_flags, self._queued_at, self._started_at):
                    d.pop(k, None)

    def get(self, jid: str) -> Optional[dict]:
        j = self.jobs.get(jid)
        if j is not None:
            return j
        try:
            with session() as db:
                row = db.get(SimulationJob, jid)
                if row is None:
                    return None
                status, message = row.status, row.message
                if status in ("PENDING", "RUNNING"):   # not in memory: it belonged to a previous process
                    status, message = "FAILED", "interrupted by a service restart"
                return {"id": row.id, "kind": row.kind, "status": status, "progress": row.progress,
                        "message": message, "idempotency_key": row.idempotency_key, "request": row.request,
                        "created_by": row.created_by,
                        "created_utc": row.created_at.isoformat().replace("+00:00", "Z") if row.created_at else None,
                        "finished_utc": row.finished_at.isoformat().replace("+00:00", "Z") if row.finished_at else None}
        except Exception:
            return None

    def result(self, jid: str) -> Optional[dict]:
        r = self.results.get(jid)
        if r is not None:
            return r
        try:
            with session() as db:
                row = db.query(ScenarioResult).filter(ScenarioResult.job_id == jid).order_by(ScenarioResult.id.desc()).first()
                return row.result if row is not None else None
        except Exception:
            return None

    def cancel(self, jid: str) -> bool:
        if jid in self.cancel_flags and self.jobs[jid]["status"] in ("PENDING", "RUNNING"):
            self.cancel_flags[jid].set()
            return True
        return False


def _json_safe(o):
    import math
    import numpy as np
    if isinstance(o, dict):
        return {str(k): _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.ndarray):
        return _json_safe(o.tolist())
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return o
