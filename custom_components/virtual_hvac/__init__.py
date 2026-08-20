"""Virtual HVAC integration lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS, SUBENTRY_ROOM
from .models import ControllerConfig, RoomConfig, validate_output_ownership
from .runtime import ControllerRuntime

VirtualHVACConfigEntry = ConfigEntry[ControllerRuntime]


async def async_setup_entry(hass: HomeAssistant, entry: VirtualHVACConfigEntry) -> bool:
    """Set up a controller and all room subentries behind a startup barrier."""
    try:
        controller_config = ControllerConfig.from_mapping(dict(entry.data))
        room_configs = {
            subentry_id: RoomConfig.from_mapping(dict(subentry.data))
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_ROOM
        }
        validate_output_ownership(controller_config, room_configs)
    except (KeyError, TypeError, ValueError) as err:
        raise ConfigEntryError("Invalid Virtual HVAC configuration") from err

    runtime = ControllerRuntime(hass, entry.entry_id, controller_config, room_configs)
    entry.runtime_data = runtime
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=controller_config.name,
        manufacturer="Virtual HVAC",
        model="Multi-room controller",
    )
    forwarded = False
    try:
        await runtime.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        forwarded = True
        await runtime.async_finish_startup()
    except Exception as err:
        safe = await runtime.async_stop()
        if forwarded:
            await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        entry.runtime_data = None  # type: ignore[assignment]
        if not safe:
            raise ConfigEntryError(
                "Setup failed and one or more outputs could not be confirmed OFF"
            ) from err
        raise
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VirtualHVACConfigEntry) -> bool:
    """Neutralize first and refuse unload when physical OFF cannot be confirmed."""
    runtime = entry.runtime_data
    if not await runtime.async_stop():
        return False
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    entry.runtime_data = None  # type: ignore[assignment]
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: VirtualHVACConfigEntry) -> None:
    """Use the one parent update listener for controller and subentry changes."""
    await hass.config_entries.async_reload(entry.entry_id)
