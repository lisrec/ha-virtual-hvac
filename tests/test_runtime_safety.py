"""Safety regression tests for runtime actuation and protection state."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import ATTR_HVAC_MODE, ATTR_TEMPERATURE, HVACMode
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.util.dt import utcnow

from custom_components.virtual_hvac.actuators import ActuatorAdapter
from custom_components.virtual_hvac.control import ControlDecision, OutputMode
from custom_components.virtual_hvac.models import RoomConfig
from custom_components.virtual_hvac.protection import ProtectionTimestamps


def room_config(**overrides: object) -> RoomConfig:
    values: dict[str, object] = {
        "name": "Room",
        "temperature_sensor_entity_ids": ("sensor.temperature",),
        "ac_entity_ids": ("climate.ac",),
        "heater_entity_ids": ("climate.heater",),
        "minimum_seconds_cooling_on": 0,
        "minimum_seconds_cooling_off": 0,
        "mode_reversal_guard_seconds": 0,
    }
    values.update(overrides)
    return RoomConfig(**values)  # type: ignore[arg-type]


def decision(output: OutputMode) -> ControlDecision:
    return ControlDecision(
        output_mode=output,
        heat_demand=output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST),
        heater_active=output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST),
        ac_target_temperature=22.0 if output is OutputMode.COOL else None,
        rapid=False,
        silent=False,
        reason="test",
    )


def set_climates(hass, *, heater_mode: HVACMode = HVACMode.OFF) -> None:
    hass.states.async_set(
        "climate.ac",
        HVACMode.OFF,
        {
            "hvac_modes": [
                HVACMode.OFF,
                HVACMode.COOL,
                HVACMode.DRY,
                HVACMode.FAN_ONLY,
                HVACMode.HEAT,
            ],
            ATTR_TEMPERATURE: 21.0,
        },
    )
    hass.states.async_set(
        "climate.heater",
        heater_mode,
        {"hvac_modes": [HVACMode.OFF, HVACMode.HEAT], ATTR_TEMPERATURE: 21.0},
    )


@pytest.mark.asyncio
async def test_reversal_stops_and_confirms_heater_before_enabling_cooling(hass) -> None:
    set_climates(hass, heater_mode=HVACMode.HEAT)
    order: list[tuple[str, str]] = []

    async def set_mode(call) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        mode = call.data[ATTR_HVAC_MODE]
        if entity_id == "climate.heater" and mode == HVACMode.OFF:
            await asyncio.sleep(0.01)
        order.append((entity_id, mode))
        old = hass.states.get(entity_id)
        assert old is not None
        hass.states.async_set(entity_id, mode, old.attributes)

    async def set_temperature(call) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        old = hass.states.get(entity_id)
        assert old is not None
        hass.states.async_set(
            entity_id,
            old.state,
            old.attributes | {ATTR_TEMPERATURE: call.data[ATTR_TEMPERATURE]},
        )

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    hass.services.async_register("climate", "set_temperature", set_temperature)

    result = await ActuatorAdapter(hass, room_config()).async_apply(decision(OutputMode.COOL), 22.0)

    assert result.success
    assert order.index(("climate.heater", HVACMode.OFF)) < order.index(
        ("climate.ac", HVACMode.COOL)
    )


@pytest.mark.asyncio
async def test_command_without_physical_acknowledgement_fails(hass, monkeypatch) -> None:
    set_climates(hass)

    async def ignore_mode(call) -> None:
        return None

    hass.services.async_register("climate", "set_hvac_mode", ignore_mode)
    monkeypatch.setattr("custom_components.virtual_hvac.actuators.COMMAND_ACK_TIMEOUT", 0.01)

    result = await ActuatorAdapter(hass, room_config()).async_apply(decision(OutputMode.COOL), 22.0)

    assert not result.success
    assert result.reason == "ac_stop_or_start_not_confirmed"


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_ac_mode", [HVACMode.COOL, HVACMode.DRY])
async def test_heat_assist_stops_ac_before_starting_any_heating(hass, initial_ac_mode) -> None:
    set_climates(hass)
    ac = hass.states.get("climate.ac")
    assert ac is not None
    hass.states.async_set("climate.ac", initial_ac_mode, ac.attributes)
    order: list[tuple[str, str]] = []

    async def set_mode(call) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        mode = call.data[ATTR_HVAC_MODE]
        order.append((entity_id, mode))
        old = hass.states.get(entity_id)
        assert old is not None
        hass.states.async_set(entity_id, mode, old.attributes)

    async def set_temperature(call) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        old = hass.states.get(entity_id)
        assert old is not None
        hass.states.async_set(
            entity_id,
            old.state,
            old.attributes | {ATTR_TEMPERATURE: call.data[ATTR_TEMPERATURE]},
        )

    hass.services.async_register("climate", "set_hvac_mode", set_mode)
    hass.services.async_register("climate", "set_temperature", set_temperature)

    result = await ActuatorAdapter(hass, room_config()).async_apply(
        decision(OutputMode.HEAT_ASSIST), 22.0
    )

    assert result.success
    assert (
        order.index(("climate.ac", HVACMode.OFF))
        < order.index(("climate.heater", HVACMode.HEAT))
        < order.index(("climate.ac", HVACMode.HEAT))
    )


@pytest.mark.asyncio
async def test_heat_assist_failure_is_not_success_when_ac_off_fallback_is_unconfirmed(
    hass, monkeypatch
) -> None:
    set_climates(hass)
    adapter = ActuatorAdapter(hass, room_config())
    monkeypatch.setattr(
        adapter,
        "_async_set_ac",
        AsyncMock(side_effect=[True, False, False, False]),
    )
    monkeypatch.setattr(adapter, "_async_set_heater", AsyncMock(return_value=True))

    result = await adapter.async_apply(decision(OutputMode.HEAT_ASSIST), 22.0)

    assert not result.success
    assert result.reason == "ac_heat_assist_not_confirmed"


def test_protection_timestamps_treat_missing_corrupt_and_future_as_just_changed() -> None:
    now = utcnow()
    timestamps = ProtectionTimestamps(None)
    assert timestamps.elapsed("missing", now) == 0.0

    timestamps.replace_raw(
        {"corrupt": "not-a-time", "future": (now + timedelta(hours=1)).isoformat()}
    )
    assert timestamps.elapsed("corrupt", now) == 0.0
    assert timestamps.elapsed("future", now) == 0.0


def test_protection_timestamps_preserve_valid_wall_clock_elapsed() -> None:
    now = utcnow()
    timestamps = ProtectionTimestamps(None)
    timestamps.replace_raw({"relay": (now - timedelta(seconds=42)).isoformat()})
    assert timestamps.elapsed("relay", now) == pytest.approx(42.0)
