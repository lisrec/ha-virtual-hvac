"""Constants for Virtual HVAC."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "virtual_hvac"
SUBENTRY_ROOM = "room"

PLATFORMS: tuple[Platform, ...] = (
    Platform.CLIMATE,
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
)

CONF_NAME = "name"
CONF_SHARED_HEAT_SOURCE = "shared_heat_source_entity_id"
CONF_ENABLE_SAFE_HEATING_DELAY = "enable_safe_heating_delay"
CONF_MIN_HEATING_ON = "minimum_seconds_heating_on"
CONF_MIN_HEATING_OFF = "minimum_seconds_heating_off"
CONF_TEMPERATURE_SENSORS = "temperature_sensor_entity_ids"
CONF_AC_ENTITY = "ac_entity_id"
CONF_HEATER_ENTITY = "heater_entity_id"
CONF_WINDOW_ENTITIES = "window_entity_ids"
CONF_RAPID_ENTITY = "rapid_entity_id"
CONF_SILENT_ENTITY = "silent_entity_id"
CONF_HEAT_HYSTERESIS_ON = "heating_hysteresis_on"
CONF_HEAT_HYSTERESIS_OFF = "heating_hysteresis_off"
CONF_COOL_HYSTERESIS_ON = "cooling_hysteresis_on"
CONF_COOL_HYSTERESIS_OFF = "cooling_hysteresis_off"
CONF_ENABLE_SAFE_COOLING_DELAY = "enable_safe_cooling_delay"
CONF_MIN_COOLING_ON = "minimum_seconds_cooling_on"
CONF_MIN_COOLING_OFF = "minimum_seconds_cooling_off"
CONF_REVERSAL_GUARD = "mode_reversal_guard_seconds"
CONF_TRV_OFFSET = "trv_target_offset"
CONF_BOOST_AC_HEAT = "boost_ac_heat_assist"
CONF_TEMPERATURE_MAX_AGE = "temperature_sensor_max_age_seconds"

LEGACY_CONF_SHARED_MIN_ON = "shared_minimum_on_seconds"
LEGACY_CONF_SHARED_MIN_OFF = "shared_minimum_off_seconds"
LEGACY_CONF_AC_MIN_OFF = "ac_minimum_off_seconds"
LEGACY_CONF_WINDOW_ENTITY = "window_entity_id"

DEFAULT_CONTROLLER_NAME = "Virtual HVAC"
DEFAULT_TARGET_TEMPERATURE = 21.0
MIN_TARGET_TEMPERATURE = 5.0
MAX_TARGET_TEMPERATURE = 35.0
