"""Diagnostic state sensors for Virtual HVAC."""

from __future__ import annotations

from typing import override

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import VirtualHVACControllerEntity, VirtualHVACRoomEntity
from .runtime import ControllerRuntime, RoomRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ControllerRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up controller and room diagnostic sensors."""
    async_add_entities([SharedHeatSourceStatus(entry.entry_id, entry.runtime_data)])
    for subentry_id, room in entry.runtime_data.rooms.items():
        async_add_entities(
            [RoomControllerStatus(entry.entry_id, room)],
            config_subentry_id=subentry_id,
        )


class SharedHeatSourceStatus(VirtualHVACControllerEntity, SensorEntity):
    """Expose the current shared relay arbitration reason."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry_id: str, runtime: ControllerRuntime) -> None:
        super().__init__(entry_id, runtime, "shared_heat_source_status", "Heat source status")

    @property
    @override
    def native_value(self) -> str:
        return self.runtime.shared_status


class RoomControllerStatus(VirtualHVACRoomEntity, SensorEntity):
    """Expose the effective room decision reason."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry_id: str, room: RoomRuntime) -> None:
        super().__init__(entry_id, room, "status", "Controller status")

    @property
    @override
    def native_value(self) -> str:
        return self.room.status

    @property
    @override
    def available(self) -> bool:
        return self.room.available
