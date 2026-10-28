"""Combined commands must cross Core's real climate dispatcher as one intent."""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.components.climate import HVACMode
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import async_mock_service
from test_integration import entity_id, make_entry, set_source_states

from custom_components.virtual_hvac.control import Preset, VirtualMode


@pytest.fixture
async def combined_room(hass):
    set_source_states(hass)
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    entry, subentry = make_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    climate_id = entity_id(hass, "climate", entry, subentry, "climate")
    await room.async_set_target_temperature(18)
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    calls = []
    unsub = hass.bus.async_listen(EVENT_CALL_SERVICE, lambda event: calls.append(event.data))
    try:
        yield room, climate_id, calls
    finally:
        unsub()
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("mode", "target", "ac_output"),
    [(HVACMode.COOL, 20, "cool"), (HVACMode.COOL, 24, "off"), (HVACMode.OFF, 24, "off")],
)
async def test_combined_command_is_one_intent(hass, combined_room, mode, target, ac_output):
    room, climate_id, calls = combined_room
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_id, "hvac_mode": mode, "temperature": target},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(climate_id).state == mode
    assert room.target_temperature == target
    assert room.preset is Preset.COMFORT
    assert hass.states.get(climate_id).attributes["temperature"] == target
    assert hass.states.get("climate.test_ac").state == ac_output
    assert hass.states.get("climate.test_heater").state == "off"
    assert hass.states.get("switch.test_heat_source").state == "off"
    assert not room.heat_demand
    if ac_output == "off":
        assert not any(
            call["service_data"].get("entity_id") != climate_id
            and call["service_data"].get("hvac_mode") in ("cool", "dry", "fan_only")
            for call in calls
        )
    # A target-first implementation would briefly start heat for the 24 C cases.
    assert not any(
        call["service_data"].get("hvac_mode") == "heat"
        or (call["service"] == "turn_on" and call["domain"] == "switch")
        for call in calls
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"hvac_mode": mode, "temperature": value}
        for mode in ("cool", "off")
        for value in (float("nan"), float("inf"), -float("inf"), 4.5, 35.5, True, False, "bad")
    ]
    + [{"hvac_mode": mode, "temperature": 24} for mode in ("auto", "not_a_mode", None)],
)
async def test_invalid_combined_service_preserves_intent_and_outputs(hass, combined_room, payload):
    room, climate_id, calls = combined_room
    before = (room.mode, room.target_temperature, room.preset, room.decision)
    with pytest.raises((vol.Invalid, HomeAssistantError, ValueError)):
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": climate_id, **payload}, blocking=True
        )
    await hass.async_block_till_done()
    assert (room.mode, room.target_temperature, room.preset, room.decision) == before
    assert hass.states.get(climate_id).state == "heat"
    assert hass.states.get("climate.test_ac").state == "off"
    assert hass.states.get("climate.test_heater").state == "off"
    assert hass.states.get("switch.test_heat_source").state == "off"
    assert not any(call["service_data"].get("entity_id") != climate_id for call in calls)


async def test_combined_service_rejects_mode_unsupported_by_room(hass, combined_room):
    room, climate_id, calls = combined_room
    ac = hass.states.get("climate.test_ac")
    hass.states.async_set(
        ac.entity_id, ac.state, dict(ac.attributes) | {"hvac_modes": ["off", "heat"]}
    )
    await hass.async_block_till_done()
    calls.clear()
    before = (room.mode, room.target_temperature, room.decision)
    with pytest.raises((HomeAssistantError, ValueError), match="Unsupported HVAC mode"):
        await hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": climate_id, "hvac_mode": "cool", "temperature": 24},
            blocking=True,
        )
    await hass.async_block_till_done()
    assert (room.mode, room.target_temperature, room.decision) == before
    assert not any(call["service_data"].get("entity_id") != climate_id for call in calls)


async def test_target_only_preserves_mode_and_heats(hass, combined_room):
    room, climate_id, _calls = combined_room
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": climate_id, "temperature": 24}, blocking=True
    )
    await hass.async_block_till_done()
    assert room.mode is VirtualMode.HEAT
    assert room.target_temperature == 24
    assert room.preset is Preset.COMFORT
    assert hass.states.get(climate_id).state == "heat"
    assert hass.states.get("climate.test_heater").state == "heat"
    assert hass.states.get("switch.test_heat_source").state == "on"


@pytest.mark.parametrize("target", [True, False, float("nan"), float("inf"), 4.5, 35.5, "24", None])
async def test_direct_combined_entity_rejects_invalid_target_before_mutation(
    hass, combined_room, target
):
    from custom_components.virtual_hvac.climate import VirtualRoomClimate

    room, _climate_id, calls = combined_room
    entity = VirtualRoomClimate("test", room)
    before = (room.mode, room.target_temperature, room.decision)
    with pytest.raises(ValueError, match="numeric" if isinstance(target, bool) else None):
        await entity.async_set_temperature(temperature=target, hvac_mode=HVACMode.COOL)
    assert (room.mode, room.target_temperature, room.decision) == before
    assert calls == []


async def test_combined_off_neutralizes_active_heat_and_retains_new_target(hass, combined_room):
    room, climate_id, calls = combined_room
    await room.async_set_target_temperature(24)
    await hass.async_block_till_done()
    assert room.heat_demand
    assert hass.states.get("switch.test_heat_source").state == "on"
    calls.clear()
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_id, "hvac_mode": "off", "temperature": 25},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert room.mode is VirtualMode.OFF
    assert room.target_temperature == 25
    assert not room.heat_demand
    assert hass.states.get(climate_id).state == "off"
    assert hass.states.get("climate.test_heater").state == "off"
    assert hass.states.get("climate.test_ac").state == "off"
    assert hass.states.get("switch.test_heat_source").state == "off"
    assert not any(
        call["service_data"].get("hvac_mode") == "heat"
        or (call["service"] == "turn_on" and call["domain"] == "switch")
        for call in calls
    )


async def test_combined_core_service_converts_fahrenheit_target(hass, combined_room):
    room, climate_id, _calls = combined_room
    hass.config.units = US_CUSTOMARY_SYSTEM
    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": climate_id, "hvac_mode": "cool", "temperature": 68},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert room.mode is VirtualMode.COOL
    assert room.target_temperature == pytest.approx(20)
    assert hass.states.get("climate.test_ac").state == "cool"
