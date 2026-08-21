"""UI configuration flows for Virtual HVAC."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, override
from uuid import uuid4

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    Platform,
    UnitOfTemperature,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)

from .const import (
    CONF_AC_ENTITIES,
    CONF_BOOST_AC_HEAT,
    CONF_COOL_HYSTERESIS_OFF,
    CONF_COOL_HYSTERESIS_ON,
    CONF_ENABLE_SAFE_COOLING_DELAY,
    CONF_ENABLE_SAFE_HEATING_DELAY,
    CONF_HEAT_HYSTERESIS_OFF,
    CONF_HEAT_HYSTERESIS_ON,
    CONF_HEATER_ENTITIES,
    CONF_MIN_COOLING_OFF,
    CONF_MIN_COOLING_ON,
    CONF_MIN_HEATING_OFF,
    CONF_MIN_HEATING_ON,
    CONF_NAME,
    CONF_RAPID_ENTITY,
    CONF_REVERSAL_GUARD,
    CONF_SHARED_HEAT_SOURCE,
    CONF_SILENT_ENTITY,
    CONF_TEMPERATURE_MAX_AGE,
    CONF_TEMPERATURE_SENSORS,
    CONF_TRV_OFFSET,
    CONF_WINDOW_ENTITIES,
    CONF_WINDOW_OPEN_BEHAVIOR,
    DEFAULT_CONTROLLER_NAME,
    DOMAIN,
    SUBENTRY_ROOM,
    WindowOpenBehavior,
)
from .models import ControllerConfig, RoomConfig, validate_output_ownership

_TEMPERATURE_UNITS = {
    UnitOfTemperature.CELSIUS,
    UnitOfTemperature.FAHRENHEIT,
    UnitOfTemperature.KELVIN,
}


def _number_selector(
    minimum: float, maximum: float, step: float, unit: str | None = None
) -> NumberSelector:
    """Create a numeric box selector with explicit bounds."""
    config = NumberSelectorConfig(
        min=minimum,
        max=maximum,
        step=step,
        mode=NumberSelectorMode.BOX,
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _controller_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=values.get(CONF_NAME, DEFAULT_CONTROLLER_NAME)
            ): TextSelector(),
            vol.Optional(
                CONF_SHARED_HEAT_SOURCE,
                description={"suggested_value": values.get(CONF_SHARED_HEAT_SOURCE)},
            ): EntitySelector(EntitySelectorConfig(domain=Platform.SWITCH)),
            vol.Required(
                CONF_ENABLE_SAFE_HEATING_DELAY,
                default=values.get(CONF_ENABLE_SAFE_HEATING_DELAY, False),
            ): BooleanSelector(),
            vol.Required(
                CONF_MIN_HEATING_ON, default=values.get(CONF_MIN_HEATING_ON, 300)
            ): _number_selector(0, 86_400, 1, "s"),
            vol.Required(
                CONF_MIN_HEATING_OFF, default=values.get(CONF_MIN_HEATING_OFF, 180)
            ): _number_selector(0, 86_400, 1, "s"),
        }
    )


def _room_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    values = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=values.get(CONF_NAME, "")): TextSelector(),
            vol.Required(
                CONF_TEMPERATURE_SENSORS,
                default=values.get(CONF_TEMPERATURE_SENSORS, []),
            ): EntitySelector(
                EntitySelectorConfig(
                    domain=Platform.SENSOR,
                    device_class="temperature",
                    multiple=True,
                )
            ),
            vol.Optional(
                CONF_AC_ENTITIES,
                default=values.get(CONF_AC_ENTITIES, []),
            ): EntitySelector(EntitySelectorConfig(domain=Platform.CLIMATE, multiple=True)),
            vol.Optional(
                CONF_HEATER_ENTITIES,
                default=values.get(CONF_HEATER_ENTITIES, []),
            ): EntitySelector(
                EntitySelectorConfig(domain=[Platform.CLIMATE, Platform.SWITCH], multiple=True)
            ),
            vol.Optional(
                CONF_WINDOW_ENTITIES,
                default=values.get(CONF_WINDOW_ENTITIES, []),
            ): EntitySelector(EntitySelectorConfig(domain=Platform.BINARY_SENSOR, multiple=True)),
            vol.Required(
                CONF_WINDOW_OPEN_BEHAVIOR,
                default=values.get(
                    CONF_WINDOW_OPEN_BEHAVIOR, WindowOpenBehavior.TURN_OFF_HVAC.value
                ),
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[behavior.value for behavior in WindowOpenBehavior],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_RAPID_ENTITY,
                description={"suggested_value": values.get(CONF_RAPID_ENTITY)},
            ): EntitySelector(EntitySelectorConfig(domain=Platform.SWITCH)),
            vol.Optional(
                CONF_SILENT_ENTITY,
                description={"suggested_value": values.get(CONF_SILENT_ENTITY)},
            ): EntitySelector(EntitySelectorConfig(domain=Platform.SWITCH)),
            vol.Required(
                CONF_HEAT_HYSTERESIS_ON,
                default=values.get(CONF_HEAT_HYSTERESIS_ON, 0.5),
            ): _number_selector(0.1, 5.0, 0.1, "°"),
            vol.Required(
                CONF_HEAT_HYSTERESIS_OFF,
                default=values.get(CONF_HEAT_HYSTERESIS_OFF, 0.3),
            ): _number_selector(0.1, 5.0, 0.1, "°"),
            vol.Required(
                CONF_COOL_HYSTERESIS_ON,
                default=values.get(CONF_COOL_HYSTERESIS_ON, 0.5),
            ): _number_selector(0.1, 5.0, 0.1, "°"),
            vol.Required(
                CONF_COOL_HYSTERESIS_OFF,
                default=values.get(CONF_COOL_HYSTERESIS_OFF, 0.3),
            ): _number_selector(0.1, 5.0, 0.1, "°"),
            vol.Required(
                CONF_ENABLE_SAFE_COOLING_DELAY,
                default=values.get(CONF_ENABLE_SAFE_COOLING_DELAY, False),
            ): BooleanSelector(),
            vol.Required(
                CONF_MIN_COOLING_ON, default=values.get(CONF_MIN_COOLING_ON, 300)
            ): _number_selector(0, 86_400, 1, "s"),
            vol.Required(
                CONF_MIN_COOLING_OFF, default=values.get(CONF_MIN_COOLING_OFF, 300)
            ): _number_selector(0, 86_400, 1, "s"),
            vol.Required(
                CONF_REVERSAL_GUARD, default=values.get(CONF_REVERSAL_GUARD, 300)
            ): _number_selector(0, 86_400, 1, "s"),
            vol.Required(
                CONF_TRV_OFFSET, default=values.get(CONF_TRV_OFFSET, 1.0)
            ): _number_selector(0, 5.0, 0.1, "°"),
            vol.Required(
                CONF_BOOST_AC_HEAT,
                default=values.get(CONF_BOOST_AC_HEAT, False),
            ): BooleanSelector(),
            vol.Optional(
                CONF_TEMPERATURE_MAX_AGE,
                description={"suggested_value": values.get(CONF_TEMPERATURE_MAX_AGE, 300)},
            ): _number_selector(1, 604_800, 1, "s"),
        }
    )


def _clean_optional_values(data: Mapping[str, Any]) -> dict[str, Any]:
    """Remove empty optional selector values before persistent storage."""
    return {key: value for key, value in data.items() if value not in (None, "")}


class VirtualHVACConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure the singleton Virtual HVAC controller."""

    VERSION = 1
    MINOR_VERSION = 4

    @classmethod
    @callback
    @override
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return supported room subentries."""
        return {SUBENTRY_ROOM: RoomSubentryFlow}

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Create the single controller entry."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors: dict[str, str] = {}
        if user_input is not None:
            clean = _clean_optional_values(user_input)
            try:
                config = ControllerConfig.from_mapping(clean)
            except (TypeError, ValueError):
                errors["base"] = "invalid_controller_config"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=config.name, data=config.to_mapping())
        return self.async_show_form(
            step_id="user", data_schema=_controller_schema(user_input), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure and reload global controller settings."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            clean = _clean_optional_values(user_input)
            try:
                config = ControllerConfig.from_mapping(clean)
            except (TypeError, ValueError):
                errors["base"] = "invalid_controller_config"
            else:
                try:
                    rooms = {
                        subentry_id: RoomConfig.from_mapping(dict(subentry.data))
                        for subentry_id, subentry in entry.subentries.items()
                        if subentry.subentry_type == SUBENTRY_ROOM
                    }
                    validate_output_ownership(config, rooms)
                except (KeyError, TypeError, ValueError):
                    errors["base"] = "actuator_already_assigned"
                else:
                    return self.async_update_and_abort(
                        entry, title=config.name, data=config.to_mapping()
                    )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_controller_schema(entry.data),
            errors=errors,
        )


class RoomSubentryFlow(ConfigSubentryFlow):
    """Create and reconfigure room subentries."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a room."""
        return await self._async_room_form(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Reconfigure an existing room."""
        return await self._async_room_form(user_input, reconfigure=True)

    async def _async_room_form(
        self, user_input: dict[str, Any] | None, *, reconfigure: bool
    ) -> SubentryFlowResult:
        entry = self._get_entry()
        current = self._get_reconfigure_subentry() if reconfigure else None
        defaults = {CONF_NAME: current.title, **current.data} if current is not None else None
        errors: dict[str, str] = {}
        if user_input is not None:
            clean = _clean_optional_values(user_input)
            try:
                room = RoomConfig.from_mapping(clean)
            except (KeyError, TypeError, ValueError):
                errors["base"] = "invalid_room_config"
            else:
                if not self._temperature_sensors_valid(room.temperature_sensor_entity_ids):
                    errors["base"] = "invalid_temperature_sensor"
                elif self._has_assignment_conflict(
                    entry, room, current.subentry_id if current else None
                ):
                    errors["base"] = "actuator_already_assigned"
                else:
                    stored = room.to_mapping()
                    stored[CONF_TEMPERATURE_SENSORS] = list(room.temperature_sensor_entity_ids)
                    stored[CONF_AC_ENTITIES] = list(room.ac_entity_ids)
                    stored[CONF_HEATER_ENTITIES] = list(room.heater_entity_ids)
                    stored[CONF_WINDOW_ENTITIES] = list(room.window_entity_ids)
                    stored[CONF_WINDOW_OPEN_BEHAVIOR] = room.window_open_behavior.value
                    if current is not None:
                        return self.async_update_and_abort(
                            entry, current, title=room.name, data=stored
                        )
                    return self.async_create_entry(
                        title=room.name,
                        unique_id=uuid4().hex,
                        data=stored,
                    )
        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=_room_schema(defaults),
            errors=errors,
        )

    def _temperature_sensors_valid(self, entity_ids: tuple[str, ...]) -> bool:
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None:
                return False
            if state.attributes.get(ATTR_DEVICE_CLASS) == "temperature":
                continue
            if state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) in _TEMPERATURE_UNITS:
                continue
            return False
        return True

    @staticmethod
    def _assigned_actuators(room: RoomConfig) -> set[str]:
        return set(room.output_entity_ids())

    def _has_assignment_conflict(
        self, entry: ConfigEntry, room: RoomConfig, ignored_subentry_id: str | None
    ) -> bool:
        requested = self._assigned_actuators(room)
        shared = entry.data.get(CONF_SHARED_HEAT_SOURCE)
        if shared is not None and shared in requested:
            return True
        for subentry_id, subentry in entry.subentries.items():
            if subentry_id == ignored_subentry_id or subentry.subentry_type != SUBENTRY_ROOM:
                continue
            try:
                existing = RoomConfig.from_mapping(dict(subentry.data))
            except (KeyError, TypeError, ValueError):
                continue
            if requested & self._assigned_actuators(existing):
                return True
        return False
