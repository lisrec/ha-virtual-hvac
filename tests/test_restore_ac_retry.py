"""Restoration preserves intent without treating unknown AC states as safe."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service
from test_integration import make_entry, set_source_states

from custom_components.virtual_hvac.const import DOMAIN
from custom_components.virtual_hvac.control import Preset, VirtualMode


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable_state", ["unavailable", "unknown", None])
@pytest.mark.parametrize(
    ("mode", "expected_output"),
    [
        (VirtualMode.HEAT, "heat"),
        (VirtualMode.COOL, "cool"),
        (VirtualMode.DRY, "dry"),
        (VirtualMode.FAN_ONLY, "fan_only"),
        (VirtualMode.AUTO, "heat"),
    ],
)
async def test_ac_only_intent_survives_unavailable_output_setup_retry(
    hass, mode, expected_output, unavailable_state
):
    set_source_states(hass)
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    template, original = make_entry(shared=False)
    subentry = ConfigSubentry(
        data=MappingProxyType(dict(original.data) | {"heater_entity_id": None}),
        subentry_type=original.subentry_type,
        title=original.title,
        unique_id=original.unique_id,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=template.title,
        data=dict(template.data),
        subentries_data=[subentry.as_dict()],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    target = 20 if mode is VirtualMode.COOL else 24
    await room.async_set_target_temperature(target)
    await room.async_set_preset(Preset.SLEEP)
    await room.async_set_mode(mode)
    await hass.async_block_till_done()
    assert hass.states.get("climate.test_ac").state == expected_output
    assert await hass.config_entries.async_unload(entry.entry_id)
    old = hass.states.get("climate.test_ac")
    if unavailable_state is None:
        hass.states.async_remove("climate.test_ac")
    else:
        hass.states.async_set("climate.test_ac", unavailable_state, old.attributes)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    await hass.async_block_till_done()
    # Intent is retained, but the unavailable output is never armed or called ON.
    current = hass.states.get("climate.test_ac")
    assert current is None if unavailable_state is None else current.state == unavailable_state
    entry.async_cancel_retry_setup()
    set_source_states(hass)
    entry._async_setup_again(hass)
    await hass.async_block_till_done()
    try:
        restored = entry.runtime_data.rooms[subentry.subentry_id]
        assert restored.mode is mode
        assert restored.target_temperature == target
        assert restored.preset is Preset.SLEEP
        assert hass.states.get("climate.test_ac").state == expected_output
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
