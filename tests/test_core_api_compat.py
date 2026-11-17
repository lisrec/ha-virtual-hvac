"""Cross-Core API contracts, exercised with each real Home Assistant fixture."""

from __future__ import annotations

import json

import pytest
from ha_fixtures import MockConfigEntry
from homeassistant import config_entries
from homeassistant.components.climate import ClimateEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import UnitOfTemperature
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from test_integration import entity_id, make_entry, set_source_states

from custom_components.virtual_hvac.climate import VirtualRoomClimate
from custom_components.virtual_hvac.const import DOMAIN


@pytest.mark.asyncio
async def test_room_metadata_uses_registered_parent_id_across_reload(hass) -> None:
    """Room devices retain their parent and subentry, without deprecated tuple links."""
    set_source_states(hass)
    entry, subentry = make_entry(shared=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = dr.async_get(hass)
    parent = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert parent is not None
    room = entry.runtime_data.rooms[subentry.subentry_id]
    facade = VirtualRoomClimate(entry.entry_id, room)
    facade.hass = hass
    metadata = facade.device_info
    assert metadata is not None
    assert metadata.get("via_device_id") == parent.id
    assert "via_device" not in metadata
    device = registry.async_get_device_by_identifier(
        (DOMAIN, f"{entry.entry_id}:{subentry.subentry_id}"), entry.entry_id
    )
    assert device is not None
    room_device_id = device.id
    assert device.via_device_id == parent.id
    for platform, suffix in (
        ("climate", "climate"),
        ("sensor", "status"),
        ("binary_sensor", "heat_demand"),
    ):
        registered = er.async_get(hass).async_get(
            entity_id(hass, platform, entry, subentry, suffix)
        )
        assert registered is not None
        assert registered.device_id == room_device_id
        assert registered.config_subentry_id == subentry.subentry_id
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    device = registry.async_get(room_device_id)
    assert device is not None
    assert device.via_device_id == parent.id
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_native_climate_temperatures_stay_live_without_modern_legacy_overrides(hass) -> None:
    """Use native Celsius properties; install old names only for an old Core."""
    set_source_states(hass)
    entry, subentry = make_entry(shared=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    facade = VirtualRoomClimate(entry.entry_id, room)
    assert facade.native_temperature_unit == UnitOfTemperature.CELSIUS
    assert facade.native_current_temperature == 21
    await room.async_set_target_temperature(24)
    assert facade.native_target_temperature == 24
    hass.states.async_set("sensor.test_temperature_two", "24", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert facade.native_current_temperature == 22
    if hasattr(ClimateEntity, "native_temperature_unit"):
        for name in ("current_temperature", "target_temperature", "temperature_unit"):
            assert name not in VirtualRoomClimate.__dict__
    else:
        assert facade.current_temperature == 22
        assert facade.target_temperature == 24
        assert facade.temperature_unit == UnitOfTemperature.CELSIUS
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("room_form", [False, True])
@pytest.mark.parametrize("reconfigure", [False, True])
async def test_flow_forms_use_core_schema_and_real_http_serializer(
    hass, room_form, reconfigure
) -> None:
    """Retain selectors, defaults and suggestions through Core's actual serializer."""
    from types import MappingProxyType

    from test_config_flow import CONTROLLER_DATA, ROOM_DATA, set_source_entities

    from custom_components.virtual_hvac.const import SUBENTRY_ROOM

    set_source_entities(hass)
    subentry = ConfigSubentry(
        data=MappingProxyType(ROOM_DATA | {"rapid_entity_id": "switch.rapid"}),
        subentry_type=SUBENTRY_ROOM,
        title="Saved room",
        unique_id="saved-room",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=CONTROLLER_DATA | {"shared_heat_source_entity_id": "switch.shared"},
        subentries_data=[subentry.as_dict()],
    )
    context = {
        "source": (config_entries.SOURCE_RECONFIGURE if reconfigure else config_entries.SOURCE_USER)
    }
    if room_form or reconfigure:
        entry.add_to_hass(hass)
    if room_form:
        manager = hass.config_entries.subentries
        if reconfigure:
            context["subentry_id"] = subentry.subentry_id
        result = await manager.async_init((entry.entry_id, SUBENTRY_ROOM), context=context)
    else:
        manager = hass.config_entries.flow
        if reconfigure:
            context["entry_id"] = entry.entry_id
        result = await manager.async_init(DOMAIN, context=context)
    assert result["type"] is FlowResultType.FORM
    assert type(result["data_schema"]) is type(cv.PLATFORM_SCHEMA)
    # This delegates to Probatio on modern Core and voluptuous-serialize on 2026.9.
    payload = FlowManagerIndexView(manager)._prepare_result_json(result)
    fields = {field["name"]: field for field in payload["data_schema"]}
    assert json.loads(json.dumps(payload))["type"] == "form"
    if room_form:
        assert fields["temperature_sensor_entity_ids"]["required"] is True
        assert fields["temperature_sensor_entity_ids"]["selector"]["entity"]["multiple"] is True
        assert fields["window_entity_ids"]["selector"]["entity"]["multiple"] is True
        assert fields["boost_ac_heat_assist"]["default"] is (not reconfigure)
        if reconfigure:
            assert fields["rapid_entity_id"]["description"]["suggested_value"] == "switch.rapid"
    else:
        assert fields["minimum_seconds_heating_on"]["default"] == 300
        assert fields["enable_safe_heating_delay"]["default"] is reconfigure
        if reconfigure:
            assert fields["shared_heat_source_entity_id"]["description"]["suggested_value"] == (
                "switch.shared"
            )
