"""Virtual climate entities for room subentries."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .control import OutputMode, Preset, VirtualMode
from .entity import VirtualHVACRoomEntity
from .runtime import ControllerRuntime, RoomRuntime

_HA_TO_VIRTUAL = {
    HVACMode.OFF: VirtualMode.OFF,
    HVACMode.HEAT: VirtualMode.HEAT,
    HVACMode.COOL: VirtualMode.COOL,
    HVACMode.DRY: VirtualMode.DRY,
    HVACMode.FAN_ONLY: VirtualMode.FAN_ONLY,
    HVACMode.HEAT_COOL: VirtualMode.AUTO,
}
_VIRTUAL_TO_HA = {value: key for key, value in _HA_TO_VIRTUAL.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[ControllerRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one virtual climate entity per room subentry."""
    for subentry_id, room in entry.runtime_data.rooms.items():
        async_add_entities(
            [VirtualRoomClimate(entry.entry_id, room)],
            config_subentry_id=subentry_id,
        )


class VirtualRoomClimate(VirtualHVACRoomEntity, ClimateEntity, RestoreEntity):
    """User-facing virtual thermostat for one room."""

    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5.0
    _attr_max_temp = 35.0
    _attr_target_temperature_step = 0.5

    def __init__(self, entry_id: str, room: RoomRuntime) -> None:
        super().__init__(entry_id, room, "climate", None)
        self._attr_preset_modes = [preset.value for preset in Preset]

    @property
    @override
    def available(self) -> bool:
        return self.room.available

    @property
    @override
    def current_temperature(self) -> float | None:
        return self.room.current_temperature

    @property
    @override
    def target_temperature(self) -> float:
        return self.room.target_temperature

    @property
    @override
    def hvac_mode(self) -> HVACMode:
        return _VIRTUAL_TO_HA[self.room.mode]

    @property
    @override
    def hvac_modes(self) -> list[HVACMode]:
        return [_VIRTUAL_TO_HA[mode] for mode in self.room.supported_virtual_modes()]

    @property
    @override
    def hvac_action(self) -> HVACAction:
        output = self.room.decision.output_mode
        if output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST):
            return HVACAction.HEATING
        if output is OutputMode.COOL:
            return HVACAction.COOLING
        if output is OutputMode.DRY:
            return HVACAction.DRYING
        if output is OutputMode.FAN_ONLY:
            return HVACAction.FAN
        if self.room.status in ("heat_target_satisfied", "auto_dead_band"):
            return HVACAction.IDLE
        return HVACAction.OFF

    @property
    @override
    def preset_mode(self) -> str:
        return self.room.preset.value

    @property
    @override
    def fan_modes(self) -> list[str] | None:
        states = [self.hass.states.get(entity_id) for entity_id in self.room.config.ac_entity_ids]
        if not states or any(state is None for state in states):
            return None
        common = set(states[0].attributes.get("fan_modes", []))  # type: ignore[union-attr]
        for state in states[1:]:
            common.intersection_update(state.attributes.get("fan_modes", []))  # type: ignore[union-attr]
        return [
            mode
            for mode in states[0].attributes.get("fan_modes", [])  # type: ignore[union-attr]
            if mode in common
        ]

    @property
    @override
    def fan_mode(self) -> str | None:
        states = [self.hass.states.get(entity_id) for entity_id in self.room.config.ac_entity_ids]
        if not states or any(state is None for state in states):
            return None
        modes = {state.attributes.get("fan_mode") for state in states}  # type: ignore[union-attr]
        return modes.pop() if len(modes) == 1 else None

    @property
    @override
    def swing_modes(self) -> list[str] | None:
        states = [self.hass.states.get(entity_id) for entity_id in self.room.config.ac_entity_ids]
        if not states or any(state is None for state in states):
            return None
        common = set(states[0].attributes.get("swing_modes", []))  # type: ignore[union-attr]
        for state in states[1:]:
            common.intersection_update(state.attributes.get("swing_modes", []))  # type: ignore[union-attr]
        return [
            mode
            for mode in states[0].attributes.get("swing_modes", [])  # type: ignore[union-attr]
            if mode in common
        ]

    @property
    @override
    def swing_mode(self) -> str | None:
        states = [self.hass.states.get(entity_id) for entity_id in self.room.config.ac_entity_ids]
        if not states or any(state is None for state in states):
            return None
        modes = {state.attributes.get("swing_mode") for state in states}  # type: ignore[union-attr]
        return modes.pop() if len(modes) == 1 else None

    @property
    @override
    def supported_features(self) -> ClimateEntityFeature:
        features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_OFF
            | ClimateEntityFeature.TURN_ON
        )
        if self.fan_modes:
            features |= ClimateEntityFeature.FAN_MODE
        if self.swing_modes:
            features |= ClimateEntityFeature.SWING_MODE
        return features

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "controller_status": self.room.status,
            "physical_output_status": self.room.physical_status,
            "valid_temperature_sensor_count": sum(
                1
                for entity_id in self.room.config.temperature_sensor_entity_ids
                if (state := self.hass.states.get(entity_id)) is not None
                and state.state not in ("unknown", "unavailable")
            ),
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        mode = VirtualMode.OFF
        target = self.room.target_temperature
        preset = Preset.COMFORT
        if last_state is not None:
            try:
                mode = _HA_TO_VIRTUAL[HVACMode(last_state.state)]
            except (KeyError, ValueError):
                mode = VirtualMode.OFF
            restored_target = last_state.attributes.get(ATTR_TEMPERATURE)
            if isinstance(restored_target, int | float):
                target = float(restored_target)
            try:
                preset = Preset(last_state.attributes.get("preset_mode", Preset.COMFORT))
            except ValueError:
                preset = Preset.COMFORT
        await self.room.async_restore(mode, target, preset)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        try:
            virtual_mode = _HA_TO_VIRTUAL[HVACMode(hvac_mode)]
        except (KeyError, ValueError) as err:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}") from err
        await self.room.async_set_mode(virtual_mode)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if not isinstance(temperature, int | float):
            raise ValueError("A numeric target temperature is required")
        await self.room.async_set_target_temperature(float(temperature))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        await self.room.async_set_preset(Preset(preset_mode))

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.room.async_set_fan_mode(fan_mode)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        await self.room.async_set_swing_mode(swing_mode)

    async def async_turn_off(self) -> None:
        await self.room.async_set_mode(VirtualMode.OFF)

    async def async_turn_on(self) -> None:
        modes = self.room.supported_virtual_modes()
        preferred = VirtualMode.AUTO if VirtualMode.AUTO in modes else modes[1]
        await self.room.async_set_mode(preferred)
