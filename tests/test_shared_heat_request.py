"""Shared CO demand from an always-open floor, without a local heater ACK."""

import json
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from test_integration import set_source_states

from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM
from custom_components.virtual_hvac.control import OutputMode, Preset, VirtualMode
from custom_components.virtual_hvac.models import RoomConfig


@pytest.mark.asyncio
async def test_shared_heat_safety_cooling_recovery_and_unload(hass, freezer):
    set_source_states(hass)
    entry, subentry = shared_entry()
    data = dict(subentry.data) | {
        "window_entity_ids": ["binary_sensor.shared_window"],
        "window_open_delay_minutes": 0,
    }
    subentry = ConfigSubentry(
        data=MappingProxyType(data),
        subentry_type=SUBENTRY_ROOM,
        title=subentry.title,
        unique_id=subentry.unique_id,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        data=dict(entry.data),
        subentries_data=[subentry.as_dict()],
    )
    hass.states.async_set("binary_sensor.shared_window", "off")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    try:
        await room.async_set_target_temperature(24)
        await room.async_set_mode(VirtualMode.HEAT)
        await room.async_set_preset(Preset.BOOST)
        await hass.async_block_till_done()
        assert room.heat_demand
        assert not room.decision.heater_active
        assert room.decision.output_mode is OutputMode.HEAT
        assert hass.states.get("climate.test_ac").state == "off"
        for state in ("on", "unknown", "unavailable"):
            hass.states.async_set("binary_sensor.shared_window", state)
            await hass.async_block_till_done()
            assert not room.heat_demand
            assert hass.states.get(aggregate_id(hass, entry)).state == "off"
            hass.states.async_set("binary_sensor.shared_window", "off")
            await hass.async_block_till_done()
            assert room.heat_demand
        freezer.tick(timedelta(seconds=301))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert hass.states.get(aggregate_id(hass, entry)).state == "off"
        assert hass.states.get("climate.test_ac").state == "off"
        hass.states.async_set("sensor.test_temperature_two", "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert room.heat_demand
        await room.async_set_target_temperature(18)
        await room.async_set_mode(VirtualMode.COOL)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert hass.states.get("climate.test_ac").state == "cool"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert not room.heat_demand
    assert hass.states.get("climate.test_ac").state == "off"


@pytest.mark.asyncio
async def test_shared_heat_ac_unavailable_withdraws_demand_and_recovers(hass):
    set_source_states(hass)
    entry, subentry = shared_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    try:
        await room.async_set_target_temperature(24)
        await room.async_set_mode(VirtualMode.HEAT)
        assert room.heat_demand
        ac = hass.states.get("climate.test_ac")
        hass.states.async_set("climate.test_ac", "unavailable", ac.attributes)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert hass.states.get(aggregate_id(hass, entry)).state == "off"
        hass.states.async_set("climate.test_ac", "off", ac.attributes)
        await hass.async_block_till_done()
        assert room.heat_demand
        assert not room.decision.heater_active
        assert hass.states.get("climate.test_ac").state == "off"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_shared_heat_multiple_rooms_or_does_not_turn_off_other_room(hass):
    set_source_states(hass)
    _, first = shared_entry()
    second = ConfigSubentry(
        data=MappingProxyType(dict(first.data) | {"name": "Second floor", "ac_entity_ids": []}),
        subentry_type=SUBENTRY_ROOM,
        title="Second floor",
        unique_id="second-floor",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        data={"name": "Virtual HVAC"},
        subentries_data=[first.as_dict(), second.as_dict()],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        for room in entry.runtime_data.rooms.values():
            await room.async_set_target_temperature(24)
            await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        await entry.runtime_data.rooms[first.subentry_id].async_set_mode(VirtualMode.OFF)
        await hass.async_block_till_done()
        assert not entry.runtime_data.rooms[first.subentry_id].heat_demand
        assert entry.runtime_data.rooms[second.subentry_id].heat_demand
        assert hass.states.get(aggregate_id(hass, entry)).state == "on"
        await entry.runtime_data.rooms[second.subentry_id].async_set_mode(VirtualMode.OFF)
        await hass.async_block_till_done()
        assert hass.states.get(aggregate_id(hass, entry)).state == "off"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


def test_shared_heat_ui_strings_are_synchronized():
    root = Path(__file__).resolve().parents[1] / "custom_components/virtual_hvac"
    strings = json.loads((root / "strings.json").read_text())
    assert strings == json.loads((root / "translations/en.json").read_text())
    for step in ("user", "reconfigure"):
        form = strings["config_subentries"]["room"]["step"][step]
        assert form["data"]["shared_heat_request"]
        assert "cannot" in form["data_description"]["shared_heat_request"]


def shared_entry():
    subentry = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Always-open floor",
                "temperature_sensor_entity_ids": ["sensor.test_temperature_two"],
                "ac_entity_ids": ["climate.test_ac"],
                "shared_heat_request": True,
                "mode_reversal_guard_seconds": 0,
            }
        ),
        subentry_type=SUBENTRY_ROOM,
        title="Always-open floor",
        unique_id="always-open-floor",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        data={"name": "Virtual HVAC", "shared_heat_source_entity_id": None},
        subentries_data=[subentry.as_dict()],
    )
    return entry, subentry


def aggregate_id(hass, entry):
    result = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_aggregate_heat_demand"
    )
    assert result is not None
    return result


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_shared_heat_request_must_be_boolean(value):
    data = dict(shared_entry()[1].data) | {"shared_heat_request": value}
    with pytest.raises(ValueError, match="shared heat request must be boolean"):
        RoomConfig.from_mapping(data)


@pytest.mark.parametrize(
    "heater_key, heater_value",
    [
        ("heater_entity_ids", ["switch.test_heater"]),
        ("heater_entity_id", "switch.test_heater"),
    ],
)
def test_shared_heat_rejects_ambiguous_local_heater(heater_key, heater_value):
    data = dict(shared_entry()[1].data) | {heater_key: heater_value}
    with pytest.raises(ValueError, match="shared heat request cannot configure local heaters"):
        RoomConfig.from_mapping(data)


def test_shared_heat_can_be_configured_without_local_outputs():
    data = dict(shared_entry()[1].data) | {"ac_entity_ids": []}
    config = RoomConfig.from_mapping(data)
    assert config.shared_heat_request
    assert config.output_entity_ids() == ()
    assert RoomConfig.from_mapping(config.to_mapping()) == config


@pytest.mark.asyncio
async def test_shared_heat_requests_aggregate_with_ac_off_and_stops_at_target(hass):
    set_source_states(hass)
    entry, subentry = shared_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        room = entry.runtime_data.rooms[subentry.subentry_id]
        await room.async_set_target_temperature(24)
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert room.decision.output_mode is OutputMode.HEAT
        assert room.decision.heat_demand
        assert not room.decision.heater_active
        assert room.decision.ac_target_temperature is None
        assert hass.states.get("climate.test_ac").state == "off"
        assert hass.states.get(aggregate_id(hass, entry)).state == "on"
        hass.states.async_set("sensor.test_temperature_two", "24", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert hass.states.get(aggregate_id(hass, entry)).state == "off"
        assert hass.states.get("climate.test_ac").state == "off"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_shared_heat_capability_and_clocks_do_not_depend_on_ac_heat_support(hass):
    set_source_states(hass)
    ac = hass.states.get("climate.test_ac")
    hass.states.async_set(
        "climate.test_ac", "off", ac.attributes | {"hvac_modes": ["off", "cool", "fan_only"]}
    )
    entry, subentry = shared_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        room = entry.runtime_data.rooms[subentry.subentry_id]
        assert VirtualMode.HEAT in room.supported_virtual_modes()
        assert VirtualMode.AUTO in room.supported_virtual_modes()
        ac_clock = room._timestamps.elapsed(room._ac_timestamp_key)
        await room.async_set_target_temperature(24)
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert room.heat_demand
        assert room._memory.heating_active
        assert not room._decision_uses_ac(room.decision)
        assert room._physical_outputs_match_decision()
        assert room._timestamps.elapsed(room._ac_timestamp_key) >= ac_clock
        assert room._actuators.outputs_match(room.decision, 24)
        assert hass.states.get("climate.test_ac").state == "off"
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
