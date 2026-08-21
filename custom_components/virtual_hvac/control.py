"""Deterministic room HVAC decision engine."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .const import WindowOpenBehavior
from .models import RoomConfig


class VirtualMode(StrEnum):
    """Modes exposed by a virtual room thermostat."""

    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    DRY = "dry"
    FAN_ONLY = "fan_only"
    AUTO = "auto"


class Preset(StrEnum):
    """Portable presets independent of a physical AC vendor."""

    COMFORT = "comfort"
    BOOST = "boost"
    SLEEP = "sleep"


class OutputMode(StrEnum):
    """Mutually exclusive actuator paths selected by the controller."""

    OFF = "off"
    HEAT = "heat"
    HEAT_ASSIST = "heat_assist"
    COOL = "cool"
    DRY = "dry"
    FAN_ONLY = "fan_only"


@dataclass(frozen=True, slots=True)
class ControlMemory:
    """Small state snapshot required for hysteresis and reversal protection."""

    last_output_mode: OutputMode = OutputMode.OFF
    heating_active: bool = False


@dataclass(frozen=True, slots=True)
class ControlInput:
    """Current room input used for one deterministic decision."""

    mode: VirtualMode
    preset: Preset
    target_temperature: float
    current_temperature: float | None
    window_configured: bool
    window_open: bool | None
    ac_off_elapsed_seconds: float
    mode_elapsed_seconds: float
    ac_on_elapsed_seconds: float = math.inf


@dataclass(frozen=True, slots=True)
class ControlDecision:
    """Required outputs for one room at a point in time."""

    output_mode: OutputMode
    heat_demand: bool
    heater_active: bool
    ac_target_temperature: float | None
    rapid: bool
    silent: bool
    reason: str
    retry_after_seconds: int | None = None


class RoomController:
    """Evaluate room inputs without performing Home Assistant service calls."""

    def __init__(self, config: RoomConfig) -> None:
        self._config = config

    def decide(self, inputs: ControlInput, memory: ControlMemory) -> ControlDecision:
        """Return a fail-closed, mutually exclusive room decision."""
        rapid = inputs.preset is Preset.BOOST
        silent = inputs.preset is Preset.SLEEP

        if inputs.current_temperature is None or not math.isfinite(inputs.current_temperature):
            return self._off("no_valid_temperature", rapid=False, silent=False)
        if not math.isfinite(inputs.target_temperature):
            return self._off("invalid_target", rapid=False, silent=False)
        if inputs.mode is VirtualMode.OFF:
            return self._off("mode_off", rapid=False, silent=False)
        if inputs.window_configured:
            if inputs.window_open is None:
                return self._off("window_unavailable", rapid=False, silent=False)
            if inputs.window_open:
                if self._config.window_open_behavior is WindowOpenBehavior.IGNORE_OPEN_WINDOW:
                    pass
                elif self._config.window_open_behavior is WindowOpenBehavior.FALLBACK_TO_FAN_ONLY:
                    if not self._config.ac_entity_ids:
                        return self._off("window_fan_only_unavailable", rapid=False, silent=False)
                    return ControlDecision(
                        OutputMode.FAN_ONLY,
                        False,
                        False,
                        None,
                        False,
                        False,
                        "window_open_fan_only",
                    )
                else:
                    return self._off("window_open", rapid=False, silent=False)

        if inputs.mode is VirtualMode.COOL:
            if self._is_reversal(memory.last_output_mode, OutputMode.COOL, inputs):
                return self._protected("mode_reversal_guard", inputs)
            if protected := self._compressor_protection(inputs):
                return protected
            return ControlDecision(
                OutputMode.COOL,
                False,
                False,
                inputs.target_temperature,
                rapid,
                silent,
                "explicit_cool",
            )
        if inputs.mode is VirtualMode.DRY:
            if self._is_reversal(memory.last_output_mode, OutputMode.DRY, inputs):
                return self._protected("mode_reversal_guard", inputs)
            if protected := self._compressor_protection(inputs):
                return protected
            return ControlDecision(
                OutputMode.DRY, False, False, None, rapid, silent, "explicit_dry"
            )
        if inputs.mode is VirtualMode.FAN_ONLY:
            return ControlDecision(
                OutputMode.FAN_ONLY,
                False,
                False,
                None,
                rapid,
                silent,
                "explicit_fan_only",
            )
        if inputs.mode is VirtualMode.HEAT:
            if self._is_reversal(memory.last_output_mode, OutputMode.HEAT, inputs):
                return self._protected("mode_reversal_guard", inputs)
            return self._decide_heat(inputs, memory, rapid, silent)
        return self._decide_auto(inputs, memory, rapid, silent)

    def _decide_heat(
        self,
        inputs: ControlInput,
        memory: ControlMemory,
        rapid: bool,
        silent: bool,
    ) -> ControlDecision:
        temperature = inputs.current_temperature
        if temperature is None:
            return self._off("no_valid_temperature", rapid=False, silent=False)
        heating = memory.heating_active or memory.last_output_mode in (
            OutputMode.HEAT,
            OutputMode.HEAT_ASSIST,
        )
        if heating:
            heating = temperature < (
                inputs.target_temperature + self._config.heating_hysteresis_off
            )
        else:
            heating = temperature <= (
                inputs.target_temperature - self._config.heating_hysteresis_on
            )
        if not heating:
            return self._off("heat_target_satisfied", rapid=rapid, silent=silent)
        output = (
            OutputMode.HEAT_ASSIST
            if rapid and self._config.boost_ac_heat_assist and self._config.ac_entity_ids
            else OutputMode.HEAT
        )
        return ControlDecision(
            output,
            True,
            True,
            inputs.target_temperature if output is OutputMode.HEAT_ASSIST else None,
            rapid,
            silent,
            "heat_demand",
        )

    def _decide_auto(
        self,
        inputs: ControlInput,
        memory: ControlMemory,
        rapid: bool,
        silent: bool,
    ) -> ControlDecision:
        temperature = inputs.current_temperature
        if temperature is None:
            return self._off("no_valid_temperature", rapid=False, silent=False)
        target = inputs.target_temperature

        if memory.last_output_mode in (OutputMode.HEAT, OutputMode.HEAT_ASSIST):
            if temperature < target + self._config.heating_hysteresis_off:
                return ControlDecision(
                    OutputMode.HEAT,
                    True,
                    True,
                    None,
                    rapid,
                    silent,
                    "auto_continue_heat",
                )
        elif (
            memory.last_output_mode is OutputMode.COOL
            and temperature > target - self._config.cooling_hysteresis_off
        ):
            return ControlDecision(
                OutputMode.COOL,
                False,
                False,
                target,
                rapid,
                silent,
                "auto_continue_cool",
            )
        elif (
            memory.last_output_mode is OutputMode.COOL
            and self._config.enable_safe_cooling_delay
            and inputs.ac_on_elapsed_seconds < self._config.minimum_seconds_cooling_on
        ):
            return ControlDecision(
                OutputMode.COOL,
                False,
                False,
                target,
                rapid,
                silent,
                "ac_minimum_on",
                math.ceil(self._config.minimum_seconds_cooling_on - inputs.ac_on_elapsed_seconds),
            )

        wants_heat = temperature <= target - self._config.heating_hysteresis_on
        wants_cool = temperature >= target + self._config.cooling_hysteresis_on
        if wants_heat:
            if self._is_reversal(memory.last_output_mode, OutputMode.HEAT, inputs):
                return self._protected("mode_reversal_guard", inputs)
            return ControlDecision(
                OutputMode.HEAT,
                True,
                True,
                None,
                rapid,
                silent,
                "auto_heat",
            )
        if wants_cool:
            if self._is_reversal(memory.last_output_mode, OutputMode.COOL, inputs):
                return self._protected("mode_reversal_guard", inputs)
            if (
                self._config.enable_safe_cooling_delay
                and inputs.ac_off_elapsed_seconds < self._config.minimum_seconds_cooling_off
            ):
                return ControlDecision(
                    OutputMode.OFF,
                    False,
                    False,
                    None,
                    rapid,
                    silent,
                    "ac_minimum_off",
                    math.ceil(
                        self._config.minimum_seconds_cooling_off - inputs.ac_off_elapsed_seconds
                    ),
                )
            return ControlDecision(
                OutputMode.COOL,
                False,
                False,
                target,
                rapid,
                silent,
                "auto_cool",
            )
        return self._off("auto_dead_band", rapid=rapid, silent=silent)

    def _is_reversal(
        self, previous: OutputMode, requested: OutputMode, inputs: ControlInput
    ) -> bool:
        previous_is_heat = previous in (OutputMode.HEAT, OutputMode.HEAT_ASSIST)
        previous_is_cool = previous in (OutputMode.COOL, OutputMode.DRY)
        requested_is_heat = requested in (OutputMode.HEAT, OutputMode.HEAT_ASSIST)
        requested_is_cool = requested in (OutputMode.COOL, OutputMode.DRY)
        is_reversal = (previous_is_heat and requested_is_cool) or (
            previous_is_cool and requested_is_heat
        )
        return (
            is_reversal and inputs.mode_elapsed_seconds < self._config.mode_reversal_guard_seconds
        )

    def _compressor_protection(self, inputs: ControlInput) -> ControlDecision | None:
        if not self._config.enable_safe_cooling_delay or (
            inputs.ac_off_elapsed_seconds >= self._config.minimum_seconds_cooling_off
        ):
            return None
        return ControlDecision(
            OutputMode.OFF,
            False,
            False,
            None,
            False,
            False,
            "ac_minimum_off",
            math.ceil(self._config.minimum_seconds_cooling_off - inputs.ac_off_elapsed_seconds),
        )

    def _protected(self, reason: str, inputs: ControlInput) -> ControlDecision:
        return ControlDecision(
            OutputMode.OFF,
            False,
            False,
            None,
            False,
            False,
            reason,
            math.ceil(self._config.mode_reversal_guard_seconds - inputs.mode_elapsed_seconds),
        )

    @staticmethod
    def _off(reason: str, *, rapid: bool, silent: bool) -> ControlDecision:
        return ControlDecision(OutputMode.OFF, False, False, None, rapid, silent, reason)
