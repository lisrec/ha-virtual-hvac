"""Privacy-preserving diagnostics for Virtual HVAC."""

from __future__ import annotations

from enum import Enum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import SUBENTRY_ROOM
from .models import RoomConfig
from .runtime import ControllerRuntime, RoomRuntime

_SAFE_ROOM_STATUSES = frozenset(
    {
        "ac_minimum_on",
        "ac_minimum_off",
        "ac_heat_assist_not_confirmed",
        "ac_stop_not_confirmed",
        "ac_stop_or_start_not_confirmed",
        "auto_continue_cool",
        "auto_continue_heat",
        "auto_cool",
        "auto_dead_band",
        "auto_heat",
        "explicit_cool",
        "explicit_dry",
        "explicit_fan_only",
        "heat_demand",
        "heat_target_satisfied",
        "heater_start_not_confirmed",
        "heater_stop_not_confirmed",
        "invalid_target",
        "logical_path_unavailable",
        "mode_off",
        "mode_reversal_guard",
        "neutralization_not_confirmed",
        "no_valid_temperature",
        "preset_output_not_confirmed",
        "service_call_failed",
        "shutdown_neutralized",
        "stale_command_neutralization_failed",
        "startup_disarmed",
        "startup_inputs_not_authoritative",
        "startup_neutralization_failed",
        "window_open",
        "window_unavailable",
    }
)
_SAFE_ROOM_PHYSICAL_STATUSES = frozenset(
    {
        "outputs_confirmed",
        "outputs_neutral",
        "outputs_neutral_after_failure",
        "physical_neutralization_failed",
        "shutdown_neutralization_failed",
        "stale_command_neutralization_failed",
        "startup_disarmed",
        "startup_inputs_not_authoritative",
        "startup_neutralization_failed",
    }
)
_SAFE_SHARED_STATUSES = frozenset(
    {
        "command_not_confirmed",
        "minimum_off",
        "minimum_on",
        "not_configured",
        "relay_unavailable",
        "service_call_failed",
        "shutdown_neutralization_failed",
        "shutdown_neutralized",
        "startup_disarmed",
        "startup_neutralization_failed",
        "startup_neutralized",
        "steady_off",
        "steady_on",
        "turn_off",
        "turn_on",
    }
)
_SAFE_SHARED_PHYSICAL_STATUSES = frozenset(
    {
        "not_configured",
        "physical_command_confirmed",
        "physical_command_failed",
        "physical_off_confirmed",
        "physical_off_not_confirmed",
        "physical_state_confirmed",
        "physical_state_unknown",
        "startup_disarmed",
    }
)
_SAFE_MODES = frozenset({"auto", "cool", "dry", "fan_only", "heat", "off"})
_SAFE_PRESETS = frozenset({"boost", "comfort", "sleep"})
_SAFE_OUTPUT_MODES = frozenset({"cool", "dry", "fan_only", "heat", "heat_assist", "off"})


def _safe_category(value: object, allowed: frozenset[str]) -> str:
    """Return only a known category, never arbitrary runtime text."""
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) and value in allowed else "unrecognized"


def _room_diagnostics(config: RoomConfig, runtime: RoomRuntime | None) -> dict[str, Any]:
    """Describe room structure and safe state categories without identifiers."""
    result: dict[str, Any] = {
        "temperature_sensor_count": len(config.temperature_sensor_entity_ids),
        "configured_inputs": {"window": config.window_entity_id is not None},
        "configured_outputs": {
            "ac": config.ac_entity_id is not None,
            "heater": config.heater_entity_id is not None,
            "rapid": config.rapid_entity_id is not None,
            "silent": config.silent_entity_id is not None,
        },
        "settings": {
            "heating_hysteresis_on": config.heating_hysteresis_on,
            "heating_hysteresis_off": config.heating_hysteresis_off,
            "cooling_hysteresis_on": config.cooling_hysteresis_on,
            "cooling_hysteresis_off": config.cooling_hysteresis_off,
            "enable_safe_cooling_delay": config.enable_safe_cooling_delay,
            "minimum_seconds_cooling_on": config.minimum_seconds_cooling_on,
            "minimum_seconds_cooling_off": config.minimum_seconds_cooling_off,
            "mode_reversal_guard_seconds": config.mode_reversal_guard_seconds,
            "trv_target_offset": config.trv_target_offset,
            "boost_ac_heat_assist": config.boost_ac_heat_assist,
            "temperature_sensor_max_age_seconds": (config.temperature_sensor_max_age_seconds),
        },
    }
    if runtime is not None:
        decision = runtime.decision
        result["runtime"] = {
            "available": runtime.available,
            "mode": _safe_category(runtime.mode, _SAFE_MODES),
            "preset": _safe_category(runtime.preset, _SAFE_PRESETS),
            "output_mode": _safe_category(decision.output_mode, _SAFE_OUTPUT_MODES),
            "heat_demand": runtime.heat_demand,
            "status": _safe_category(runtime.status, _SAFE_ROOM_STATUSES),
            "physical_status": _safe_category(
                runtime.physical_status, _SAFE_ROOM_PHYSICAL_STATUSES
            ),
            "retry_after_seconds": decision.retry_after_seconds,
        }
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[ControllerRuntime]
) -> dict[str, Any]:
    """Return mandatory-redacted diagnostics for a controller config entry."""
    del hass
    runtime = entry.runtime_data
    rooms: list[dict[str, Any]] = []
    for subentry_id, subentry in entry.subentries.items():
        if subentry.subentry_type != SUBENTRY_ROOM:
            continue
        try:
            config = RoomConfig.from_mapping(dict(subentry.data))
        except (KeyError, TypeError, ValueError):
            rooms.append({"configuration_valid": False})
            continue
        rooms.append(_room_diagnostics(config, runtime.rooms.get(subentry_id)))

    controller_config = runtime.config
    return {
        "controller": {
            "room_count": len(rooms),
            "shared_heat_source_configured": controller_config.shared_heat_source_entity_id
            is not None,
            "enable_safe_heating_delay": controller_config.enable_safe_heating_delay,
            "minimum_seconds_heating_on": controller_config.minimum_seconds_heating_on,
            "minimum_seconds_heating_off": controller_config.minimum_seconds_heating_off,
            "runtime": {
                "aggregate_heat_demand": runtime.aggregate_heat_demand,
                "shared_status": _safe_category(runtime.shared_status, _SAFE_SHARED_STATUSES),
                "shared_physical_status": _safe_category(
                    runtime.shared_physical_status, _SAFE_SHARED_PHYSICAL_STATUSES
                ),
            },
        },
        "rooms": rooms,
    }
