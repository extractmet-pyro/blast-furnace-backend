"""Response sign/delay sanity, numeric stability over a long horizon, Ergun pressure-drop monotonicity."""
import numpy as np

from app.physics import outputs as out


def _avg(model, hours, keys, dt=240.0):
    acc = []
    for _ in range(int(hours * 3600 / dt)):
        model.step(dt)
        k = out.kpis(model)
        acc.append([k[c]["value"] for c in keys])
    return np.mean(np.array(acc, dtype=float), axis=0)


def _pair(base_model, mv, du, hours=8.0, keys=("hm_si_pct", "hmt_C", "raft_C")):
    a = base_model.clone()
    b = base_model.clone()
    b.op[mv] += du
    a.run(hours - 2.0, dt=240.0)
    b.run(hours - 2.0, dt=240.0)
    return _avg(b, 2.0, keys) - _avg(a, 2.0, keys)


def test_blast_temperature_heats_lower_zone(base_model):
    d = _pair(base_model, "blast_temp_C", +40.0)
    assert d[2] > 10.0          # RAFT up (immediate)
    assert d[0] > 0.01          # Si up within hours (thermal state)


def test_blast_moisture_cools_lower_zone(base_model):
    d = _pair(base_model, "blast_moisture_g_Nm3", +8.0)
    assert d[2] < -10.0         # RAFT down (water-gas reaction endothermic)
    assert d[0] < -0.01         # Si down


def test_oxygen_enrichment_raises_raft_and_production(base_model):
    d = _pair(base_model, "o2_enrichment_pct", +1.0, keys=("raft_C", "production_t_d", "hm_si_pct"))
    assert d[0] > 20.0 and d[1] > 50.0


def test_long_horizon_numerical_stability(model):
    for _ in range(int(24 * 3600 / 300.0)):
        model.step(300.0)
        assert np.all(np.isfinite(model.s_T)) and np.isfinite(model.dm_T)
    k = out.kpis(model)
    assert 1350 < k["hmt_C"]["value"] < 1650
    assert out.balances(model)["step"]["status"] == "OK"


def test_pressure_drop_increases_with_blast_volume(base_model):
    dps = []
    for f in (0.94, 1.0, 1.06):
        m = base_model.clone()
        m.op["blast_volume_Nm3_min"] *= f
        m.run(1.0, dt=240.0)
        dps.append(out.kpis(m)["pressure_drop_kPa"]["value"])
    assert dps[0] < dps[1] < dps[2], dps


def test_burden_descent_delay(base_model):
    """A burden change acts on the hearth only after the solids travel time (no instantaneous effect)."""
    a, b = base_model.clone(), base_model.clone()
    mix = dict(b.op["burden_mix"])
    b.op["coke_batch_t"] *= 1.04
    a.run(1.0, dt=240.0)
    b.run(1.0, dt=240.0)
    d = abs(out.kpis(b)["hmt_C"]["value"] - out.kpis(a)["hmt_C"]["value"])
    assert d < 2.0, d
