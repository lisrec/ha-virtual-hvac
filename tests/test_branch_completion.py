"""Meaningful edge cases that complete remaining safety branch coverage."""

from __future__ import annotations

from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import STATE_UNKNOWN
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_hvac.climate import VirtualRoomClimate
from custom_components.virtual_hvac.config_flow import RoomSubentryFlow
from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM
from custom_components.virtual_hvac.control import ControlDecision, OutputMode, Preset, VirtualMode
from custom_components.virtual_hvac.diagnostics import async_get_config_entry_diagnostics
from custom_components.virtual_hvac.models import ControllerConfig, RoomConfig
from custom_components.virtual_hvac.protection import ProtectionTimestamps
from custom_components.virtual_hvac.runtime import ControllerRuntime


def room_config(**overrides: object) -> RoomConfig:
    values: dict[str, object] = {
        "name": "Room",
        "temperature_sensor_entity_ids": ("sensor.temperature",),
        "heater_entity_ids": ("switch.heater",),
    }
    values.update(overrides)
    return RoomConfig(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "   "}, "controller name"),
        ({"name": "Controller", "minimum_seconds_heating_on": -1}, "protection times"),
    ],
)
def test_controller_rejects_empty_name_and_out_of_range_protection(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        ControllerConfig(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": "  "}, "room name"),
        ({"minimum_seconds_cooling_off": -1}, "protection times"),
        ({"mode_reversal_guard_seconds": 86_401}, "reversal guard"),
        ({"trv_target_offset": 5.1}, "TRV target offset"),
    ],
)
def test_room_rejects_unsafe_boundary_settings(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        room_config(**overrides)


def fake_room(output: OutputMode, *, status: str = "test") -> SimpleNamespace:
    config = room_config(ac_entity_ids=("climate.ac",))
    return SimpleNamespace(
        subentry_id="room-id",
        config=config,
        available=True,
        current_temperature=21.0,
        target_temperature=22.0,
        mode=VirtualMode.OFF,
        preset=Preset.COMFORT,
        status=status,
        physical_status="outputs_confirmed",
        decision=ControlDecision(
            output,
            output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST),
            output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST),
            None,
            False,
            False,
            status,
        ),
        supported_virtual_modes=lambda: [VirtualMode.OFF, VirtualMode.HEAT, VirtualMode.COOL],
        async_set_mode=AsyncMock(),
        async_set_target_temperature=AsyncMock(),
        async_set_preset=AsyncMock(),
        async_set_fan_mode=AsyncMock(),
        async_set_swing_mode=AsyncMock(),
        async_restore=AsyncMock(),
    )


@pytest.mark.parametrize(
    ("output", "status", "expected"),
    [
        (OutputMode.COOL, "explicit_cool", HVACAction.COOLING),
        (OutputMode.DRY, "explicit_dry", HVACAction.DRYING),
        (OutputMode.FAN_ONLY, "explicit_fan_only", HVACAction.FAN),
        (OutputMode.OFF, "auto_dead_band", HVACAction.IDLE),
        (OutputMode.OFF, "mode_off", HVACAction.OFF),
    ],
)
def test_climate_action_exposes_each_physical_operating_state(
    output: OutputMode, status: str, expected: HVACAction
) -> None:
    assert VirtualRoomClimate("entry-id", fake_room(output, status=status)).hvac_action is expected


def test_climate_capabilities_omit_unavailable_fan_and_swing_modes(hass) -> None:
    entity = VirtualRoomClimate("entry-id", fake_room(OutputMode.OFF))
    entity.hass = hass

    assert entity.fan_modes is None
    assert entity.swing_modes is None
    assert not entity.supported_features & ClimateEntityFeature.FAN_MODE
    assert not entity.supported_features & ClimateEntityFeature.SWING_MODE


def test_climate_attributes_count_only_authoritative_temperature_sources(hass) -> None:
    room = fake_room(OutputMode.OFF)
    room.config = room_config(
        ac_entity_ids=("climate.ac",),
        temperature_sensor_entity_ids=("sensor.good", "sensor.unknown", "sensor.missing"),
    )
    entity = VirtualRoomClimate("entry-id", room)
    entity.hass = hass
    hass.states.async_set("sensor.good", "21")
    hass.states.async_set("sensor.unknown", STATE_UNKNOWN)

    assert entity.extra_state_attributes["valid_temperature_sensor_count"] == 1


@pytest.mark.asyncio
async def test_climate_commands_validate_input_and_forward_supported_controls() -> None:
    room = fake_room(OutputMode.OFF)
    entity = VirtualRoomClimate("entry-id", room)

    with pytest.raises(ValueError, match="Unsupported HVAC mode"):
        await entity.async_set_hvac_mode("unsupported")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="numeric target"):
        await entity.async_set_temperature(temperature="warm")

    await entity.async_set_temperature(temperature=23)
    await entity.async_set_fan_mode("quiet")
    await entity.async_set_swing_mode("vertical")
    await entity.async_turn_off()
    await entity.async_turn_on()

    room.async_set_target_temperature.assert_awaited_once_with(23.0)
    room.async_set_fan_mode.assert_awaited_once_with("quiet")
    room.async_set_swing_mode.assert_awaited_once_with("vertical")
    assert room.async_set_mode.await_args_list[-2].args == (VirtualMode.OFF,)
    assert room.async_set_mode.await_args_list[-1].args == (VirtualMode.HEAT,)


@pytest.mark.asyncio
async def test_protection_without_store_loads_and_flushes_as_noops() -> None:
    timestamps = ProtectionTimestamps(None)
    await timestamps.async_load()
    timestamps.record("relay")
    await timestamps.async_flush()
    assert timestamps.elapsed("relay") >= 0


@pytest.mark.asyncio
async def test_protection_load_treats_storage_error_as_empty_state() -> None:
    timestamps = ProtectionTimestamps(None)
    timestamps._store = SimpleNamespace(async_load=AsyncMock(side_effect=OSError("bad store")))
    timestamps.replace_raw({"old": "2020-01-01T00:00:00+00:00"})

    await timestamps.async_load()

    assert timestamps.elapsed("old") == 0.0


@pytest.mark.asyncio
async def test_diagnostics_skip_foreign_subentries_and_flag_invalid_rooms(hass) -> None:
    invalid = ConfigSubentry(
        data=MappingProxyType({"name": "Invalid"}),
        subentry_type=SUBENTRY_ROOM,
        title="Invalid",
        unique_id="invalid",
    )
    foreign = ConfigSubentry(
        data=MappingProxyType({}),
        subentry_type="foreign",
        title="Foreign",
        unique_id="foreign",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Controller",
        data={"name": "Controller"},
        subentries_data=[invalid.as_dict(), foreign.as_dict()],
    )
    entry.runtime_data = ControllerRuntime(
        hass, entry.entry_id, ControllerConfig(name="Controller"), {}
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["controller"]["room_count"] == 1
    assert result["rooms"] == [{"configuration_valid": False}]


def test_temperature_sensor_validation_accepts_units_and_rejects_non_temperature(hass) -> None:
    flow = RoomSubentryFlow()
    flow.hass = hass
    hass.states.async_set("sensor.by_unit", "21", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.humidity", "50", {"unit_of_measurement": "%"})

    assert flow._temperature_sensors_valid(("sensor.by_unit",))
    assert not flow._temperature_sensors_valid(("sensor.humidity",))
