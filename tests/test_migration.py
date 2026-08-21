from __future__ import annotations

from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_hvac import async_migrate_entry
from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM, WindowOpenBehavior


@pytest.mark.asyncio
async def test_migrate_legacy_settings_to_minor_version_four(hass) -> None:
    room = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Office",
                "temperature_sensor_entity_ids": ["sensor.temperature"],
                "ac_entity_id": "climate.ac",
                "window_entity_id": "binary_sensor.legacy_window",
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
    assert entry.minor_version == 4
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
    assert "window_entity_id" not in migrated_room.data
    assert migrated_room.data["window_entity_ids"] == ["binary_sensor.legacy_window"]
    assert migrated_room.data["ac_entity_ids"] == ["climate.ac"]
    assert migrated_room.data["heater_entity_ids"] == []
    assert migrated_room.data["window_open_behavior"] == WindowOpenBehavior.TURN_OFF_HVAC


@pytest.mark.asyncio
async def test_migrate_minor_two_room_without_window_to_empty_list(hass) -> None:
    room = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Office",
                "temperature_sensor_entity_ids": ["sensor.temperature"],
                "ac_entity_id": "climate.ac",
                "window_entity_id": "",
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
        minor_version=2,
        data={"name": "Virtual HVAC"},
        subentries_data=[room.as_dict()],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 4
    migrated_room = next(iter(entry.subentries.values()))
    assert migrated_room.data["window_entity_ids"] == []
    assert migrated_room.data["ac_entity_ids"] == ["climate.ac"]
    assert migrated_room.data["heater_entity_ids"] == []
    assert migrated_room.data["window_open_behavior"] == WindowOpenBehavior.TURN_OFF_HVAC


@pytest.mark.asyncio
async def test_migrate_multiple_rooms_and_remain_idempotent(hass) -> None:
    rooms = [
        ConfigSubentry(
            data=MappingProxyType(
                {
                    "name": f"Room {index}",
                    "temperature_sensor_entity_ids": [f"sensor.temperature_{index}"],
                    "ac_entity_id": f"climate.ac_{index}",
                    "window_entity_id": f"binary_sensor.window_{index}",
                }
            ),
            subentry_type=SUBENTRY_ROOM,
            title=f"Room {index}",
            unique_id=f"room-{index}",
        )
        for index in range(3)
    ]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        version=1,
        minor_version=2,
        data={"name": "Virtual HVAC"},
        subentries_data=[room.as_dict() for room in rooms],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)
    first_result = [dict(room.data) for room in entry.subentries.values()]
    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 4
    assert [dict(room.data) for room in entry.subentries.values()] == first_result
    assert sorted(room["window_entity_ids"] for room in first_result) == [
        ["binary_sensor.window_0"],
        ["binary_sensor.window_1"],
        ["binary_sensor.window_2"],
    ]
    assert sorted(room["ac_entity_ids"] for room in first_result) == [
        ["climate.ac_0"],
        ["climate.ac_1"],
        ["climate.ac_2"],
    ]


@pytest.mark.asyncio
async def test_resume_partially_completed_window_migration(hass) -> None:
    already_migrated = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Room A",
                "temperature_sensor_entity_ids": ["sensor.temperature_a"],
                "ac_entity_id": "climate.ac_a",
                "window_entity_ids": [
                    "binary_sensor.window_a1",
                    "binary_sensor.window_a2",
                ],
            }
        ),
        subentry_type=SUBENTRY_ROOM,
        title="Room A",
        unique_id="room-a",
    )
    pending = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Room B",
                "temperature_sensor_entity_ids": ["sensor.temperature_b"],
                "ac_entity_id": "climate.ac_b",
                "window_entity_id": "binary_sensor.window_b",
            }
        ),
        subentry_type=SUBENTRY_ROOM,
        title="Room B",
        unique_id="room-b",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Virtual HVAC",
        version=1,
        minor_version=2,
        data={"name": "Virtual HVAC"},
        subentries_data=[already_migrated.as_dict(), pending.as_dict()],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    rooms = {room.title: room.data for room in entry.subentries.values()}
    assert entry.minor_version == 4
    assert rooms["Room A"]["window_entity_ids"] == [
        "binary_sensor.window_a1",
        "binary_sensor.window_a2",
    ]
    assert rooms["Room B"]["window_entity_ids"] == ["binary_sensor.window_b"]
    assert rooms["Room A"]["ac_entity_ids"] == ["climate.ac_a"]
    assert rooms["Room B"]["ac_entity_ids"] == ["climate.ac_b"]
    assert "window_entity_id" not in rooms["Room B"]


@pytest.mark.asyncio
async def test_migrate_minor_three_preserves_existing_delay_choices(hass) -> None:
    room = ConfigSubentry(
        data=MappingProxyType(
            {
                "name": "Office",
                "temperature_sensor_entity_ids": ["sensor.temperature"],
                "ac_entity_id": "climate.ac",
                "heater_entity_id": "climate.trv",
                "enable_safe_cooling_delay": False,
                "minimum_seconds_cooling_on": 11,
                "minimum_seconds_cooling_off": 12,
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
        minor_version=3,
        data={
            "name": "Virtual HVAC",
            "enable_safe_heating_delay": False,
            "minimum_seconds_heating_on": 13,
            "minimum_seconds_heating_off": 14,
        },
        subentries_data=[room.as_dict()],
    )
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry)

    assert entry.minor_version == 4
    assert entry.data["enable_safe_heating_delay"] is False
    assert entry.data["minimum_seconds_heating_on"] == 13
    assert entry.data["minimum_seconds_heating_off"] == 14
    migrated_room = next(iter(entry.subentries.values()))
    assert migrated_room.data["enable_safe_cooling_delay"] is False
    assert migrated_room.data["minimum_seconds_cooling_on"] == 11
    assert migrated_room.data["minimum_seconds_cooling_off"] == 12
    assert migrated_room.data["ac_entity_ids"] == ["climate.ac"]
    assert migrated_room.data["heater_entity_ids"] == ["climate.trv"]
