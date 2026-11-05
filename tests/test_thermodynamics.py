"""Thermochemical property sanity: Cp > 0, enthalpy monotonic, equilibrium trends, slag properties."""
import numpy as np
import pytest

from app.physics import slag_metal as sm
from app.physics import thermodynamics as th
from app.physics.species import TT_GAS, TT_LIQ, TT_SOLID

T = np.linspace(400.0, 2200.0, 37)


@pytest.mark.parametrize("tt", [TT_GAS, TT_SOLID, TT_LIQ])
def test_enthalpy_increases_with_temperature(tt):
    h = np.array([tt.h(t) for t in T])
    assert np.all(np.diff(h, axis=0) > 0), "sensible enthalpy must increase with T for every species"
    assert np.all(np.isfinite(h))


def test_boudouard_equilibrium_shifts_to_co_at_high_t():
    K_lo, K_hi = th.K("boud", 900.0), th.K("boud", 1300.0)
    assert K_hi > 100 * K_lo


def test_baur_glaessner_fe_feo_boundary():
    bg = th.baur_glaessner(1.0)
    y = np.interp(1000.0, np.array(bg["T_C"]), np.array(bg["wus_CO"]))
    assert 60.0 < y < 80.0, y   # % CO in CO+CO2 at the wustite/iron boundary near 1000 degC (~70 %)


def test_carbon_saturation_range():
    c1450 = sm.carbon_saturation(1450.0, {"Si": 0.4, "Mn": 0.3, "P": 0.1, "S": 0.03})
    c1550 = sm.carbon_saturation(1550.0, {"Si": 0.4, "Mn": 0.3, "P": 0.1, "S": 0.03})
    assert 4.3 < c1450 < 5.6 and c1550 > c1450


def test_slag_viscosity_decreases_with_temperature():
    w = {"CaO": 40.0, "SiO2": 34.0, "Al2O3": 15.0, "MgO": 9.0, "FeO": 0.5, "MnO": 0.3}
    assert sm.urbain_viscosity(w, 1773.0) < sm.urbain_viscosity(w, 1673.0)


def test_sulfide_capacity_increases_with_basicity():
    lo = sm.sulfide_capacity(sm.optical_basicity({"CaO": 36, "SiO2": 40, "Al2O3": 15, "MgO": 8}), 1773.0)
    hi = sm.sulfide_capacity(sm.optical_basicity({"CaO": 44, "SiO2": 32, "Al2O3": 15, "MgO": 8}), 1773.0)
    assert hi > lo > 0


def test_reaction_catalogue_complete():
    tab = [r for r in th.reaction_catalogue_table((1000.0, 1500.0)) if "name" in r]
    assert len(tab) >= 15
    for r in tab:
        assert all(np.isfinite(v) for v in r["dH_kJ"])
