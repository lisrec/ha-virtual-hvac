"""Focused actuator acknowledgement and failure-path tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest
from homeassistant.components.climate import ATTR_TEMPERATURE, HVACMode
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError

from custom_components.virtual_hvac.actuators import (
    ActuationResult,
    ActuatorAdapter,
    async_set_switch_confirmed,
)
from custom_components.virtual_hvac.control import ControlDecision, OutputMode
from custom_components.virtual_hvac.models import RoomConfig


def room_config(**overrides: object) -> RoomConfig:
    values: dict[str, object] = {
        "name": "Room",
        "temperature_sensor_entity_ids": ("sensor.temperature",),
        "ac_entity_id": "climate.ac",
        "heater_entity_id": "switch.heater",
        "rapid_entity_id": "switch.rapid",
        "silent_entity_id": "switch.silent",
    }
    values.update(overrides)
    return RoomConfig(**values)  # type: ignore[arg-type]


def decision(output: OutputMode, *, rapid: bool = False, silent: bool = False) -> ControlDecision:
    return ControlDecision(
        output_mode=output,
        heat_demand=output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST),
        heater_active=output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST),
        ac_target_temperature=22.0,
        rapid=rapid,
        silent=silent,
        reason="test",
    )


@pytest.mark.asyncio
async def test_switch_command_requires_an_authoritative_starting_state(hass) -> None:
    assert not await async_set_switch_confirmed(hass, "switch.output", True)
    hass.states.async_set("switch.output", STATE_UNAVAILABLE)
    assert not await async_set_switch_confirmed(hass, "switch.output", True)


@pytest.mark.asyncio
async def test_switch_command_skips_service_when_already_converged(hass) -> None:
    hass.states.async_set("switch.output", STATE_ON)
    assert await async_set_switch_confirmed(hass, "switch.output", True)


@pytest.mark.asyncio
async def test_switch_command_confirms_state_change_from_service(hass) -> None:
    hass.states.async_set("switch.output", STATE_OFF)

    async def turn_on(_call) -> None:
        hass.states.async_set("switch.output", STATE_ON)

    hass.services.async_register("switch", "turn_on", turn_on)

    assert await async_set_switch_confirmed(hass, "switch.output", True)


@pytest.mark.asyncio
async def test_switch_service_error_is_reported_as_unconfirmed(hass) -> None:
    hass.states.async_set("switch.output", STATE_OFF)

    async def fail(_call) -> None:
        raise HomeAssistantError("device rejected command")

    hass.services.async_register("switch", "turn_on", fail)

    assert not await async_set_switch_confirmed(hass, "switch.output", True)


@pytest.mark.asyncio
async def test_fan_mode_rejects_missing_or_unsupported_capability(hass) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    assert not await adapter.async_set_fan_mode("quiet")
    hass.states.async_set("climate.ac", HVACMode.OFF, {"fan_modes": ["auto"]})
    assert not await adapter.async_set_fan_mode("quiet")


@pytest.mark.asyncio
async def test_fan_mode_skips_service_when_already_selected(hass) -> None:
    hass.states.async_set("climate.ac", HVACMode.OFF, {"fan_modes": ["auto"], "fan_mode": "auto"})
    assert await ActuatorAdapter(hass, room_config()).async_set_fan_mode("auto")


@pytest.mark.asyncio
async def test_fan_mode_requires_service_acknowledgement(hass) -> None:
    hass.states.async_set(
        "climate.ac", HVACMode.OFF, {"fan_modes": ["auto", "quiet"], "fan_mode": "auto"}
    )

    async def set_fan_mode(call) -> None:
        old = hass.states.get("climate.ac")
        assert old is not None
        hass.states.async_set(
            "climate.ac", old.state, old.attributes | {"fan_mode": call.data["fan_mode"]}
        )

    hass.services.async_register("climate", "set_fan_mode", set_fan_mode)
    assert await ActuatorAdapter(hass, room_config()).async_set_fan_mode("quiet")


@pytest.mark.asyncio
async def test_swing_mode_handles_unsupported_current_and_confirmed_values(hass) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    assert not await adapter.async_set_swing_mode("vertical")
    hass.states.async_set(
        "climate.ac",
        HVACMode.OFF,
        {"swing_modes": ["off", "vertical"], "swing_mode": "off"},
    )
    assert await adapter.async_set_swing_mode("off")

    async def set_swing_mode(call) -> None:
        old = hass.states.get("climate.ac")
        assert old is not None
        hass.states.async_set(
            "climate.ac", old.state, old.attributes | {"swing_mode": call.data["swing_mode"]}
        )

    hass.services.async_register("climate", "set_swing_mode", set_swing_mode)
    assert await adapter.async_set_swing_mode("vertical")


@pytest.mark.asyncio
async def test_cooling_refuses_to_start_until_heater_stop_is_confirmed(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=False))
    monkeypatch.setattr(adapter, "_async_set_ac", AsyncMock(return_value=True))

    result = await adapter.async_apply(decision(OutputMode.COOL), 21.0)

    assert result == ActuationResult(False, "heater_stop_not_confirmed")
    adapter._async_set_ac.assert_not_awaited()


@pytest.mark.asyncio
async def test_heating_refuses_to_start_until_ac_stop_is_confirmed(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(adapter, "_async_set_ac", AsyncMock(return_value=False))
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=True))

    result = await adapter.async_apply(decision(OutputMode.HEAT), 21.0)

    assert result == ActuationResult(False, "ac_stop_not_confirmed")
    adapter._async_set_heater.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_heater_start_triggers_best_effort_neutralization(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(adapter, "_async_set_ac", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=False))
    neutralize = AsyncMock(return_value=ActuationResult(True))
    monkeypatch.setattr(adapter, "async_neutralize", neutralize)

    result = await adapter.async_apply(decision(OutputMode.HEAT), 21.0)

    assert result == ActuationResult(False, "heater_start_not_confirmed")
    neutralize.assert_awaited_once_with(21.0)


@pytest.mark.asyncio
async def test_heat_assist_failure_neutralizes_instead_of_reporting_success(
    hass, monkeypatch
) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    set_ac = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(adapter, "_async_set_ac", set_ac)
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=True))
    neutralize = AsyncMock(return_value=ActuationResult(True))
    monkeypatch.setattr(adapter, "async_neutralize", neutralize)

    result = await adapter.async_apply(decision(OutputMode.HEAT_ASSIST), 21.0)

    assert result == ActuationResult(False, "ac_heat_assist_not_confirmed")
    assert set_ac.await_args_list == [call(HVACMode.OFF, None), call(HVACMode.HEAT, 22.0)]
    neutralize.assert_awaited_once_with(21.0)


@pytest.mark.asyncio
async def test_off_decision_reports_neutralization_failure(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(
        adapter,
        "async_neutralize",
        AsyncMock(return_value=ActuationResult(False, "neutralization_not_confirmed")),
    )

    result = await adapter.async_apply(decision(OutputMode.OFF), 21.0)

    assert result == ActuationResult(False, "neutralization_not_confirmed")


@pytest.mark.asyncio
async def test_preset_failure_neutralizes_active_outputs(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_async_set_ac", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_async_set_presets", AsyncMock(return_value=False))
    neutralize = AsyncMock(return_value=ActuationResult(True))
    monkeypatch.setattr(adapter, "async_neutralize", neutralize)

    result = await adapter.async_apply(decision(OutputMode.COOL, rapid=True), 21.0)

    assert result == ActuationResult(False, "preset_output_not_confirmed")
    neutralize.assert_awaited_once_with(21.0)


@pytest.mark.asyncio
async def test_home_assistant_error_from_adapter_is_fail_closed(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(
        adapter, "_async_set_heater", AsyncMock(side_effect=HomeAssistantError("failed"))
    )

    result = await adapter.async_apply(decision(OutputMode.COOL), 21.0)

    assert result == ActuationResult(False, "service_call_failed")


@pytest.mark.asyncio
async def test_neutralize_attempts_every_configured_output(hass, monkeypatch) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(adapter, "_async_set_ac", AsyncMock(return_value=True))
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=False))
    confirmed = AsyncMock(side_effect=[True, True])
    monkeypatch.setattr(
        "custom_components.virtual_hvac.actuators.async_set_switch_confirmed", confirmed
    )

    result = await adapter.async_neutralize(21.0)

    assert result == ActuationResult(False, "neutralization_not_confirmed")
    assert confirmed.await_count == 2


@pytest.mark.asyncio
async def test_authoritative_check_rejects_unknown_output(hass) -> None:
    config = room_config(rapid_entity_id=None, silent_entity_id=None)
    hass.states.async_set("climate.ac", HVACMode.OFF)
    hass.states.async_set("switch.heater", "unknown")
    assert not await ActuatorAdapter(hass, config).async_inputs_authoritative()

    hass.states.async_set("switch.heater", STATE_OFF)
    assert await ActuatorAdapter(hass, config).async_inputs_authoritative()


@pytest.mark.asyncio
async def test_absent_actuators_are_only_safe_in_the_off_direction(hass) -> None:
    ac_only = ActuatorAdapter(
        hass,
        room_config(heater_entity_id=None, rapid_entity_id=None, silent_entity_id=None),
    )
    heater_only = ActuatorAdapter(
        hass,
        room_config(ac_entity_id=None, rapid_entity_id=None, silent_entity_id=None),
    )

    assert await ac_only._async_set_heater(False, 21.0)
    assert not await ac_only._async_set_heater(True, 21.0)
    assert await heater_only._async_set_ac(HVACMode.OFF, None)
    assert not await heater_only._async_set_ac(HVACMode.COOL, 21.0)


@pytest.mark.asyncio
async def test_heater_rejects_unsupported_entity_domain(hass) -> None:
    adapter = ActuatorAdapter(
        hass,
        room_config(heater_entity_id="light.heater", rapid_entity_id=None, silent_entity_id=None),
    )
    assert not await adapter._async_set_heater(True, 21.0)


@pytest.mark.asyncio
async def test_climate_helpers_reject_unknown_modes_and_skip_equal_temperature(hass) -> None:
    adapter = ActuatorAdapter(hass, room_config())
    assert not await adapter._async_set_climate_mode("climate.ac", HVACMode.COOL)
    hass.states.async_set(
        "climate.ac",
        HVACMode.OFF,
        {"hvac_modes": [HVACMode.OFF], ATTR_TEMPERATURE: 21.0},
    )
    assert not await adapter._async_set_climate_mode("climate.ac", HVACMode.COOL)
    assert await adapter._async_set_climate_temperature("climate.ac", 21.001)
