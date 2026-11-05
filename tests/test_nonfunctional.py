"""Non-functional checks: rate limiting, job-queue safeguards, offline operation, performance.

The performance bounds are deliberately generous (CI runners vary); the measured values are
printed with `-s` and recorded in docs/TEST_RESULTS.md.
"""
import socket
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.core.errors import DomainError
from app.core.security import RateLimiter
from app.services.jobs import JobManager


def _until(cond, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _finished(jm, jid, timeout=20.0):
    ok = _until(lambda: (jm.get(jid) or {}).get("status") not in (None, "PENDING", "RUNNING"), timeout)
    assert ok, "job did not finish"
    return jm.get(jid)


def test_rate_limiter_returns_429_with_retry_after():
    async def ok(request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/x", ok), Route("/health", ok)])
    app.add_middleware(RateLimiter, per_min=3)
    c = TestClient(app)
    codes = [c.get("/x").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = c.get("/x")
    assert r.json()["error"]["code"] == "RATE_LIMITED" and r.headers.get("Retry-After")
    assert c.get("/health").status_code == 200          # health checks are never limited


def test_job_timeout_ends_failed_with_reason(client):
    jm = JobManager(workers=1, timeout_s=0.3)

    def slow(cb):
        for i in range(300):
            cb(i / 300, "working")
            time.sleep(0.01)
        return {"status": "OK"}

    j = _finished(jm, jm.submit("test:slow", {}, slow, "pytest")["id"])
    assert j["status"] == "FAILED" and "JOB_TIMEOUT_S" in j["message"]


def test_job_queue_is_capped(client):
    jm = JobManager(workers=1, max_pending=2)
    gate = threading.Event()
    first = jm.submit("test:block", {}, lambda cb: (gate.wait(10), {"status": "OK"})[1], "pytest")
    assert _until(lambda: jm.get(first["id"])["status"] == "RUNNING", 5)
    queued = [jm.submit("test:q", {"i": i}, lambda cb: {"status": "OK"}, "pytest") for i in range(2)]
    with pytest.raises(DomainError) as e:
        jm.submit("test:q", {"i": 3}, lambda cb: {"status": "OK"}, "pytest")
    assert e.value.code == "QUEUE_FULL" and e.value.status_code == 503
    gate.set()
    for x in [first, *queued]:
        assert _finished(jm, x["id"])["status"] == "SUCCEEDED"


def test_job_that_waits_too_long_in_the_queue_fails(client):
    jm = JobManager(workers=1, queue_timeout_s=0.2)
    gate = threading.Event()
    first = jm.submit("test:block", {}, lambda cb: (gate.wait(10), {"status": "OK"})[1], "pytest")
    late = jm.submit("test:q", {}, lambda cb: {"status": "OK"}, "pytest")
    time.sleep(0.5)
    gate.set()
    assert _finished(jm, first["id"])["status"] == "SUCCEEDED"
    j = _finished(jm, late["id"])
    assert j["status"] == "FAILED" and "queue" in j["message"]


def test_finished_jobs_leave_memory_but_stay_retrievable(client):
    jm = JobManager(workers=1, keep=2)
    ids = []
    for i in range(5):
        jid = jm.submit("test:quick", {"i": i}, lambda cb, i=i: {"status": "OK", "value": i}, "pytest")["id"]
        _finished(jm, jid)
        ids.append(jid)
    assert _until(lambda: len(jm.results) <= 2 and ids[0] not in jm.jobs, 5)
    old = jm.get(ids[0])                                  # served from the database
    assert old is not None and old["status"] == "SUCCEEDED"
    assert jm.result(ids[0])["value"] == 0


def test_demo_runs_without_network(client, auth, model, monkeypatch):
    def refuse(*a, **k):
        raise OSError("network disabled by test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for _ in range(10):
        model.step()
    assert client.get("/api/v1/system/status").status_code == 200
    assert client.get("/api/v1/twin/state", headers=auth["viewer"]).status_code == 200
    assert client.get("/api/v1/reports/balance", headers=auth["viewer"]).status_code == 200


def test_step_time_and_api_latency_under_synthetic_load(client, auth, model):
    n = 30
    t0 = time.perf_counter()
    for _ in range(n):
        model.step()
    per_step = (time.perf_counter() - t0) / n
    lat = []
    for _ in range(40):
        t = time.perf_counter()
        r = client.get("/api/v1/twin/state", headers=auth["viewer"])
        lat.append(time.perf_counter() - t)
        assert r.status_code == 200
    lat.sort()
    p95 = lat[int(0.95 * len(lat)) - 1]
    print(f"\nPERF large preset: {per_step * 1000:.0f} ms per twin step (dt {model.dt:.0f} s); "
          f"GET /api/v1/twin/state p95 {p95 * 1000:.0f} ms with the live loop running")
    assert per_step < 1.5          # measured about 0.25 s on 2 vCPU with the live loop competing
    assert p95 < 2.0               # measured about 0.5 s (requests wait for the twin lock)
