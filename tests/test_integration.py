from __future__ import annotations

from types import MappingProxyType

import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODES,
    ATTR_PRESET_MODE,
    ATTR_PRESET_MODES,
    ATTR_TEMPERATURE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import ATTR_ENTITY_ID, EVENT_CALL_SERVICE, STATE_OFF, STATE_ON
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM


def make_entry(*, shared: bool = True) -> tuple[MockConfigEntry, ConfigSubentry]:
    room_data = {
        "name": "Test room",
        "temperature_sensor_entity_ids": [
            "sensor.test_temperature_one",
            "sensor.test_temperature_two",
        ],
        "ac_entity_id": "climate.test_ac",
        "heater_entity_id": "climate.test_heater",
        "window_entity_id": "binary_sensor.test_window",
        "rapid_entity_id": "switch.test_rapid",
        "silent_entity_id": "switch.test_silent",
        "heating_hysteresis_on": 0.5,
        "heating_hysteresis_off": 0.3,
        "cooling_hysteresis_on": 0.5,
        "cooling_hysteresis_off": 0.3,
        "enable_safe_cooling_delay": True,
        "minimum_seconds_cooling_on": 0,
        "minimum_seconds_cooling_off": 0,
        "mode_reversal_guard_seconds": 0,
        "trv_target_offset": 1.0,
        "boost_ac_heat_assist": True,
    }
    subentry = ConfigSubentry(
        data=MappingProxyType(room_data),
        subentry_type=SUBENTRY_ROOM,
        title="Test room",
        unique_id="test-room-stable-id",
    )
    controller_data = {
        "name": "Virtual HVAC",
        "enable_safe_heating_delay": True,
        "minimum_seconds_heating_on": 0,
        "minimum_seconds_heating_off": 0,
    }
    if shared:
        controller_data["shared_heat_source_entity_id"] = "switch.test_heat_source"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        data=controller_data,
        subentries_data=[subentry.as_dict()],
    )
    return entry, subentry


def set_source_states(hass) -> None:
    hass.states.async_set(
        "sensor.test_temperature_one",
        "68.0",
        {"device_class": "temperature", "unit_of_measurement": "°F"},
    )
    hass.states.async_set(
        "sensor.test_temperature_two",
        "22.0",
        {"device_class": "temperature", "unit_of_measurement": "°C"},
    )
    hass.states.async_set(
        "climate.test_ac",
        HVACMode.OFF,
        {
            ATTR_HVAC_MODES: [
                HVACMode.OFF,
                HVACMode.HEAT,
                HVACMode.COOL,
                HVACMode.DRY,
                HVACMode.FAN_ONLY,
            ],
            "fan_modes": ["low", "auto"],
            "swing_modes": ["off", "vertical"],
            ATTR_TEMPERATURE: 21.0,
        },
    )
    hass.states.async_set(
        "climate.test_heater",
        HVACMode.OFF,
        {ATTR_HVAC_MODES: [HVACMode.OFF, HVACMode.HEAT], ATTR_TEMPERATURE: 21.0},
    )
    hass.states.async_set("binary_sensor.test_window", STATE_OFF)
    hass.states.async_set("switch.test_rapid", STATE_OFF)
    hass.states.async_set("switch.test_silent", STATE_OFF)
    hass.states.async_set("switch.test_heat_source", STATE_OFF)

    @callback
    def emulate_physical_acknowledgement(event) -> None:
        """Make state-only fixtures acknowledge commands like real integrations."""
        data = event.data
        service_data = data["service_data"]
        physical_entity = service_data.get(ATTR_ENTITY_ID)
        if physical_entity not in {
            "climate.test_ac",
            "climate.test_heater",
            "switch.test_rapid",
            "switch.test_silent",
            "switch.test_heat_source",
        }:
            return
        old = hass.states.get(physical_entity)
        assert old is not None
        if data["domain"] == CLIMATE_DOMAIN:
            if data["service"] == SERVICE_SET_HVAC_MODE:
                hass.states.async_set(physical_entity, service_data["hvac_mode"], old.attributes)
            elif data["service"] == SERVICE_SET_TEMPERATURE:
                hass.states.async_set(
                    physical_entity,
                    old.state,
                    old.attributes | {ATTR_TEMPERATURE: service_data[ATTR_TEMPERATURE]},
                )
        elif data["domain"] == "switch":
            hass.states.async_set(
                physical_entity,
                STATE_ON if data["service"] == "turn_on" else STATE_OFF,
                old.attributes,
            )

    hass.bus.async_listen(EVENT_CALL_SERVICE, emulate_physical_acknowledgement)


def entity_id(
    hass,
    platform: str,
    entry: MockConfigEntry,
    subentry: ConfigSubentry,
    suffix: str,
) -> str:
    registry = er.async_get(hass)
    found = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{subentry.subentry_id}_{suffix}"
    )
    assert found is not None
    return found


@pytest.mark.asyncio
async def test_setup_creates_room_and_controller_entities(hass) -> None:
    set_source_states(hass)
    entry, subentry = make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    climate_id = entity_id(hass, "climate", entry, subentry, "climate")
    climate_state = hass.states.get(climate_id)
    assert climate_state is not None
    assert climate_state.attributes["current_temperature"] == 21.0
    assert climate_state.attributes[ATTR_HVAC_MODES] == [
        HVACMode.OFF,
        HVACMode.HEAT,
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.HEAT_COOL,
    ]
    assert climate_state.attributes[ATTR_PRESET_MODES] == ["comfort", "boost", "sleep"]

    demand_id = entity_id(hass, "binary_sensor", entry, subentry, "heat_demand")
    assert hass.states.get(demand_id).state == STATE_OFF
    status_id = entity_id(hass, "sensor", entry, subentry, "status")
    assert hass.states.get(status_id).state == "mode_off"

    registry = er.async_get(hass)
    aggregate_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_aggregate_heat_demand"
    )
    assert aggregate_id is not None
    assert hass.states.get(aggregate_id).state == STATE_OFF


@pytest.mark.asyncio
async def test_fahrenheit_system_converts_service_target_to_internal_celsius(hass) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    set_source_states(hass)
    entry, subentry = make_entry(shared=False)
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate_id = entity_id(hass, "climate", entry, subentry, "climate")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: climate_id, ATTR_TEMPERATURE: 71.6},
        blocking=True,
    )
    await hass.async_block_till_done()

    room = entry.runtime_data.rooms[subentry.subentry_id]
    assert room.target_temperature == pytest.approx(22.0)
    assert hass.states.get(climate_id).attributes[ATTR_TEMPERATURE] == 72


@pytest.mark.asyncio
async def test_heat_mode_controls_room_and_shared_heat_source(hass) -> None:
    set_source_states(hass)
    entry, subentry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate_id = entity_id(hass, "climate", entry, subentry, "climate")
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, lambda event: calls.append(event.data))
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: climate_id, ATTR_TEMPERATURE: 22.0},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: climate_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(climate_id)
    assert state.state == HVACMode.HEAT
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    demand_id = entity_id(hass, "binary_sensor", entry, subentry, "heat_demand")
    assert hass.states.get(demand_id).state == STATE_ON

    assert any(
        call["service_data"][ATTR_ENTITY_ID] == "climate.test_heater"
        and call["service_data"]["hvac_mode"] == HVACMode.HEAT
        for call in calls
        if call["domain"] == CLIMATE_DOMAIN and call["service"] == SERVICE_SET_HVAC_MODE
    )
    assert any(
        call["service_data"][ATTR_ENTITY_ID] == "climate.test_heater"
        and call["service_data"][ATTR_TEMPERATURE] == 23.0
        for call in calls
        if call["domain"] == CLIMATE_DOMAIN and call["service"] == SERVICE_SET_TEMPERATURE
    )
    assert any(
        call["service_data"][ATTR_ENTITY_ID] == "switch.test_heat_source"
        for call in calls
        if call["domain"] == "switch" and call["service"] == "turn_on"
    )


@pytest.mark.asyncio
async def test_boost_and_window_interlock(hass) -> None:
    set_source_states(hass)
    entry, subentry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate_id = entity_id(hass, "climate", entry, subentry, "climate")
    calls = []
    hass.bus.async_listen(EVENT_CALL_SERVICE, lambda event: calls.append(event.data))
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")

    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: climate_id, ATTR_TEMPERATURE: 22.0},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_PRESET_MODE,
        {ATTR_ENTITY_ID: climate_id, ATTR_PRESET_MODE: "boost"},
        blocking=True,
    )
    await hass.services.async_call(
        CLIMATE_DOMAIN,
        SERVICE_SET_HVAC_MODE,
        {ATTR_ENTITY_ID: climate_id, "hvac_mode": HVACMode.HEAT},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert any(
        call["service_data"][ATTR_ENTITY_ID] == "switch.test_rapid"
        for call in calls
        if call["domain"] == "switch" and call["service"] == "turn_on"
    )
    assert hass.states.get(climate_id).attributes[ATTR_PRESET_MODE] == "boost"

    hass.states.async_set("switch.test_heat_source", STATE_ON)
    hass.states.async_set("binary_sensor.test_window", STATE_ON)
    await hass.async_block_till_done()

    demand_id = entity_id(hass, "binary_sensor", entry, subentry, "heat_demand")
    assert hass.states.get(demand_id).state == STATE_OFF
    assert hass.states.get(climate_id).attributes[ATTR_HVAC_ACTION] == HVACAction.OFF
    assert any(
        call["service_data"][ATTR_ENTITY_ID] == "switch.test_heat_source"
        for call in calls
        if call["domain"] == "switch" and call["service"] == "turn_off"
    )


@pytest.mark.asyncio
async def test_unload_removes_entities_and_runtime(hass) -> None:
    set_source_states(hass)
    entry, subentry = make_entry(shared=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    climate_id = entity_id(hass, "climate", entry, subentry, "climate")
    assert hass.states.get(climate_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(climate_id) is None or hass.states.get(climate_id).state == "unavailable"
    assert getattr(entry, "runtime_data", None) is None
