"""Unit conversions (kcal/J/GJ, degC/K, Nm3, gauge pressure) and finite-value guards."""
import math

import pytest

from app.core.units import UnitError, convert, dimension
from app.physics.species import MOL_PER_NM3


def test_temperature():
    assert convert(25.0, "degC", "K") == pytest.approx(298.15)
    assert convert(1773.15, "K", "degC") == pytest.approx(1500.0)


def test_energy():
    assert convert(1.0, "kcal", "J") == pytest.approx(4184.0)
    assert convert(1.0, "GJ", "MJ") == pytest.approx(1000.0)
    assert convert(1.0, "kWh", "MJ") == pytest.approx(3.6)


def test_pressure_gauge():
    assert convert(0.0, "kPa_g", "Pa") == pytest.approx(101325.0)
    assert convert(2.0, "bar", "kPa") == pytest.approx(200.0)


def test_normal_cubic_metre():
    assert MOL_PER_NM3 == pytest.approx(101325.0 / (8.314462618 * 273.15), rel=1e-6)


def test_incompatible_units_rejected():
    with pytest.raises(UnitError):
        convert(1.0, "degC", "kPa")
    with pytest.raises(UnitError):
        convert(1.0, "furlong", "m")


def test_dimension_lookup():
    assert dimension("t/h") == dimension("kg/s")
