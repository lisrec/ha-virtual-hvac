"""Shared entity mixins for Virtual HVAC."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN
from .runtime import ControllerRuntime, RoomRuntime

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import ChildDeviceInfo
else:
    # Core 2026.8 supports the same metadata through DeviceInfo, but has no
    # ChildDeviceInfo type. Keep annotations resolvable on that supported Core.
    ChildDeviceInfo = getattr(dr, "ChildDeviceInfo", DeviceInfo)


class VirtualHVACRoomEntity:
    """Common metadata and update wiring for room entities."""

    hass: HomeAssistant
    _attr_has_entity_name = True
    _attr_name: str | None
    _attr_unique_id: str | None

    def __init__(
        self,
        entry_id: str,
        room: RoomRuntime,
        unique_suffix: str,
        name: str | None,
    ) -> None:
        self.room = room
        self._entry_id = entry_id
        self._attr_unique_id = f"{entry_id}_{room.subentry_id}_{unique_suffix}"
        self._attr_name = name

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo | None:
        """Resolve the registered parent only after HA attaches the entity."""
        # The lifecycle registers the controller before forwarding any platform.
        parent = dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, self._entry_id), self._entry_id
        )
        if parent is None:
            raise RuntimeError("Virtual HVAC controller device must be registered first")
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry_id}:{self.room.subentry_id}")},
            name=self.room.config.name,
            manufacturer="Virtual HVAC",
            model="Room controller",
            via_device_id=parent.id,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe the entity to room updates."""
        await super().async_added_to_hass()  # type: ignore[misc]
        self.async_on_remove(self.room.async_add_listener(self._async_room_updated))  # type: ignore[attr-defined]

    def _async_room_updated(self) -> None:
        self.async_write_ha_state()  # type: ignore[attr-defined]


class VirtualHVACControllerEntity:
    """Common metadata and update wiring for controller entities."""

    _attr_has_entity_name = True
    _attr_device_info: DeviceInfo | ChildDeviceInfo | None
    _attr_name: str | None
    _attr_unique_id: str | None

    def __init__(
        self,
        entry_id: str,
        runtime: ControllerRuntime,
        unique_suffix: str,
        name: str,
    ) -> None:
        self.runtime = runtime
        self._attr_unique_id = f"{entry_id}_{unique_suffix}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=runtime.config.name,
            manufacturer="Virtual HVAC",
            model="Multi-room controller",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe the entity to controller updates."""
        await super().async_added_to_hass()  # type: ignore[misc]
        self.async_on_remove(self.runtime.async_add_listener(self._async_controller_updated))  # type: ignore[attr-defined]

    def _async_controller_updated(self) -> None:
        self.async_write_ha_state()  # type: ignore[attr-defined]
