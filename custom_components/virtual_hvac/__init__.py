"""Virtual HVAC integration lifecycle."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ENABLE_SAFE_COOLING_DELAY,
    CONF_ENABLE_SAFE_HEATING_DELAY,
    CONF_MIN_COOLING_OFF,
    CONF_MIN_COOLING_ON,
    CONF_MIN_HEATING_OFF,
    CONF_MIN_HEATING_ON,
    DOMAIN,
    LEGACY_CONF_AC_MIN_OFF,
    LEGACY_CONF_SHARED_MIN_OFF,
    LEGACY_CONF_SHARED_MIN_ON,
    PLATFORMS,
    SUBENTRY_ROOM,
)
from .models import ControllerConfig, RoomConfig, validate_output_ownership
from .runtime import ControllerRuntime

VirtualHVACConfigEntry = ConfigEntry[ControllerRuntime]


async def async_migrate_entry(hass: HomeAssistant, entry: VirtualHVACConfigEntry) -> bool:
    """Migrate legacy protection fields to the 0.2 configuration contract."""
    if entry.version != 1:
        return False
    if entry.minor_version >= 2:
        return True

    controller_data = dict(entry.data)
    legacy_heating_on = controller_data.pop(LEGACY_CONF_SHARED_MIN_ON, 300)
    legacy_heating_off = controller_data.pop(LEGACY_CONF_SHARED_MIN_OFF, 180)
    controller_data.setdefault(CONF_ENABLE_SAFE_HEATING_DELAY, True)
    controller_data.setdefault(CONF_MIN_HEATING_ON, legacy_heating_on)
    controller_data.setdefault(CONF_MIN_HEATING_OFF, legacy_heating_off)

    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        room_data = dict(subentry.data)
        legacy_cooling_off = room_data.pop(LEGACY_CONF_AC_MIN_OFF, 300)
        room_data.setdefault(CONF_ENABLE_SAFE_COOLING_DELAY, True)
        room_data.setdefault(CONF_MIN_COOLING_ON, legacy_cooling_off)
        room_data.setdefault(CONF_MIN_COOLING_OFF, legacy_cooling_off)
        hass.config_entries.async_update_subentry(entry, subentry, data=room_data)

    hass.config_entries.async_update_entry(
        entry,
        data=controller_data,
        version=1,
        minor_version=2,
    )
    return True


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
