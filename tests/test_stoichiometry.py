"""Reaction matrices conserve every element; molar masses and reaction enthalpy signs are sane."""
import numpy as np
import pytest

from app.physics.reactions import COMPILED, REACTIONS
from app.physics.species import ELEMENTS, SPECIES
from app.physics.thermodynamics import element_residual, reaction_props


@pytest.mark.parametrize("name", sorted(REACTIONS))
def test_reaction_conserves_elements(name):
    res = element_residual(REACTIONS[name].stoich)
    assert all(abs(v) < 1e-9 for v in res.values()), (name, res)


def test_compiled_transfers_conserve_elements():
    """The phase-indexed compiled vectors used by the integrator reproduce a conserving stoichiometry."""
    from app.physics.species import GAS, LIQ, SOLID
    for name, cr in COMPILED.items():
        tot = {}
        for keys, idx, nu in ((GAS, cr.g_idx, cr.g_nu), (SOLID, cr.s_idx, cr.s_nu), (LIQ, cr.l_idx, cr.l_nu)):
            for i, v in zip(idx, nu):
                for el, n in SPECIES[keys[int(i)]].formula.items():
                    tot[el] = tot.get(el, 0.0) + v * n
        assert all(abs(v) < 1e-9 for v in tot.values()), (name, tot)


@pytest.mark.parametrize("key,M", [("Fe2O3", 159.69), ("Fe3O4", 231.53), ("FeO", 71.84), ("CO", 28.010), ("CO2", 44.009),
                                   ("H2O", 18.015), ("CaCO3", 100.09), ("SiO2", 60.08), ("Al2O3", 101.96)])
def test_molar_masses(key, M):
    assert abs(SPECIES[key].molar_mass * 1000.0 - M) / M < 2e-3  # stored in kg/mol


@pytest.mark.parametrize("name,sign", [("R_boud", +1), ("R_wg", +1), ("R_wus_CO", -1), ("R_hem_CO", -1)])
def test_reaction_enthalpy_sign(name, sign):
    dH = float(np.atleast_1d(reaction_props(REACTIONS[name].stoich, 1200.0)["dH"])[0])
    assert np.sign(dH) == sign, (name, dH)


def test_element_list_complete():
    for e in ("Fe", "C", "O", "H", "N", "Si", "Ca", "Mg", "Al", "Mn", "P", "S"):
        assert e in ELEMENTS
