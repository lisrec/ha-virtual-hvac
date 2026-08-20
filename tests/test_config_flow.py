from __future__ import annotations

from types import MappingProxyType

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM

CONTROLLER_DATA = {
    "name": "Virtual HVAC",
    "shared_minimum_on_seconds": 300,
    "shared_minimum_off_seconds": 180,
}
ROOM_DATA = {
    "name": "Test room",
    "temperature_sensor_entity_ids": ["sensor.test_temperature"],
    "ac_entity_id": "climate.test_ac",
    "heater_entity_id": "climate.test_heater",
    "heating_hysteresis_on": 0.5,
    "heating_hysteresis_off": 0.3,
    "cooling_hysteresis_on": 0.5,
    "cooling_hysteresis_off": 0.3,
    "ac_minimum_off_seconds": 300,
    "mode_reversal_guard_seconds": 300,
    "trv_target_offset": 1.0,
    "boost_ac_heat_assist": False,
}


def set_source_entities(hass) -> None:
    hass.states.async_set(
        "sensor.test_temperature",
        "21.0",
        {ATTR_DEVICE_CLASS: "temperature", ATTR_UNIT_OF_MEASUREMENT: "°C"},
    )
    hass.states.async_set("climate.test_ac", "off", {"hvac_modes": ["off", "cool"]})
    hass.states.async_set("climate.test_heater", "off", {"hvac_modes": ["off", "heat"]})


@pytest.mark.asyncio
async def test_user_flow_creates_single_controller(hass) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], CONTROLLER_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Virtual HVAC"
    assert result["data"]["shared_minimum_on_seconds"] == 300

    entry = MockConfigEntry(domain=DOMAIN, title="Virtual HVAC", data=CONTROLLER_DATA)
    entry.add_to_hass(hass)
    duplicate = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert duplicate["type"] is FlowResultType.ABORT
    assert duplicate["reason"] == "single_instance_allowed"


@pytest.mark.asyncio
async def test_room_subentry_flow_creates_room(hass) -> None:
    set_source_entities(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Virtual HVAC", data=CONTROLLER_DATA)
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], ROOM_DATA)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test room"
    assert len(entry.subentries) == 1
    subentry = next(iter(entry.subentries.values()))
    assert subentry.subentry_type == SUBENTRY_ROOM
    assert subentry.data["temperature_sensor_entity_ids"] == ["sensor.test_temperature"]
    assert subentry.unique_id is not None


@pytest.mark.asyncio
async def test_room_flow_rejects_actuator_used_by_another_room(hass) -> None:
    set_source_entities(hass)
    existing = ConfigSubentry(
        data=MappingProxyType(ROOM_DATA),
        subentry_type=SUBENTRY_ROOM,
        title="Existing room",
        unique_id="existing-room",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        data=CONTROLLER_DATA,
        subentries_data=[existing.as_dict()],
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], ROOM_DATA | {"name": "Second room"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "actuator_already_assigned"}


@pytest.mark.asyncio
async def test_room_flow_rejects_missing_temperature_sensor(hass) -> None:
    set_source_entities(hass)
    entry = MockConfigEntry(domain=DOMAIN, title="Virtual HVAC", data=CONTROLLER_DATA)
    entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={"source": config_entries.SOURCE_USER},
    )
    invalid = ROOM_DATA | {"temperature_sensor_entity_ids": ["sensor.does_not_exist"]}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], invalid)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_temperature_sensor"}


@pytest.mark.asyncio
async def test_room_subentry_can_be_reconfigured(hass) -> None:
    set_source_entities(hass)
    existing = ConfigSubentry(
        data=MappingProxyType(ROOM_DATA),
        subentry_type=SUBENTRY_ROOM,
        title="Test room",
        unique_id="stable-room-id",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        data=CONTROLLER_DATA,
        subentries_data=[existing.as_dict()],
    )
    entry.add_to_hass(hass)
    subentry = next(iter(entry.subentries.values()))

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_ROOM),
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "subentry_id": subentry.subentry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    updated = ROOM_DATA | {"name": "Updated room", "trv_target_offset": 1.5}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], updated)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert subentry.title == "Updated room"
    assert subentry.unique_id == "stable-room-id"
    assert subentry.data["trv_target_offset"] == 1.5
