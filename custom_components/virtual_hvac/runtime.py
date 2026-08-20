"""Event-driven, serialized runtime for Virtual HVAC."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util.dt import utcnow
from homeassistant.util.unit_conversion import TemperatureConverter

from .actuators import ActuatorAdapter, async_set_switch_confirmed
from .control import (
    ControlDecision,
    ControlInput,
    ControlMemory,
    OutputMode,
    Preset,
    RoomController,
    VirtualMode,
)
from .heat_source import decide_heat_source
from .models import ControllerConfig, RoomConfig
from .protection import ProtectionTimestamps
from .temperature import average_valid_temperatures

Listener = Callable[[], None]
_AC_ACTIVE_MODES = {OutputMode.COOL, OutputMode.DRY, OutputMode.HEAT_ASSIST}
_AC_COMPRESSOR_INACTIVE_STATES = {STATE_OFF, "fan_only"}


class RoomRuntime:
    """Own room state, subscriptions, and one coalescing actuator writer."""

    def __init__(
        self,
        hass: HomeAssistant,
        subentry_id: str,
        config: RoomConfig,
        changed: Callable[[], None],
        timestamps: ProtectionTimestamps | None = None,
    ) -> None:
        self.hass = hass
        self.subentry_id = subentry_id
        self.config = config
        self.mode = VirtualMode.OFF
        self.preset = Preset.COMFORT
        self.target_temperature = 21.0
        self.decision = ControlDecision(
            OutputMode.OFF, False, False, None, False, False, "startup_disarmed"
        )
        self.physical_status = "startup_disarmed"
        self._memory = ControlMemory()
        self._controller = RoomController(config)
        self._actuators = ActuatorAdapter(hass, config)
        self._changed = changed
        self._timestamps = timestamps or ProtectionTimestamps(None)
        self._listeners: set[Listener] = set()
        self._remove_state_listener: Callable[[], None] | None = None
        self._cancel_timer: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._generation = 0
        self._ready = False
        self._stopping = False
        self._actuating = False

    @property
    def _output_timestamp_key(self) -> str:
        return f"room:{self.subentry_id}:output"

    @property
    def _ac_timestamp_key(self) -> str:
        return f"room:{self.subentry_id}:ac"

    async def async_start(self) -> None:
        """Subscribe while remaining disarmed until startup inputs are validated."""
        entities = set(self.config.temperature_sensor_entity_ids)
        entities.update(self.config.window_entity_ids)
        entities.update(
            entity_id
            for entity_id in (
                self.config.ac_entity_id,
                self.config.heater_entity_id,
                self.config.rapid_entity_id,
                self.config.silent_entity_id,
            )
            if entity_id is not None
        )
        self._remove_state_listener = async_track_state_change_event(
            self.hass, entities, self._async_source_changed
        )

    async def async_finish_startup(self) -> bool:
        """Neutralize known outputs, validate live inputs, then arm restored intent."""
        async with self._lock:
            self._actuating = True
            try:
                result = await self._actuators.async_neutralize(self.target_temperature)
            finally:
                self._actuating = False
            if not result.success:
                self.physical_status = "startup_neutralization_failed"
                self.decision = replace(self.decision, reason="startup_neutralization_failed")
                self._publish()
                return False
            if self.config.ac_entity_id is not None:
                self._timestamps.record(self._ac_timestamp_key)
            self._timestamps.record(self._output_timestamp_key)
            if not await self._inputs_authoritative():
                self.physical_status = "startup_inputs_not_authoritative"
                self.decision = replace(self.decision, reason="startup_inputs_not_authoritative")
                self._publish()
                return False
            self._ready = True
            self.physical_status = "outputs_neutral"
        await self.async_evaluate()
        return self.physical_status == "outputs_confirmed"

    async def async_stop(self, *, neutralize: bool = True) -> bool:
        """Neutralize before removing ownership; refuse cleanup on failed acknowledgement."""
        self._stopping = True
        self._ready = False
        self._generation += 1
        if neutralize:
            async with self._lock:
                was_ac_active = self._physical_ac_active()
                self._actuating = True
                try:
                    result = await self._actuators.async_neutralize(self.target_temperature)
                finally:
                    self._actuating = False
                if not result.success:
                    self.physical_status = "shutdown_neutralization_failed"
                    self._stopping = False
                    self._publish()
                    return False
                if was_ac_active:
                    self._timestamps.record(self._ac_timestamp_key)
                self._timestamps.record(self._output_timestamp_key)
                self.physical_status = "outputs_neutral"
                self.decision = replace(
                    self.decision,
                    output_mode=OutputMode.OFF,
                    heat_demand=False,
                    heater_active=False,
                    ac_target_temperature=None,
                    reason="shutdown_neutralized",
                )
        self._cleanup()
        return True

    @callback
    def _cleanup(self) -> None:
        if self._remove_state_listener is not None:
            self._remove_state_listener()
            self._remove_state_listener = None
        self._cancel_retry_timer()
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Register an entity-state listener."""
        self._listeners.add(listener)

        @callback
        def remove() -> None:
            self._listeners.discard(listener)

        return remove

    @callback
    def _async_source_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self._ready or self._stopping:
            return
        if event.data["entity_id"] in self.config.output_entity_ids() and (
            self._lock.locked() or self._physical_outputs_match_decision()
        ):
            return
        self.hass.async_create_task(
            self.async_evaluate(), f"Virtual HVAC room update {self.subentry_id}"
        )

    def _valid_temperature_values(self) -> list[object]:
        now = utcnow()
        values: list[object] = []
        for entity_id in self.config.temperature_sensor_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                continue
            max_age = self.config.temperature_sensor_max_age_seconds
            reported = getattr(state, "last_reported", state.last_updated)
            if max_age is not None and (now - reported).total_seconds() > max_age:
                continue
            source_unit = state.attributes.get("unit_of_measurement")
            try:
                value = float(state.state)
                if source_unit is not None:
                    value = TemperatureConverter.convert(value, source_unit, "°C")
            except (TypeError, ValueError):
                continue
            values.append(value)
        return values

    @property
    def current_temperature(self) -> float | None:
        """Return a fresh mean normalized to Celsius."""
        return average_valid_temperatures(self._valid_temperature_values())

    @property
    def available(self) -> bool:
        return self.current_temperature is not None and self._ready

    @property
    def heat_demand(self) -> bool:
        """Logical demand is fail-closed independently of physical reachability."""
        return self._ready and self.decision.heat_demand

    @property
    def status(self) -> str:
        return self.decision.reason

    def supported_virtual_modes(self) -> list[VirtualMode]:
        """Derive modes from currently configured and supported actuators."""
        result = [VirtualMode.OFF]
        if self.config.heater_entity_id is not None:
            result.append(VirtualMode.HEAT)
        ac_modes: list[str] = []
        if self.config.ac_entity_id is not None:
            state = self.hass.states.get(self.config.ac_entity_id)
            if state is not None:
                ac_modes = list(state.attributes.get("hvac_modes", []))
            result.extend(
                mode
                for mode in (
                    VirtualMode.COOL,
                    VirtualMode.DRY,
                    VirtualMode.FAN_ONLY,
                )
                if mode.value in ac_modes
            )
        if VirtualMode.HEAT in result and VirtualMode.COOL in result:
            result.append(VirtualMode.AUTO)
        return result

    async def async_restore(
        self, mode: VirtualMode, target_temperature: float, preset: Preset
    ) -> None:
        """Restore intent only; startup barrier owns the first physical actuation."""
        if mode in self.supported_virtual_modes():
            self.mode = mode
        if math.isfinite(target_temperature) and 5.0 <= target_temperature <= 35.0:
            self.target_temperature = target_temperature
        self.preset = preset
        self._notify()

    async def async_set_mode(self, mode: VirtualMode) -> None:
        if mode not in self.supported_virtual_modes():
            raise ValueError(f"Unsupported HVAC mode: {mode}")
        self.mode = mode
        await self.async_evaluate()

    async def async_set_target_temperature(self, temperature: float) -> None:
        if not math.isfinite(temperature) or not 5.0 <= temperature <= 35.0:
            raise ValueError("Target temperature must be between 5 and 35")
        self.target_temperature = temperature
        await self.async_evaluate()

    async def async_set_preset(self, preset: Preset) -> None:
        self.preset = preset
        await self.async_evaluate()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        if await self._actuators.async_set_fan_mode(fan_mode):
            self._notify()

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        if await self._actuators.async_set_swing_mode(swing_mode):
            self._notify()

    async def async_evaluate(self) -> None:
        """Serialize, coalesce, and invalidate stale reconciliations."""
        self._generation += 1
        requested_generation = self._generation
        async with self._lock:
            if requested_generation < self._generation or not self._ready:
                return
            while self._ready and not self._stopping:
                applying_generation = self._generation
                await self._async_reconcile_once()
                if applying_generation == self._generation:
                    break
                self._actuating = True
                try:
                    stale_neutral = await self._actuators.async_neutralize(self.target_temperature)
                finally:
                    self._actuating = False
                if not stale_neutral.success:
                    self.physical_status = "stale_command_neutralization_failed"
                    self._ready = False
                    self.decision = replace(
                        self.decision,
                        output_mode=OutputMode.OFF,
                        heat_demand=False,
                        heater_active=False,
                        reason="stale_command_neutralization_failed",
                    )
                    break
        self._publish()

    async def _async_reconcile_once(self) -> None:
        self._cancel_retry_timer()
        now = utcnow()
        decision = self._controller.decide(
            ControlInput(
                mode=self.mode,
                preset=self.preset,
                target_temperature=self.target_temperature,
                current_temperature=self.current_temperature,
                window_configured=bool(self.config.window_entity_ids),
                window_open=self._window_open(),
                ac_off_elapsed_seconds=self._ac_off_elapsed(now),
                mode_elapsed_seconds=self._timestamps.elapsed(self._output_timestamp_key, now),
                ac_on_elapsed_seconds=self._ac_on_elapsed(now),
            ),
            self._memory,
        )
        previous_output = self.decision.output_mode
        confirmed_at: datetime | None = None
        self._actuating = True
        try:
            actuation = await self._actuators.async_apply(decision, self.target_temperature)
        finally:
            self._actuating = False
        if not actuation.success:
            self._actuating = True
            try:
                neutral = await self._actuators.async_neutralize(self.target_temperature)
            finally:
                self._actuating = False
            decision = replace(
                decision,
                output_mode=OutputMode.OFF,
                heat_demand=False,
                heater_active=False,
                ac_target_temperature=None,
                reason=actuation.reason or "logical_path_unavailable",
            )
            self.physical_status = (
                "outputs_neutral_after_failure"
                if neutral.success
                else "physical_neutralization_failed"
            )
            if neutral.success:
                confirmed_at = utcnow()
        else:
            self.physical_status = "outputs_confirmed"
            confirmed_at = utcnow()
        if confirmed_at is not None and not actuation.success:
            # A failed path may have partially actuated before confirmed neutralization.
            self._timestamps.record(self._output_timestamp_key, confirmed_at)
            if self.config.ac_entity_id is not None:
                self._timestamps.record(self._ac_timestamp_key, confirmed_at)
        elif confirmed_at is not None and decision.output_mode != previous_output:
            self._timestamps.record(self._output_timestamp_key, confirmed_at)
            if (previous_output in _AC_ACTIVE_MODES) != (decision.output_mode in _AC_ACTIVE_MODES):
                self._timestamps.record(self._ac_timestamp_key, confirmed_at)
        last_active = self._memory.last_output_mode
        if decision.output_mode is not OutputMode.OFF:
            last_active = decision.output_mode
        elif self.mode is VirtualMode.OFF:
            last_active = OutputMode.OFF
        self._memory = ControlMemory(
            last_output_mode=last_active,
            heating_active=decision.heat_demand,
        )
        self.decision = decision
        if decision.retry_after_seconds is not None:
            self._cancel_timer = async_call_later(
                self.hass, decision.retry_after_seconds, self._async_retry
            )

    async def _inputs_authoritative(self) -> bool:
        if self.current_temperature is None:
            return False
        if self.config.window_entity_ids and self._window_open() is None:
            return False
        return await self._actuators.async_inputs_authoritative()

    def _window_open(self) -> bool | None:
        if not self.config.window_entity_ids:
            return False
        indeterminate = False
        for entity_id in self.config.window_entity_ids:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                indeterminate = True
            elif state.state == STATE_ON:
                return True
            elif state.state != STATE_OFF:
                indeterminate = True
        return None if indeterminate else False

    def _physical_ac_active(self) -> bool:
        entity_id = self.config.ac_entity_id
        state = self.hass.states.get(entity_id) if entity_id is not None else None
        return self._ac_compressor_active(state) is True

    @staticmethod
    def _ac_compressor_active(state: State | None) -> bool | None:
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return state.state not in _AC_COMPRESSOR_INACTIVE_STATES

    def _physical_outputs_match_decision(self) -> bool:
        """Ignore delayed acknowledgements but reconcile external output drift."""
        output = self.decision.output_mode
        ac_expected = {
            OutputMode.COOL: "cool",
            OutputMode.DRY: "dry",
            OutputMode.FAN_ONLY: "fan_only",
            OutputMode.HEAT_ASSIST: "heat",
        }.get(output, STATE_OFF)
        ac_state = (
            self.hass.states.get(self.config.ac_entity_id)
            if self.config.ac_entity_id is not None
            else None
        )
        if ac_state is not None and ac_state.state != ac_expected:
            return False
        heater_state = (
            self.hass.states.get(self.config.heater_entity_id)
            if self.config.heater_entity_id is not None
            else None
        )
        heater_expected_on = output in (OutputMode.HEAT, OutputMode.HEAT_ASSIST)
        if heater_state is not None:
            heater_is_on = heater_state.state in (STATE_ON, "heat")
            if heater_is_on != heater_expected_on:
                return False
        active = output is not OutputMode.OFF
        for entity_id, expected in (
            (self.config.rapid_entity_id, active and self.decision.rapid),
            (self.config.silent_entity_id, active and self.decision.silent),
        ):
            state = self.hass.states.get(entity_id) if entity_id is not None else None
            if state is not None and (state.state == STATE_ON) != expected:
                return False
        return True

    def _ac_off_elapsed(self, now: datetime) -> float:
        entity_id = self.config.ac_entity_id
        if entity_id is None:
            return math.inf
        state = self.hass.states.get(entity_id)
        compressor_active = self._ac_compressor_active(state)
        if compressor_active is None:
            return 0.0
        if compressor_active:
            return math.inf
        return self._timestamps.elapsed(self._ac_timestamp_key, now)

    def _ac_on_elapsed(self, now: datetime) -> float:
        entity_id = self.config.ac_entity_id
        if entity_id is None:
            return math.inf
        state = self.hass.states.get(entity_id)
        compressor_active = self._ac_compressor_active(state)
        if compressor_active is None:
            return 0.0
        if not compressor_active:
            return math.inf
        return self._timestamps.elapsed(self._ac_timestamp_key, now)

    @callback
    def _async_retry(self, now: datetime) -> None:
        self._cancel_timer = None
        self.hass.async_create_task(
            self.async_evaluate(), f"Virtual HVAC room retry {self.subentry_id}"
        )

    @callback
    def _cancel_retry_timer(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _publish(self) -> None:
        self._notify()
        self._changed()

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()


class ControllerRuntime:
    """Own room runtimes and the sole serialized shared-relay writer."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        config: ControllerConfig,
        rooms: dict[str, RoomConfig],
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.config = config
        self._timestamps = ProtectionTimestamps(hass, entry_id)
        self.rooms = {
            subentry_id: RoomRuntime(
                hass,
                subentry_id,
                room_config,
                self._async_room_changed,
                self._timestamps,
            )
            for subentry_id, room_config in rooms.items()
        }
        self.shared_status = "startup_disarmed"
        self.shared_physical_status = "startup_disarmed"
        self._listeners: set[Listener] = set()
        self._remove_shared_listener: Callable[[], None] | None = None
        self._cancel_shared_timer: Callable[[], None] | None = None
        self._shared_lock = asyncio.Lock()
        self._shared_generation = 0
        self._startup_complete = False
        self._stopping = False

    @property
    def _shared_timestamp_key(self) -> str:
        return "controller:shared_heat_source"

    async def async_start(self) -> None:
        """Load durable protection state and subscribe without actuating."""
        await self._timestamps.async_load()
        if self.config.shared_heat_source_entity_id is not None:
            self._remove_shared_listener = async_track_state_change_event(
                self.hass,
                [self.config.shared_heat_source_entity_id],
                self._async_shared_source_changed,
            )
        for room in self.rooms.values():
            await room.async_start()

    async def async_finish_startup(self) -> None:
        """Neutralize shared/room outputs before arming restored room intent."""
        if not await self._async_neutralize_shared("startup"):
            raise RuntimeError("shared heat source startup neutralization failed")
        for room in self.rooms.values():
            if not await room.async_finish_startup():
                await self._async_neutralize_all()
                raise RuntimeError(
                    f"room {room.subentry_id} startup barrier could not be satisfied"
                )
        self._startup_complete = True
        await self.async_evaluate_shared_heat_source()

    async def async_stop(self) -> bool:
        """Refuse unload if any physically reachable output cannot confirm neutral."""
        self._stopping = True
        self._startup_complete = False
        self._shared_generation += 1
        if not await self._async_neutralize_all():
            self._stopping = False
            return False
        if self._remove_shared_listener is not None:
            self._remove_shared_listener()
            self._remove_shared_listener = None
        self._cancel_shared_retry()
        for room in self.rooms.values():
            await room.async_stop(neutralize=False)
        self._listeners.clear()
        await self._timestamps.async_flush()
        return True

    async def _async_neutralize_all(self) -> bool:
        shared_safe = await self._async_neutralize_shared("shutdown")
        rooms_safe = True
        for room in self.rooms.values():
            if not await room.async_stop():
                rooms_safe = False
        return shared_safe and rooms_safe

    async def _async_neutralize_shared(self, phase: str) -> bool:
        entity_id = self.config.shared_heat_source_entity_id
        if entity_id is None:
            self.shared_status = "not_configured"
            self.shared_physical_status = "not_configured"
            return True
        async with self._shared_lock:
            state = self.hass.states.get(entity_id)
            if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self.shared_status = f"{phase}_neutralization_failed"
                self.shared_physical_status = "physical_state_unknown"
                self._notify()
                return False
            if not await async_set_switch_confirmed(self.hass, entity_id, False):
                self.shared_status = f"{phase}_neutralization_failed"
                self.shared_physical_status = "physical_off_not_confirmed"
                self._notify()
                return False
            self._timestamps.record(self._shared_timestamp_key)
            self.shared_status = f"{phase}_neutralized"
            self.shared_physical_status = "physical_off_confirmed"
        self._notify()
        return True

    @property
    def aggregate_heat_demand(self) -> bool:
        return self._startup_complete and any(room.heat_demand for room in self.rooms.values())

    @callback
    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        self._listeners.add(listener)

        @callback
        def remove() -> None:
            self._listeners.discard(listener)

        return remove

    @callback
    def _async_room_changed(self) -> None:
        if self._startup_complete and not self._stopping:
            self.hass.async_create_task(
                self.async_evaluate_shared_heat_source(),
                f"Virtual HVAC shared heat update {self.entry_id}",
            )
        self._notify()

    @callback
    def _async_shared_source_changed(self, event: Event[EventStateChangedData]) -> None:
        if not self._startup_complete or self._stopping:
            return
        self.hass.async_create_task(
            self.async_evaluate_shared_heat_source(),
            f"Virtual HVAC shared source reconcile {self.entry_id}",
        )

    async def async_evaluate_shared_heat_source(self) -> None:
        """Serialize and coalesce shared relay arbitration with durable guards."""
        self._shared_generation += 1
        requested_generation = self._shared_generation
        entity_id = self.config.shared_heat_source_entity_id
        if entity_id is None:
            self.shared_status = "not_configured"
            self.shared_physical_status = "not_configured"
            self._notify()
            return
        async with self._shared_lock:
            if requested_generation < self._shared_generation or self._stopping:
                return
            while not self._stopping:
                applying_generation = self._shared_generation
                self._cancel_shared_retry()
                state = self.hass.states.get(entity_id)
                if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                    relay_state = None
                    elapsed = 0.0
                else:
                    relay_state = (
                        state.state == STATE_ON if state.state in (STATE_ON, STATE_OFF) else None
                    )
                    elapsed = self._timestamps.elapsed(self._shared_timestamp_key, utcnow())
                decision = decide_heat_source(
                    self.aggregate_heat_demand,
                    relay_state,
                    elapsed,
                    self.config.minimum_seconds_heating_on,
                    self.config.minimum_seconds_heating_off,
                    safe_delay_enabled=self.config.enable_safe_heating_delay,
                )
                self.shared_status = decision.reason
                if relay_state is None:
                    self.shared_physical_status = "physical_state_unknown"
                elif decision.action is not None:
                    confirmed = await async_set_switch_confirmed(
                        self.hass, entity_id, decision.action
                    )
                    if not confirmed:
                        self.shared_status = "command_not_confirmed"
                        self.shared_physical_status = "physical_command_failed"
                        break
                    self._timestamps.record(self._shared_timestamp_key)
                    self.shared_physical_status = "physical_command_confirmed"
                else:
                    self.shared_physical_status = "physical_state_confirmed"
                if decision.retry_after_seconds is not None:
                    self._cancel_shared_timer = async_call_later(
                        self.hass,
                        decision.retry_after_seconds,
                        self._async_shared_retry,
                    )
                if applying_generation == self._shared_generation:
                    break
        self._notify()

    @callback
    def _async_shared_retry(self, now: datetime) -> None:
        self._cancel_shared_timer = None
        self.hass.async_create_task(
            self.async_evaluate_shared_heat_source(),
            f"Virtual HVAC shared heat retry {self.entry_id}",
        )

    @callback
    def _cancel_shared_retry(self) -> None:
        if self._cancel_shared_timer is not None:
            self._cancel_shared_timer()
            self._cancel_shared_timer = None

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
