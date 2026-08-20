"""Heat-demand binary sensors for Virtual HVAC."""

from __future__ import annotations

from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import VirtualHVACControllerEntity, VirtualHVACRoomEntity
from .runtime import ControllerRuntime, RoomRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ControllerRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up aggregate and per-room heat-demand sensors."""
    async_add_entities([AggregateHeatDemand(entry.entry_id, entry.runtime_data)])
    for subentry_id, room in entry.runtime_data.rooms.items():
        async_add_entities(
            [RoomHeatDemand(entry.entry_id, room)],
            config_subentry_id=subentry_id,
        )


class AggregateHeatDemand(VirtualHVACControllerEntity, BinarySensorEntity):
    """OR aggregation of every valid room heat demand."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True

    def __init__(self, entry_id: str, runtime: ControllerRuntime) -> None:
        super().__init__(entry_id, runtime, "aggregate_heat_demand", "Aggregate heat demand")

    @property
    @override
    def is_on(self) -> bool:
        return self.runtime.aggregate_heat_demand


class RoomHeatDemand(VirtualHVACRoomEntity, BinarySensorEntity):
    """Effective fail-closed heat demand for one room."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_should_poll = False

    def __init__(self, entry_id: str, room: RoomRuntime) -> None:
        super().__init__(entry_id, room, "heat_demand", "Heat demand")

    @property
    @override
    def is_on(self) -> bool:
        return self.room.heat_demand

    @property
    @override
    def available(self) -> bool:
        return self.room.available
