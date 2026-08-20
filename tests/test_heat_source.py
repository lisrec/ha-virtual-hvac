from __future__ import annotations

from custom_components.virtual_hvac.heat_source import HeatSourceDecision, decide_heat_source


def test_turns_on_when_demand_exists_after_minimum_off() -> None:
    assert decide_heat_source(True, False, 181, 300, 180) == HeatSourceDecision(
        action=True, reason="turn_on"
    )


def test_waits_for_minimum_off() -> None:
    assert decide_heat_source(True, False, 60, 300, 180) == HeatSourceDecision(
        action=None, reason="minimum_off", retry_after_seconds=120
    )


def test_turns_off_without_demand_after_minimum_on() -> None:
    assert decide_heat_source(False, True, 301, 300, 180) == HeatSourceDecision(
        action=False, reason="turn_off"
    )


def test_waits_for_minimum_on() -> None:
    assert decide_heat_source(False, True, 100, 300, 180) == HeatSourceDecision(
        action=None, reason="minimum_on", retry_after_seconds=200
    )


def test_unavailable_relay_never_produces_action() -> None:
    assert decide_heat_source(True, None, 1000, 300, 180) == HeatSourceDecision(
        action=None, reason="relay_unavailable"
    )


def test_steady_states_are_idempotent() -> None:
    assert decide_heat_source(True, True, 1000, 300, 180).action is None
    assert decide_heat_source(False, False, 1000, 300, 180).action is None


def test_disabled_safe_heating_delay_bypasses_minimum_times() -> None:
    assert decide_heat_source(
        True,
        False,
        0,
        300,
        180,
        safe_delay_enabled=False,
    ) == HeatSourceDecision(action=True, reason="turn_on")
    assert decide_heat_source(
        False,
        True,
        0,
        300,
        180,
        safe_delay_enabled=False,
    ) == HeatSourceDecision(action=False, reason="turn_off")
