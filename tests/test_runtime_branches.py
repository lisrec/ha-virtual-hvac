"""Focused runtime lifecycle, reconciliation, and safety-branch tests."""

from __future__ import annotations

import math
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.util.dt import utcnow

from custom_components.virtual_hvac.actuators import ActuationResult
from custom_components.virtual_hvac.control import (
    ControlDecision,
    ControlMemory,
    OutputMode,
    Preset,
    VirtualMode,
)
from custom_components.virtual_hvac.models import ControllerConfig, RoomConfig
from custom_components.virtual_hvac.protection import ProtectionTimestamps
from custom_components.virtual_hvac.runtime import ControllerRuntime, RoomRuntime


def room_config(**overrides: object) -> RoomConfig:
    values: dict[str, object] = {
        "name": "Room",
        "temperature_sensor_entity_ids": ("sensor.temperature",),
        "ac_entity_id": "climate.ac",
        "heater_entity_id": "switch.heater",
        "window_entity_id": "binary_sensor.window",
        "rapid_entity_id": "switch.rapid",
        "silent_entity_id": "switch.silent",
        "minimum_seconds_cooling_on": 0,
        "minimum_seconds_cooling_off": 0,
        "mode_reversal_guard_seconds": 0,
    }
    values.update(overrides)
    return RoomConfig(**values)  # type: ignore[arg-type]


def make_room(hass, **overrides: object) -> RoomRuntime:
    return RoomRuntime(hass, "room-id", room_config(**overrides), Mock())


def set_authoritative_states(hass) -> None:
    hass.states.async_set("sensor.temperature", "20.0", {"unit_of_measurement": "°C"})
    hass.states.async_set("climate.ac", HVACMode.OFF, {"hvac_modes": [HVACMode.OFF, HVACMode.COOL]})
    hass.states.async_set("switch.heater", STATE_OFF)
    hass.states.async_set("binary_sensor.window", STATE_OFF)
    hass.states.async_set("switch.rapid", STATE_OFF)
    hass.states.async_set("switch.silent", STATE_OFF)


def off_decision(reason: str = "test") -> ControlDecision:
    return ControlDecision(OutputMode.OFF, False, False, None, False, False, reason)


@pytest.mark.asyncio
async def test_room_startup_failure_stays_disarmed_and_publishes(hass, monkeypatch) -> None:
    room = make_room(hass)
    changed = Mock()
    listener = Mock()
    room._changed = changed
    room.async_add_listener(listener)
    monkeypatch.setattr(
        room._actuators,
        "async_neutralize",
        AsyncMock(return_value=ActuationResult(False, "not confirmed")),
    )

    assert not await room.async_finish_startup()
    assert not room._ready
    assert room.status == "startup_neutralization_failed"
    assert room.physical_status == "startup_neutralization_failed"
    changed.assert_called_once()
    listener.assert_called_once()


@pytest.mark.asyncio
async def test_room_startup_rejects_non_authoritative_inputs_after_neutralizing(
    hass, monkeypatch
) -> None:
    room = make_room(hass)
    monkeypatch.setattr(
        room._actuators, "async_neutralize", AsyncMock(return_value=ActuationResult(True))
    )

    assert not await room.async_finish_startup()
    assert room.status == "startup_inputs_not_authoritative"
    assert room.physical_status == "startup_inputs_not_authoritative"


@pytest.mark.asyncio
async def test_room_startup_records_stopped_ac_before_arming(hass, monkeypatch) -> None:
    set_authoritative_states(hass)
    hass.states.async_set(
        "climate.ac", HVACMode.COOL, {"hvac_modes": [HVACMode.OFF, HVACMode.COOL]}
    )
    timestamps = Mock(spec=ProtectionTimestamps)
    room = RoomRuntime(hass, "room-id", room_config(), Mock(), timestamps)
    monkeypatch.setattr(
        room._actuators, "async_neutralize", AsyncMock(return_value=ActuationResult(True))
    )
    monkeypatch.setattr(room, "_inputs_authoritative", AsyncMock(return_value=True))

    async def evaluate() -> None:
        room.physical_status = "outputs_confirmed"

    monkeypatch.setattr(room, "async_evaluate", evaluate)

    assert await room.async_finish_startup()
    assert room._ready
    assert timestamps.record.call_count == 2


@pytest.mark.asyncio
async def test_room_stop_refuses_cleanup_when_neutralization_fails(hass, monkeypatch) -> None:
    room = make_room(hass)
    cleanup = Mock()
    monkeypatch.setattr(room, "_cleanup", cleanup)
    monkeypatch.setattr(
        room._actuators, "async_neutralize", AsyncMock(return_value=ActuationResult(False))
    )

    assert not await room.async_stop()
    assert not room._stopping
    assert room.physical_status == "shutdown_neutralization_failed"
    cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_room_stop_neutralizes_state_and_removes_callbacks(hass, monkeypatch) -> None:
    room = make_room(hass)
    removed = Mock()
    cancelled = Mock()
    room._remove_state_listener = removed
    room._cancel_timer = cancelled
    room._ready = True
    room.decision = ControlDecision(OutputMode.HEAT, True, True, None, False, False, "heat")
    monkeypatch.setattr(
        room._actuators, "async_neutralize", AsyncMock(return_value=ActuationResult(True))
    )

    assert await room.async_stop()
    assert room.decision.output_mode is OutputMode.OFF
    assert room.status == "shutdown_neutralized"
    assert room.physical_status == "outputs_neutral"
    removed.assert_called_once()
    cancelled.assert_called_once()
    assert not room._listeners


@pytest.mark.asyncio
async def test_room_listener_can_be_removed_idempotently(hass) -> None:
    room = make_room(hass)
    listener = Mock()
    remove = room.async_add_listener(listener)
    remove()
    remove()
    room._notify()
    listener.assert_not_called()


@pytest.mark.asyncio
async def test_source_changes_only_schedule_needed_reconciliation(hass, monkeypatch) -> None:
    room = make_room(hass)
    evaluate = AsyncMock()
    monkeypatch.setattr(room, "async_evaluate", evaluate)
    sensor_event = SimpleNamespace(data={"entity_id": "sensor.temperature"})

    room._async_source_changed(sensor_event)
    evaluate.assert_not_awaited()

    room._ready = True
    room._async_source_changed(sensor_event)
    await hass.async_block_till_done()
    evaluate.assert_awaited_once()

    evaluate.reset_mock()
    room._stopping = True
    room._async_source_changed(sensor_event)
    evaluate.assert_not_awaited()


@pytest.mark.asyncio
async def test_temperature_collection_ignores_stale_invalid_and_unknown_sources(hass) -> None:
    room = make_room(
        hass,
        temperature_sensor_entity_ids=(
            "sensor.good",
            "sensor.invalid",
            "sensor.unknown",
            "sensor.stale",
        ),
        temperature_sensor_max_age_seconds=300,
    )
    hass.states.async_set("sensor.good", "68", {"unit_of_measurement": "°F"})
    hass.states.async_set("sensor.invalid", "not-a-number")
    hass.states.async_set("sensor.unknown", "unknown")
    hass.states.async_set("sensor.stale", "30")
    stale = hass.states.get("sensor.stale")
    assert stale is not None
    stale.last_reported = utcnow() - timedelta(hours=1)

    assert room.current_temperature == pytest.approx(20.0)
    assert not room.available
    room._ready = True
    assert room.available


@pytest.mark.asyncio
async def test_supported_modes_follow_live_ac_capabilities(hass) -> None:
    heater_only = make_room(hass, ac_entity_id=None)
    assert heater_only.supported_virtual_modes() == [VirtualMode.OFF, VirtualMode.HEAT]

    room = make_room(hass)
    assert room.supported_virtual_modes() == [VirtualMode.OFF, VirtualMode.HEAT]
    hass.states.async_set(
        "climate.ac",
        HVACMode.OFF,
        {"hvac_modes": [HVACMode.OFF, HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY]},
    )
    assert room.supported_virtual_modes() == [
        VirtualMode.OFF,
        VirtualMode.HEAT,
        VirtualMode.COOL,
        VirtualMode.DRY,
        VirtualMode.FAN_ONLY,
        VirtualMode.AUTO,
    ]


@pytest.mark.asyncio
async def test_restore_ignores_unsupported_mode_and_invalid_target(hass) -> None:
    room = make_room(hass, ac_entity_id=None)
    listener = Mock()
    room.async_add_listener(listener)

    await room.async_restore(VirtualMode.COOL, math.nan, Preset.SLEEP)

    assert room.mode is VirtualMode.OFF
    assert room.target_temperature == 21.0
    assert room.preset is Preset.SLEEP
    listener.assert_called_once()


@pytest.mark.asyncio
async def test_room_rejects_unsupported_mode_and_out_of_range_target(hass) -> None:
    room = make_room(hass, ac_entity_id=None)
    with pytest.raises(ValueError, match="Unsupported HVAC mode"):
        await room.async_set_mode(VirtualMode.COOL)
    with pytest.raises(ValueError, match="between 5 and 35"):
        await room.async_set_target_temperature(float("inf"))
    with pytest.raises(ValueError, match="between 5 and 35"):
        await room.async_set_target_temperature(36.0)


@pytest.mark.asyncio
async def test_fan_and_swing_notify_only_after_confirmed_commands(hass, monkeypatch) -> None:
    room = make_room(hass)
    listener = Mock()
    room.async_add_listener(listener)
    monkeypatch.setattr(room._actuators, "async_set_fan_mode", AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(room._actuators, "async_set_swing_mode", AsyncMock(return_value=True))

    await room.async_set_fan_mode("quiet")
    listener.assert_not_called()
    await room.async_set_fan_mode("auto")
    await room.async_set_swing_mode("vertical")
    assert listener.call_count == 2


@pytest.mark.asyncio
async def test_actuation_failure_forces_logical_off_and_reports_neutralization(
    hass, monkeypatch
) -> None:
    set_authoritative_states(hass)
    room = make_room(hass)
    room._ready = True
    wanted = ControlDecision(OutputMode.HEAT, True, True, None, False, False, "heat")
    monkeypatch.setattr(room._controller, "decide", Mock(return_value=wanted))
    monkeypatch.setattr(
        room._actuators,
        "async_apply",
        AsyncMock(return_value=ActuationResult(False, "heater_start_not_confirmed")),
    )
    monkeypatch.setattr(
        room._actuators, "async_neutralize", AsyncMock(return_value=ActuationResult(False))
    )

    await room.async_evaluate()

    assert room.decision.output_mode is OutputMode.OFF
    assert room.status == "heater_start_not_confirmed"
    assert room.physical_status == "physical_neutralization_failed"


@pytest.mark.asyncio
async def test_stale_actuation_that_cannot_neutralize_disarms_room(hass, monkeypatch) -> None:
    set_authoritative_states(hass)
    room = make_room(hass)
    room._ready = True
    monkeypatch.setattr(room._controller, "decide", Mock(return_value=off_decision()))

    async def apply(_decision, _target) -> ActuationResult:
        room._generation += 1
        return ActuationResult(True)

    monkeypatch.setattr(room._actuators, "async_apply", apply)
    monkeypatch.setattr(
        room._actuators, "async_neutralize", AsyncMock(return_value=ActuationResult(False))
    )

    await room.async_evaluate()

    assert not room._ready
    assert room.status == "stale_command_neutralization_failed"
    assert room.physical_status == "stale_command_neutralization_failed"


@pytest.mark.asyncio
async def test_input_authority_requires_temperature_window_and_outputs(hass, monkeypatch) -> None:
    room = make_room(hass)
    monkeypatch.setattr(room._actuators, "async_inputs_authoritative", AsyncMock(return_value=True))
    assert not await room._inputs_authoritative()

    hass.states.async_set("sensor.temperature", "20")
    assert not await room._inputs_authoritative()
    hass.states.async_set("binary_sensor.window", STATE_OFF)
    assert await room._inputs_authoritative()


@pytest.mark.asyncio
async def test_window_and_ac_state_helpers_fail_closed(hass) -> None:
    room = make_room(hass)
    assert room._window_open() is None
    hass.states.async_set("binary_sensor.window", STATE_UNAVAILABLE)
    assert room._window_open() is None
    hass.states.async_set("binary_sensor.window", STATE_ON)
    assert room._window_open() is True
    hass.states.async_set("binary_sensor.window", "unexpected")
    assert room._window_open() is None

    assert room._ac_off_elapsed(utcnow()) == 0.0
    hass.states.async_set("climate.ac", HVACMode.COOL)
    assert math.isinf(room._ac_off_elapsed(utcnow()))
    no_ac = make_room(hass, ac_entity_id=None)
    assert math.isinf(no_ac._ac_off_elapsed(utcnow()))


@pytest.mark.asyncio
async def test_physical_output_matching_detects_each_drift_type(hass) -> None:
    set_authoritative_states(hass)
    room = make_room(hass)
    assert room._physical_outputs_match_decision()

    hass.states.async_set("climate.ac", HVACMode.COOL)
    assert not room._physical_outputs_match_decision()
    hass.states.async_set("climate.ac", HVACMode.OFF)
    hass.states.async_set("switch.heater", STATE_ON)
    assert not room._physical_outputs_match_decision()
    hass.states.async_set("switch.heater", STATE_OFF)
    hass.states.async_set("switch.rapid", STATE_ON)
    assert not room._physical_outputs_match_decision()


@pytest.mark.asyncio
async def test_retry_callbacks_clear_timer_and_schedule_evaluation(hass, monkeypatch) -> None:
    room = make_room(hass)
    cancelled = Mock()
    room._cancel_timer = cancelled
    room._cancel_retry_timer()
    cancelled.assert_called_once()
    assert room._cancel_timer is None

    evaluate = AsyncMock()
    monkeypatch.setattr(room, "async_evaluate", evaluate)
    room._async_retry(utcnow())
    await hass.async_block_till_done()
    evaluate.assert_awaited_once()


def make_controller(hass, *, shared: bool = True) -> ControllerRuntime:
    return ControllerRuntime(
        hass,
        "entry-id",
        ControllerConfig(
            name="Controller",
            shared_heat_source_entity_id="switch.shared" if shared else None,
            minimum_seconds_heating_on=0,
            minimum_seconds_heating_off=0,
        ),
        {},
    )


@pytest.mark.asyncio
async def test_controller_shared_neutralization_handles_unknown_and_unconfirmed_state(
    hass, monkeypatch
) -> None:
    runtime = make_controller(hass)
    listener = Mock()
    runtime.async_add_listener(listener)

    assert not await runtime._async_neutralize_shared("startup")
    assert runtime.shared_physical_status == "physical_state_unknown"

    hass.states.async_set("switch.shared", STATE_ON)
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.async_set_switch_confirmed",
        AsyncMock(return_value=False),
    )
    assert not await runtime._async_neutralize_shared("shutdown")
    assert runtime.shared_status == "shutdown_neutralization_failed"
    assert runtime.shared_physical_status == "physical_off_not_confirmed"
    assert listener.call_count == 2


@pytest.mark.asyncio
async def test_controller_shared_neutralization_confirms_off_and_records_transition(
    hass, monkeypatch
) -> None:
    runtime = make_controller(hass)
    hass.states.async_set("switch.shared", STATE_ON)
    record = Mock()
    runtime._timestamps.record = record
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.async_set_switch_confirmed",
        AsyncMock(return_value=True),
    )

    assert await runtime._async_neutralize_shared("startup")
    assert runtime.shared_status == "startup_neutralized"
    assert runtime.shared_physical_status == "physical_off_confirmed"
    record.assert_called_once_with(runtime._shared_timestamp_key)


@pytest.mark.asyncio
async def test_controller_finish_startup_neutralizes_all_after_room_failure(
    hass, monkeypatch
) -> None:
    runtime = make_controller(hass, shared=False)
    room = SimpleNamespace(
        subentry_id="bad-room", async_finish_startup=AsyncMock(return_value=False)
    )
    runtime.rooms = {"bad-room": room}
    neutralize_all = AsyncMock(return_value=True)
    monkeypatch.setattr(runtime, "_async_neutralize_all", neutralize_all)

    with pytest.raises(RuntimeError, match="bad-room"):
        await runtime.async_finish_startup()
    neutralize_all.assert_awaited_once()


@pytest.mark.asyncio
async def test_controller_stop_refuses_cleanup_if_any_output_is_unsafe(hass, monkeypatch) -> None:
    runtime = make_controller(hass)
    monkeypatch.setattr(runtime, "_async_neutralize_all", AsyncMock(return_value=False))

    assert not await runtime.async_stop()
    assert not runtime._stopping


@pytest.mark.asyncio
async def test_controller_stop_cleans_listeners_rooms_and_storage(hass, monkeypatch) -> None:
    runtime = make_controller(hass, shared=False)
    remove_shared = Mock()
    cancel_timer = Mock()
    listener = Mock()
    room = SimpleNamespace(async_stop=AsyncMock(return_value=True))
    runtime._remove_shared_listener = remove_shared
    runtime._cancel_shared_timer = cancel_timer
    runtime._listeners.add(listener)
    runtime.rooms = {"room": room}
    monkeypatch.setattr(runtime, "_async_neutralize_all", AsyncMock(return_value=True))
    monkeypatch.setattr(runtime._timestamps, "async_flush", AsyncMock())

    assert await runtime.async_stop()
    remove_shared.assert_called_once()
    cancel_timer.assert_called_once()
    room.async_stop.assert_awaited_once_with(neutralize=False)
    runtime._timestamps.async_flush.assert_awaited_once()
    assert not runtime._listeners


@pytest.mark.asyncio
async def test_neutralize_all_attempts_every_room_even_after_failure(hass, monkeypatch) -> None:
    runtime = make_controller(hass, shared=False)
    first = SimpleNamespace(async_stop=AsyncMock(return_value=False))
    second = SimpleNamespace(async_stop=AsyncMock(return_value=True))
    runtime.rooms = {"first": first, "second": second}
    monkeypatch.setattr(runtime, "_async_neutralize_shared", AsyncMock(return_value=True))

    assert not await runtime._async_neutralize_all()
    first.async_stop.assert_awaited_once()
    second.async_stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_callbacks_respect_startup_and_shutdown_barriers(hass, monkeypatch) -> None:
    runtime = make_controller(hass)
    evaluate = AsyncMock()
    monkeypatch.setattr(runtime, "async_evaluate_shared_heat_source", evaluate)

    runtime._async_room_changed()
    runtime._async_shared_source_changed(SimpleNamespace())
    evaluate.assert_not_awaited()

    runtime._startup_complete = True
    runtime._async_room_changed()
    runtime._async_shared_source_changed(SimpleNamespace())
    await hass.async_block_till_done()
    assert evaluate.await_count == 2

    runtime._stopping = True
    runtime._async_shared_source_changed(SimpleNamespace())
    assert evaluate.await_count == 2


@pytest.mark.asyncio
async def test_shared_evaluation_reports_unknown_physical_state(hass) -> None:
    runtime = make_controller(hass)
    runtime._startup_complete = True

    await runtime.async_evaluate_shared_heat_source()

    assert runtime.shared_status == "relay_unavailable"
    assert runtime.shared_physical_status == "physical_state_unknown"


@pytest.mark.asyncio
async def test_shared_evaluation_reports_unconfirmed_command(hass, monkeypatch) -> None:
    runtime = make_controller(hass)
    runtime._startup_complete = True
    hass.states.async_set("switch.shared", STATE_OFF)
    runtime.rooms = {"room": SimpleNamespace(heat_demand=True)}
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.async_set_switch_confirmed",
        AsyncMock(return_value=False),
    )

    await runtime.async_evaluate_shared_heat_source()

    assert runtime.shared_status == "command_not_confirmed"
    assert runtime.shared_physical_status == "physical_command_failed"


@pytest.mark.asyncio
async def test_shared_evaluation_schedules_and_cancels_protection_retry(hass, monkeypatch) -> None:
    runtime = make_controller(hass)
    runtime._startup_complete = True
    hass.states.async_set("switch.shared", STATE_OFF)
    decision = SimpleNamespace(reason="minimum_off", action=None, retry_after_seconds=12)
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.decide_heat_source", Mock(return_value=decision)
    )
    cancel = Mock()
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.async_call_later", Mock(return_value=cancel)
    )

    await runtime.async_evaluate_shared_heat_source()
    assert runtime.shared_physical_status == "physical_state_confirmed"
    assert runtime._cancel_shared_timer is cancel

    runtime._cancel_shared_retry()
    cancel.assert_called_once()
    assert runtime._cancel_shared_timer is None


@pytest.mark.asyncio
async def test_shared_retry_and_listener_removal(hass, monkeypatch) -> None:
    runtime = make_controller(hass)
    listener = Mock()
    remove = runtime.async_add_listener(listener)
    remove()
    runtime._notify()
    listener.assert_not_called()

    evaluate = AsyncMock()
    monkeypatch.setattr(runtime, "async_evaluate_shared_heat_source", evaluate)
    runtime._async_shared_retry(utcnow())
    await hass.async_block_till_done()
    evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_retry_turns_on_after_first_start_with_relay_already_off(
    hass, monkeypatch
) -> None:
    started = utcnow()
    runtime = ControllerRuntime(
        hass,
        "entry-id",
        ControllerConfig(
            name="Controller",
            shared_heat_source_entity_id="switch.shared",
            minimum_seconds_heating_on=12,
            minimum_seconds_heating_off=12,
        ),
        {},
    )
    hass.states.async_set("switch.shared", STATE_OFF)
    runtime.rooms = {"room": SimpleNamespace(heat_demand=True)}
    confirmed = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.async_set_switch_confirmed", confirmed
    )
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: started)

    assert await runtime._async_neutralize_shared("startup")
    runtime._startup_complete = True
    await runtime.async_evaluate_shared_heat_source()
    assert runtime.shared_status == "minimum_off"

    runtime._cancel_shared_retry()
    monkeypatch.setattr(
        "custom_components.virtual_hvac.runtime.utcnow", lambda: started + timedelta(seconds=13)
    )
    runtime._async_shared_retry(started + timedelta(seconds=13))
    await hass.async_block_till_done()

    assert runtime.shared_status == "turn_on"
    assert confirmed.await_args_list[-1].args == (hass, "switch.shared", True)


@pytest.mark.asyncio
async def test_fan_only_must_wait_for_cooling_minimum_off_before_cool(hass, monkeypatch) -> None:
    now = utcnow()
    set_authoritative_states(hass)
    hass.states.async_set(
        "climate.ac",
        HVACMode.FAN_ONLY,
        {"hvac_modes": [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY]},
    )
    room = make_room(
        hass,
        enable_safe_cooling_delay=True,
        minimum_seconds_cooling_off=300,
    )
    room._ready = True
    room.mode = VirtualMode.COOL
    room._timestamps.record(room._ac_timestamp_key, now - timedelta(seconds=10))
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: now)
    monkeypatch.setattr(
        room._actuators, "async_apply", AsyncMock(return_value=ActuationResult(True))
    )

    await room.async_evaluate()
    room._cancel_retry_timer()

    assert not room._physical_ac_active()
    assert room.decision.output_mode is OutputMode.OFF
    assert room.status == "ac_minimum_off"


@pytest.mark.asyncio
async def test_cooling_minimum_on_does_not_restart_compressor_from_fan_only(
    hass, monkeypatch
) -> None:
    now = utcnow()
    set_authoritative_states(hass)
    hass.states.async_set("sensor.temperature", "21.6", {"unit_of_measurement": "°C"})
    hass.states.async_set(
        "climate.ac",
        HVACMode.FAN_ONLY,
        {"hvac_modes": [HVACMode.OFF, HVACMode.COOL, HVACMode.FAN_ONLY]},
    )
    room = make_room(
        hass,
        enable_safe_cooling_delay=True,
        minimum_seconds_cooling_on=300,
    )
    room._ready = True
    room.mode = VirtualMode.AUTO
    room.target_temperature = 22.0
    room._memory = ControlMemory(last_output_mode=OutputMode.COOL)
    room._timestamps.record(room._ac_timestamp_key, now - timedelta(seconds=10))
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: now)
    monkeypatch.setattr(
        room._actuators, "async_apply", AsyncMock(return_value=ActuationResult(True))
    )

    await room.async_evaluate()
    room._cancel_retry_timer()

    assert room.decision.output_mode is OutputMode.OFF
    assert room.status == "auto_dead_band"


@pytest.mark.asyncio
async def test_cooling_on_timestamp_starts_after_actuation_ack(hass, monkeypatch) -> None:
    started = utcnow()
    acknowledged = started + timedelta(seconds=10)
    clock = {"now": started}
    set_authoritative_states(hass)
    room = make_room(
        hass,
        enable_safe_cooling_delay=True,
        minimum_seconds_cooling_off=0,
    )
    room._ready = True
    room.mode = VirtualMode.COOL

    async def delayed_apply(*_args) -> ActuationResult:
        clock["now"] = acknowledged
        return ActuationResult(True)

    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: clock["now"])
    monkeypatch.setattr(room._actuators, "async_apply", delayed_apply)

    await room._async_reconcile_once()

    assert room.decision.output_mode is OutputMode.COOL
    assert room._timestamps.elapsed(room._ac_timestamp_key, acknowledged) == 0.0


@pytest.mark.asyncio
async def test_unconfirmed_actuation_and_neutralization_do_not_record_transition(
    hass, monkeypatch
) -> None:
    now = utcnow()
    set_authoritative_states(hass)
    room = make_room(hass)
    room._ready = True
    room.mode = VirtualMode.OFF
    room.decision = ControlDecision(
        OutputMode.COOL, False, False, 22.0, False, False, "previous_cooling"
    )
    room._timestamps.record(room._ac_timestamp_key, now - timedelta(seconds=42))
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: now)
    monkeypatch.setattr(
        room._actuators,
        "async_apply",
        AsyncMock(return_value=ActuationResult(False, "apply_failed")),
    )
    monkeypatch.setattr(
        room._actuators,
        "async_neutralize",
        AsyncMock(return_value=ActuationResult(False, "neutralization_failed")),
    )

    await room._async_reconcile_once()

    assert room.physical_status == "physical_neutralization_failed"
    assert room._timestamps.elapsed(room._ac_timestamp_key, now) == pytest.approx(42.0)


@pytest.mark.parametrize("previous_output", [OutputMode.HEAT, OutputMode.FAN_ONLY])
@pytest.mark.asyncio
async def test_confirmed_neutralization_records_conservative_ac_off_timestamp(
    hass, monkeypatch, previous_output
) -> None:
    now = utcnow()
    set_authoritative_states(hass)
    room = make_room(hass, minimum_seconds_cooling_off=60)
    room._ready = True
    room.mode = VirtualMode.COOL
    room.decision = ControlDecision(
        previous_output, False, False, None, False, False, "previous_output"
    )
    room._timestamps.record(room._ac_timestamp_key, now - timedelta(seconds=120))
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: now)
    monkeypatch.setattr(
        room._actuators,
        "async_apply",
        AsyncMock(return_value=ActuationResult(False, "apply_failed")),
    )
    monkeypatch.setattr(
        room._actuators,
        "async_neutralize",
        AsyncMock(return_value=ActuationResult(True)),
    )

    await room._async_reconcile_once()

    assert room.physical_status == "outputs_neutral_after_failure"
    assert room._timestamps.elapsed(room._ac_timestamp_key, now) == 0.0
