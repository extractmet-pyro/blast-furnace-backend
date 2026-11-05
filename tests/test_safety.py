"""Safety case: no application code path can write to a PLC/DCS; acknowledgement is audit-only;
plant-write and supervised-MPC switches are refused."""
import ast
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
FORBIDDEN_CALLS = {"write_value", "write_values", "set_value", "write_attribute", "write_attributes", "call_method",
                   "write_register", "write_registers", "write_coil", "write_coils", "db_write", "write_area",
                   "set_attribute", "write_multiple_registers", "write_single_register"}
FORBIDDEN_IMPORTS = {"pymodbus", "snap7", "pycomm3", "pylogix", "opcua"}   # asyncua allowed only in read-only collector


def _py_files():
    return [p for p in APP.rglob("*.py")]


def test_no_plant_write_calls_anywhere():
    hits = []
    for p in _py_files():
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                if name in FORBIDDEN_CALLS:
                    hits.append(f"{p.relative_to(APP)}:{node.lineno} {name}")
    assert not hits, hits


def test_no_industrial_protocol_write_libraries_imported():
    hits = []
    for p in _py_files():
        tree = ast.parse(p.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            hits += [f"{p.relative_to(APP)}:{node.lineno} {m}" for m in mods if m in FORBIDDEN_IMPORTS]
    assert not hits, hits


def test_opcua_collector_is_read_only():
    src = (APP / "data" / "opcua_readonly.py").read_text()
    calls = set(re.findall(r"\.(\w+)\(", src))
    assert "read_data_value" in calls
    assert not (calls & FORBIDDEN_CALLS)


def test_plant_write_flag_refuses_startup(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("ALLOW_PLANT_WRITES", "true")
    get_settings.cache_clear()
    try:
        from fastapi.testclient import TestClient
        from app.main import create_app
        with pytest.raises(RuntimeError):
            with TestClient(create_app()):
                pass
    finally:
        monkeypatch.delenv("ALLOW_PLANT_WRITES", raising=False)
        get_settings.cache_clear()


def test_non_demo_mode_requires_strong_jwt_secret(monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("JWT_SECRET", "too-short")
    get_settings.cache_clear()
    try:
        from fastapi.testclient import TestClient
        from app.main import create_app
        with pytest.raises(RuntimeError, match="JWT_SECRET"):
            with TestClient(create_app()):
                pass
    finally:
        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.delenv("JWT_SECRET", raising=False)
        get_settings.cache_clear()


def test_supervised_mode_refused_in_cloud():
    from app.core.config import get_settings
    from app.core.errors import DomainError
    from app.core.safety import SafetyPolicy
    pol = SafetyPolicy(get_settings())
    with pytest.raises(DomainError):
        pol.check_mode_transition("ADVISORY_SHADOW_MPC", "SUPERVISED_MPC", "ADMIN", True)


def test_no_endpoint_writes_to_plant(client):
    paths = client.get("/openapi.json").json()["paths"]
    bad = [p for p in paths if re.search(r"(write|actuat|plc|dcs|setpoint-transfer|opc)", p, re.I)]
    assert not bad, bad


def test_tampered_model_artefact_is_not_deserialised(tmp_path):
    """joblib/pickle artefacts are loaded only when their SHA-256 matches the registry metadata."""
    from app.ml.registry import ArtefactIntegrityError, ModelRegistry
    reg = ModelRegistry(tmp_path)
    meta = reg.register({"weights": [1, 2, 3]}, {"target": "hmt", "horizon_h": 1.0})
    assert reg.load(meta["model_id"]) == {"weights": [1, 2, 3]}
    (tmp_path / meta["model_id"] / "model.joblib").write_bytes(b"not the registered artefact")
    with pytest.raises(ArtefactIntegrityError):
        reg.load(meta["model_id"])


def test_shipped_artefacts_carry_integrity_hashes():
    import hashlib
    import json
    demo = ROOT / "data" / "demo"
    for side in demo.glob("initial_state_*.json"):
        snap = demo / (side.name[:-5] + ".pkl.gz")
        assert json.loads(side.read_text())["file_sha256"] == hashlib.sha256(snap.read_bytes()).hexdigest()
    for meta in (ROOT / "data" / "models").glob("*/meta.json"):
        art = meta.parent / "model.joblib"
        assert json.loads(meta.read_text())["artifact_sha256"] == hashlib.sha256(art.read_bytes()).hexdigest()
