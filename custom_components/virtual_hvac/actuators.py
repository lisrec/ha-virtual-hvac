"""Home Assistant actuator adapter for Virtual HVAC."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from math import isclose

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ATTR_TEMPERATURE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_TEMPERATURE,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, split_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .control import ControlDecision, OutputMode
from .models import RoomConfig

COMMAND_ACK_TIMEOUT = 10.0
COMMAND_CALL_TIMEOUT = 15.0


@dataclass(frozen=True, slots=True)
class ActuationResult:
    """Result of applying one room decision."""

    success: bool
    reason: str | None = None


async def _async_wait_for_state(
    hass: HomeAssistant,
    entity_id: str,
    predicate: Callable[[State | None], bool],
) -> bool:
    """Wait a bounded time for an authoritative state acknowledgement."""
    if predicate(hass.states.get(entity_id)):
        return True
    event = asyncio.Event()

    async def changed(_event: Event[EventStateChangedData]) -> None:
        if predicate(hass.states.get(entity_id)):
            event.set()

    remove = async_track_state_change_event(hass, [entity_id], changed)
    try:
        if predicate(hass.states.get(entity_id)):
            return True
        async with asyncio.timeout(COMMAND_ACK_TIMEOUT):
            await event.wait()
        return True
    except TimeoutError:
        return False
    finally:
        remove()


async def _async_call_and_confirm(
    hass: HomeAssistant,
    domain: str,
    service: str,
    data: dict[str, object],
    entity_id: str,
    predicate: Callable[[State | None], bool],
) -> bool:
    """Issue one bounded service call and require physical-state convergence."""
    try:
        async with asyncio.timeout(COMMAND_CALL_TIMEOUT):
            await hass.services.async_call(domain, service, data, blocking=True)
    except (HomeAssistantError, TimeoutError):
        return False
    return await _async_wait_for_state(hass, entity_id, predicate)


async def async_set_switch_confirmed(hass: HomeAssistant, entity_id: str, enabled: bool) -> bool:
    """Set a switch and require a bounded state acknowledgement."""
    state = hass.states.get(entity_id)
    desired_state = STATE_ON if enabled else STATE_OFF
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return False
    if state.state == desired_state:
        return True
    return await _async_call_and_confirm(
        hass,
        "switch",
        SERVICE_TURN_ON if enabled else SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: entity_id},
        entity_id,
        lambda current: current is not None and current.state == desired_state,
    )


class ActuatorAdapter:
    """Translate decisions into serialized, acknowledged HA service calls."""

    def __init__(self, hass: HomeAssistant, config: RoomConfig) -> None:
        self.hass = hass
        self.config = config

    async def async_apply(
        self, decision: ControlDecision, target_temperature: float
    ) -> ActuationResult:
        """Apply outputs only after the opposite path is confirmed stopped."""
        output = decision.output_mode
        try:
            if output in (OutputMode.COOL, OutputMode.DRY, OutputMode.FAN_ONLY):
                if not await self._async_set_heater(False, target_temperature):
                    return ActuationResult(False, "heater_stop_not_confirmed")
                ac_mode = {
                    OutputMode.COOL: HVACMode.COOL,
                    OutputMode.DRY: HVACMode.DRY,
                    OutputMode.FAN_ONLY: HVACMode.FAN_ONLY,
                }[output]
                if not await self._async_set_ac(ac_mode, decision.ac_target_temperature):
                    await self.async_neutralize(target_temperature)
                    return ActuationResult(False, "ac_stop_or_start_not_confirmed")
            elif output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST):
                if not await self._async_set_ac(HVACMode.OFF, None):
                    return ActuationResult(False, "ac_stop_not_confirmed")
                if not await self._async_set_heater(True, target_temperature):
                    await self.async_neutralize(target_temperature)
                    return ActuationResult(False, "heater_start_not_confirmed")
                if output is OutputMode.HEAT_ASSIST and not await self._async_set_ac(
                    HVACMode.HEAT, decision.ac_target_temperature
                ):
                    await self.async_neutralize(target_temperature)
                    return ActuationResult(False, "ac_heat_assist_not_confirmed")
            elif not (await self.async_neutralize(target_temperature)).success:
                return ActuationResult(False, "neutralization_not_confirmed")

            active = output is not OutputMode.OFF
            if not await self._async_set_presets(
                decision.rapid if active else False,
                decision.silent if active else False,
            ):
                await self.async_neutralize(target_temperature)
                return ActuationResult(False, "preset_output_not_confirmed")
            return ActuationResult(True)
        except HomeAssistantError:
            return ActuationResult(False, "service_call_failed")

    async def async_neutralize(self, target_temperature: float) -> ActuationResult:
        """Attempt every OFF command and require all outputs to acknowledge neutral."""
        results = [
            await self._async_set_ac(HVACMode.OFF, None),
            await self._async_set_heater(False, target_temperature),
        ]
        if self.config.rapid_entity_id is not None:
            results.append(
                await async_set_switch_confirmed(self.hass, self.config.rapid_entity_id, False)
            )
        if self.config.silent_entity_id is not None:
            results.append(
                await async_set_switch_confirmed(self.hass, self.config.silent_entity_id, False)
            )
        return ActuationResult(
            all(results), None if all(results) else "neutralization_not_confirmed"
        )

    async def async_inputs_authoritative(self) -> bool:
        """Return whether each configured physical output currently has a known state."""
        for entity_id in self.config.output_entity_ids():
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                return False
        return True

    async def async_set_fan_mode(self, fan_mode: str) -> bool:
        """Pass a supported fan mode to the selected AC."""
        entity_id = self.config.ac_entity_id
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or fan_mode not in state.attributes.get("fan_modes", []):
            return False
        if state.attributes.get("fan_mode") == fan_mode:
            return True
        return await _async_call_and_confirm(
            self.hass,
            CLIMATE_DOMAIN,
            "set_fan_mode",
            {ATTR_ENTITY_ID: entity_id, "fan_mode": fan_mode},
            entity_id,
            lambda current: current is not None and current.attributes.get("fan_mode") == fan_mode,
        )

    async def async_set_swing_mode(self, swing_mode: str) -> bool:
        """Pass a supported swing mode to the selected AC."""
        entity_id = self.config.ac_entity_id
        if not entity_id:
            return False
        state = self.hass.states.get(entity_id)
        if state is None or swing_mode not in state.attributes.get("swing_modes", []):
            return False
        if state.attributes.get("swing_mode") == swing_mode:
            return True
        return await _async_call_and_confirm(
            self.hass,
            CLIMATE_DOMAIN,
            "set_swing_mode",
            {ATTR_ENTITY_ID: entity_id, "swing_mode": swing_mode},
            entity_id,
            lambda current: current is not None
            and current.attributes.get("swing_mode") == swing_mode,
        )

    async def _async_set_ac(self, mode: HVACMode, target_temperature: float | None) -> bool:
        entity_id = self.config.ac_entity_id
        if entity_id is None:
            return mode is HVACMode.OFF
        if not await self._async_set_climate_mode(entity_id, mode):
            return False
        if mode is not HVACMode.OFF and target_temperature is not None:
            return await self._async_set_climate_temperature(entity_id, target_temperature)
        return True

    async def _async_set_heater(self, enabled: bool, target_temperature: float) -> bool:
        entity_id = self.config.heater_entity_id
        if entity_id is None:
            return not enabled
        domain = split_entity_id(entity_id)[0]
        if domain == CLIMATE_DOMAIN:
            desired_mode = HVACMode.HEAT if enabled else HVACMode.OFF
            if not await self._async_set_climate_mode(entity_id, desired_mode):
                return False
            if enabled:
                return await self._async_set_climate_temperature(
                    entity_id, target_temperature + self.config.trv_target_offset
                )
            return True
        if domain == "switch":
            return await async_set_switch_confirmed(self.hass, entity_id, enabled)
        return False

    async def _async_set_climate_mode(self, entity_id: str, mode: HVACMode) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        if mode not in state.attributes.get("hvac_modes", []):
            return False
        if state.state == mode:
            return True
        return await _async_call_and_confirm(
            self.hass,
            CLIMATE_DOMAIN,
            SERVICE_SET_HVAC_MODE,
            {ATTR_ENTITY_ID: entity_id, ATTR_HVAC_MODE: mode},
            entity_id,
            lambda current: current is not None and current.state == mode,
        )

    async def _async_set_climate_temperature(self, entity_id: str, temperature: float) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return False
        current = state.attributes.get(ATTR_TEMPERATURE)
        if isinstance(current, int | float) and isclose(float(current), temperature, abs_tol=0.01):
            return True
        return await _async_call_and_confirm(
            self.hass,
            CLIMATE_DOMAIN,
            SERVICE_SET_TEMPERATURE,
            {ATTR_ENTITY_ID: entity_id, ATTR_TEMPERATURE: temperature},
            entity_id,
            lambda updated: updated is not None
            and isinstance(updated.attributes.get(ATTR_TEMPERATURE), int | float)
            and isclose(float(updated.attributes[ATTR_TEMPERATURE]), temperature, abs_tol=0.01),
        )

    async def _async_set_presets(self, rapid: bool, silent: bool) -> bool:
        results: list[bool] = []
        if self.config.rapid_entity_id is not None:
            results.append(
                await async_set_switch_confirmed(self.hass, self.config.rapid_entity_id, rapid)
            )
        if self.config.silent_entity_id is not None:
            results.append(
                await async_set_switch_confirmed(self.hass, self.config.silent_entity_id, silent)
            )
        return all(results)
