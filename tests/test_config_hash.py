"""Artefact matching: shipped initial states and the identified ROM are reused only for the physics they were
made with, whatever the number formatting of the configuration (a browser writes 11.0 as 11)."""
import copy
import json
from pathlib import Path

from app.physics.defaults import PRESETS, build_config
from app.services.twin_service import _artefact_matches, physics_hash

DEMO = Path(__file__).resolve().parents[1] / "data" / "demo"


def _like_javascript(o):
    """JSON.stringify drops the fractional part of integral floats."""
    if isinstance(o, float) and o.is_integer():
        return int(o)
    if isinstance(o, dict):
        return {k: _like_javascript(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_like_javascript(v) for v in o]
    return o


def test_physics_hash_ignores_number_formatting_and_non_physics_edits():
    cfg = build_config("large_4000")
    assert physics_hash(_like_javascript(json.loads(json.dumps(cfg)))) == physics_hash(cfg)
    edited = copy.deepcopy(cfg)
    edited["targets"]["hmt_C"] = [1490.0, 1530.0]
    edited["economics"] = {**edited.get("economics", {}), "note": "price review"}
    assert physics_hash(edited) == physics_hash(cfg)
    changed = copy.deepcopy(cfg)
    changed["geometry"]["throat_diameter_m"] += 0.1
    assert physics_hash(changed) != physics_hash(cfg)


def test_shipped_artefacts_match_their_presets():
    for preset in PRESETS:
        meta = json.loads((DEMO / f"initial_state_{preset}.json").read_text())
        assert _artefact_matches(meta, build_config(preset)), preset
        assert _artefact_matches(meta, _like_javascript(build_config(preset))), preset
