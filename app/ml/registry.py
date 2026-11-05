"""File-based model registry (metadata JSON + joblib artefact) mirrored into the DB table
model_versions. Every entry records data origin, dataset hash, seed, package versions, metrics,
scope and approval status. Shipped demo models are labelled DEMO-SYNTHETIC / NOT_APPROVED.

joblib artefacts are pickles: `load` deserialises one only when its SHA-256 matches the value
recorded in meta.json at registration, and only from the registry directory (never uploads)."""
from __future__ import annotations

import hashlib
import json
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import joblib


def dataset_hash(df) -> str:
    h = hashlib.sha256()
    h.update(df.to_csv(index=False).encode())
    return h.hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ArtefactIntegrityError(RuntimeError):
    pass


def package_versions() -> Dict[str, str]:
    out = {"python": platform.python_version()}
    for mod in ("numpy", "scipy", "pandas", "sklearn"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:
            out[mod] = "n/a"
    return out


class ModelRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def register(self, model_obj, meta: Dict[str, object]) -> Dict[str, object]:
        mid = meta.get("model_id") or f"{meta['target']}-h{meta['horizon_h']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        d = self.root / mid
        d.mkdir(parents=True, exist_ok=True)
        joblib.dump(model_obj, d / "model.joblib")
        meta = {**meta, "model_id": mid, "created_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "packages": package_versions(), "artifact": "model.joblib", "artifact_sha256": file_sha256(d / "model.joblib")}
        (d / "meta.json").write_text(json.dumps(meta, indent=2, default=float))
        return meta

    def list(self) -> List[Dict[str, object]]:
        out = []
        for p in sorted(self.root.glob("*/meta.json")):
            try:
                out.append(json.loads(p.read_text()))
            except Exception:
                continue
        return out

    def load(self, model_id: str):
        d = self.root / Path(model_id).name                 # no path traversal out of the registry
        meta = json.loads((d / "meta.json").read_text())
        want = meta.get("artifact_sha256")
        got = file_sha256(d / "model.joblib")
        if not want or got != want:
            raise ArtefactIntegrityError(f"model {model_id}: artefact SHA-256 does not match meta.json - not loaded")
        return joblib.load(d / "model.joblib")

    def latest(self, target: str, horizon_h: float) -> Optional[Dict[str, object]]:
        c = [m for m in self.list() if m.get("target") == target and float(m.get("horizon_h")) == float(horizon_h)]
        return sorted(c, key=lambda m: m["created_utc"])[-1] if c else None
