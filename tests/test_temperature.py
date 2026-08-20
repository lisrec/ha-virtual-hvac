from __future__ import annotations

import math

from custom_components.virtual_hvac.temperature import average_valid_temperatures


def test_returns_single_valid_temperature() -> None:
    assert average_valid_temperatures([21.5]) == 21.5


def test_averages_multiple_valid_temperatures() -> None:
    assert average_valid_temperatures([20.0, 21.0, 22.0]) == 21.0


def test_ignores_invalid_non_finite_and_boolean_values() -> None:
    values = [20.0, None, "unknown", "unavailable", math.nan, math.inf, True, "22.0"]
    assert average_valid_temperatures(values) == 21.0


def test_returns_none_without_valid_temperature() -> None:
    assert average_valid_temperatures([None, "unknown", math.nan]) is None
