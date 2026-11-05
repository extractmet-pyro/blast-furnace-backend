"""Shared fixtures. Tests run fully offline on synthetic data (no network, no plant)."""
import gzip
import os
import pickle
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_TMP = tempfile.mkdtemp(prefix="bftest_")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("DATA_DIR", _TMP)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/test.db")
os.environ.setdefault("DEMO_USER_PASSWORD", "test-demo-password-123")
os.environ.setdefault("JWT_SECRET", "unit-test-secret-" + "x" * 32)
os.environ.setdefault("SIM_SPEED", "240")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "100000")
os.environ.setdefault("CORS_ORIGINS", "http://localhost:5173,https://bf-frontend.example.com")
os.environ.setdefault("EDGE_INGEST_TOKEN", "edge-test-token-0123456789")

PASSWORD = os.environ["DEMO_USER_PASSWORD"]


def _load_state():
    from app.physics.defaults import build_config
    from app.physics.integrator import BlastFurnaceModel
    p = ROOT / "data" / "demo" / "initial_state_large_4000.pkl.gz"
    if p.exists():
        with gzip.open(p, "rb") as f:
            return pickle.load(f)
    m = BlastFurnaceModel(build_config("large_4000"))
    m.warm_start(hours=36.0)
    return m


@pytest.fixture(scope="session")
def base_model():
    return _load_state()


@pytest.fixture()
def model(base_model):
    return base_model.clone()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    import time
    with TestClient(app) as c:
        for _ in range(600):
            if c.get("/api/v1/system/status").json()["state"] == "RUNNING":
                break
            time.sleep(0.5)
        yield c


def _token(client, user):
    r = client.post("/api/v1/auth/login", json={"username": user, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["access_token"]}


@pytest.fixture(scope="session")
def auth(client):
    return {u: _token(client, u) for u in ("viewer", "operator", "metallurgist", "engineer", "admin")}
