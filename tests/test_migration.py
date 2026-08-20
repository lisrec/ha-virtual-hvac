from __future__ import annotations

from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_hvac import async_migrate_entry
from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM


@pytest.mark.asyncio
async def test_migrate_legacy_protection_settings_to_minor_version_two(hass) -> None:
    room = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Office",
                "temperature_sensor_entity_ids": ["sensor.temperature"],
                "ac_entity_id": "climate.ac",
                "heating_hysteresis_on": 0.5,
                "heating_hysteresis_off": 0.3,
                "cooling_hysteresis_on": 0.5,
                "cooling_hysteresis_off": 0.3,
                "ac_minimum_off_seconds": 45,
                "mode_reversal_guard_seconds": 300,
                "trv_target_offset": 1.0,
                "boost_ac_heat_assist": False,
            }
        ),
        subentry_type=SUBENTRY_ROOM,
        title="Office",
        unique_id="office",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        version=1,
        minor_version=1,
        data={
            "name": "Virtual HVAC",
            "shared_heat_source_entity_id": "switch.heat_source",
            "shared_minimum_on_seconds": 20,
            "shared_minimum_off_seconds": 15,
        },
        subentries_data=[room.as_dict()],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 1
    assert entry.minor_version == 2
    assert dict(entry.data) == {
        "name": "Virtual HVAC",
        "shared_heat_source_entity_id": "switch.heat_source",
        "enable_safe_heating_delay": True,
        "minimum_seconds_heating_on": 20,
        "minimum_seconds_heating_off": 15,
    }
    migrated_room = next(iter(entry.subentries.values()))
    assert "ac_minimum_off_seconds" not in migrated_room.data
    assert migrated_room.data["enable_safe_cooling_delay"] is True
    assert migrated_room.data["minimum_seconds_cooling_on"] == 45
    assert migrated_room.data["minimum_seconds_cooling_off"] == 45
