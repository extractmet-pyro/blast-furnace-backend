"""Chemical limiting cases: zero PCI, zero O2 enrichment, metallic HBI burden, dry vs wet top gas."""
import numpy as np
import pytest

from app.physics import outputs as out


def _run(model, hours=3.0, dt=240.0):
    for _ in range(int(hours * 3600 / dt)):
        model.step(dt)
        assert out.balances(model)["step"]["status"] == "OK"
    return out.kpis(model)


def test_zero_pci(model):
    raft0 = out.kpis(model)["raft_C"]["value"]
    model.op["pci_t_h"] = 0.0
    k = _run(model, hours=4.5)               # specific rates are 4-h window averages
    assert k["pci_rate_kg_tHM"]["value"] == pytest.approx(0.0, abs=0.5)
    assert k["raft_C"]["value"] > raft0 + 50          # no injectant cooling of the raceway


def test_zero_oxygen_enrichment(model):
    raft0 = out.kpis(model)["raft_C"]["value"]
    model.op["o2_enrichment_pct"] = 0.0
    k = _run(model)
    assert k["raft_C"]["value"] < raft0 - 50
    assert k["top_N2_pct"]["value"] > 40


def test_metallic_hbi_burden(model):
    """HBI is a metallic feed: metallic Fe + residual FeO/Fe3O4 + C; the residual oxide is reduced in the shaft."""
    mix = dict(model.op["burden_mix"])
    assert "hbi" in model.cfg["materials"]
    take = 0.15
    mix = {k: v * (1 - take) for k, v in mix.items()}
    mix["hbi"] = mix.get("hbi", 0.0) + take
    model.op["burden_mix"] = mix
    k = _run(model, hours=4.0)
    assert k["production_t_d"]["value"] > 0
    chem = model.cfg["materials"]["hbi"]["chem"]
    assert chem.get("Fe_met", 0) > 70 and (chem.get("FeO", 0) + chem.get("Fe3O4", 0) + chem.get("Fe2O3", 0)) > 0


def test_dry_and_wet_top_gas_consistent(model):
    model.run(1.0)
    tg = out.top_gas(model)
    dry, wet = tg["dry_vol_pct"], tg["wet_vol_pct"]
    assert sum(dry.values()) == pytest.approx(100.0, abs=0.05)
    assert sum(wet.values()) == pytest.approx(100.0, abs=0.05)
    h2o = wet.get("H2O", 0.0)
    assert h2o > 0
    assert dry["CO"] == pytest.approx(wet["CO"] / (1 - h2o / 100.0), rel=2e-3)


def test_steady_state_consistency(model):
    """Over 6 h at constant inputs the 4-h production and fuel rates stay within a few percent."""
    model.run(3.0)
    k1 = out.kpis(model)
    model.run(3.0)
    k2 = out.kpis(model)
    for key, tol in (("production_t_d", 0.05), ("fuel_rate_kg_tHM", 0.03), ("eta_CO_pct", 0.03)):
        a, b = k1[key]["value"], k2[key]["value"]
        assert abs(b - a) / abs(a) < tol, (key, a, b)
