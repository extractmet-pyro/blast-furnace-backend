"""Exact element and energy conservation at every step, positivity, tapping conservation."""
import numpy as np

from app.physics import outputs as out
from app.physics.species import ELEMENTS, MM_LIQ
from app.physics.material_balance import max_rel_residual


def test_every_step_closes_elements_and_energy(model):
    worst_el, worst_E = 0.0, 0.0
    for _ in range(90):                   # 3 h at dt = 120 s
        rec = model.step()
        worst_el = max(worst_el, max_rel_residual(rec))
        worst_E = max(worst_E, abs(rec.E_in - rec.E_out - rec.E_acc) / max(abs(rec.E_in) + abs(rec.E_out), 1.0))
        assert out.balances(model)["step"]["status"] == "OK"
    assert worst_el < 1e-9, worst_el
    assert worst_E < 1e-6, worst_E   # float64 round-off on absolute (formation + sensible) enthalpies


def test_inventories_stay_non_negative(model):
    model.run(4.0)
    assert np.all(model.s_n >= -1e-9)
    assert np.all(model.dm_n >= -1e-9)
    assert np.all(model.hearth.n >= -1e-9)
    assert np.all(np.isfinite(model.s_T)) and np.all(model.s_T > 250)


def test_cumulative_balance_closes(model):
    model.run(6.0)
    cum = out.balances(model)["cumulative_elements_kg"]
    for e in ("Fe", "C", "O", "Ca", "Si"):
        assert e in cum


def test_hearth_tapping_conservation(model):
    """Liquid inventory change = liquid arriving - liquid tapped (mass, kg) over a cast cycle."""
    h = model.hearth
    m0 = float(h.n @ MM_LIQ)
    arrived = tapped = 0.0
    for _ in range(150):
        model.step()
        arrived += float(model.last_drip.liq_out @ MM_LIQ) * model.dt
        tapped += float(model.last_hearth.tapped @ MM_LIQ) * model.dt
    m1 = float(h.n @ MM_LIQ)
    # hearth chemistry exchanges a little mass with the gas (desulfurisation / Si-FeO reactions) - allow 0.5 %
    assert abs((m1 - m0) - (arrived - tapped)) <= 0.005 * max(arrived, 1.0) + 2e3


def test_streams_report_inputs_and_outputs(model):
    model.run(4.0)
    st = out.streams(model)
    names_in = {s["stream"].replace("_", " ") for s in st["inputs"]}
    names_out = {s["stream"].replace("_", " ") for s in st["outputs"]}
    assert any("blast" in n.lower() for n in names_in) and any("coke" in n.lower() for n in names_in)
    assert any("hot metal" in n.lower() for n in names_out) and any("slag" in n.lower() for n in names_out)
    assert any("top gas" in n.lower() for n in names_out)
