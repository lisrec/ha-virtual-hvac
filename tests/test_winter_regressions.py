"""Fault-sequence regressions using acknowledged state-only HA devices."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.dt import utcnow
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.virtual_hvac import async_unload_entry
from custom_components.virtual_hvac.actuators import ActuatorAdapter
from custom_components.virtual_hvac.control import Preset, VirtualMode
from custom_components.virtual_hvac.models import ControllerConfig, RoomConfig
from custom_components.virtual_hvac.runtime import ControllerRuntime, RoomRuntime


def room_config(**overrides) -> RoomConfig:
    return RoomConfig(
        **(
            {
                "name": "Winter room",
                "temperature_sensor_entity_ids": ("sensor.winter",),
                "heater_entity_ids": ("switch.heater",),
                "mode_reversal_guard_seconds": 0,
            }
            | overrides
        )
    )


def register_devices(hass):
    calls = []

    async def switch(call):
        calls.append((call.data["entity_id"], call.service))
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    async def climate(call):
        entity_id = call.data["entity_id"]
        calls.append((entity_id, dict(call.data)))
        old = hass.states.get(entity_id)
        assert old is not None
        if call.service == "set_hvac_mode":
            hass.states.async_set(entity_id, call.data["hvac_mode"], old.attributes)
        else:
            hass.states.async_set(
                entity_id, old.state, old.attributes | {"temperature": call.data["temperature"]}
            )

    for service in ("turn_on", "turn_off"):
        hass.services.async_register("switch", service, switch)
    for service in ("set_hvac_mode", "set_temperature"):
        hass.services.async_register("climate", service, climate)
    hass.states.async_set("sensor.winter", "20", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.heater", "off")
    return calls


def set_climate(hass, entity_id="climate.ac", *, target=22):
    hass.states.async_set(
        entity_id,
        "off",
        {
            "hvac_modes": ["off", "heat", "cool", "dry", "fan_only"],
            "temperature": target,
            "min_temp": 5,
            "max_temp": 35,
            "target_temp_step": 0.5,
        },
    )


async def start_room(hass, config):
    room = RoomRuntime(hass, "winter", config, Mock())
    await room.async_start()
    assert await room.async_finish_startup()
    await hass.async_block_till_done()
    return room


@pytest.mark.asyncio
async def test_native_schedule_target_drift_remains_outside_exclusive_writer_contract(hass):
    """Document existing scope, not a promise to arbitrate another writer's targets."""
    calls = register_devices(hass)
    set_climate(hass, "climate.trv", target=23)
    room = await start_room(hass, room_config(heater_entity_ids=("climate.trv",)))
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        calls.clear()
        old = hass.states.get("climate.trv")
        hass.states.async_set("climate.trv", "heat", old.attributes | {"temperature": 7})
        hass.states.async_set("sensor.winter", "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert hass.states.get("climate.trv").attributes["temperature"] == 7
        assert calls == []
        assert room.heat_demand
    finally:
        await room.async_stop()


@pytest.mark.asyncio
async def test_reversal_guard_is_elapsed_path_dwell_not_post_off_deadtime(hass, freezer):
    calls = register_devices(hass)
    set_climate(hass)
    room = await start_room(
        hass,
        room_config(
            ac_entity_ids=("climate.ac",),
            heater_entity_ids=(),
            mode_reversal_guard_seconds=300,
            temperature_sensor_max_age_seconds=None,
        ),
    )
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        freezer.tick(timedelta(seconds=600))
        calls.clear()
        await room.async_set_mode(VirtualMode.DRY)
        await hass.async_block_till_done()
        assert hass.states.get("climate.ac").state == "dry"
        assert calls == [("climate.ac", {"entity_id": "climate.ac", "hvac_mode": "dry"})]
    finally:
        await room.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_entity", ["switch.heater", "switch.shared"])
async def test_command_retry_backoff_is_capped_without_busy_loop(hass, freezer, failed_entity):
    register_devices(hass)
    hass.states.async_set("switch.shared", "off")
    runtime = ControllerRuntime(
        hass,
        "winter",
        ControllerConfig(name="Winter", shared_heat_source_entity_id="switch.shared"),
        {"room": room_config(temperature_sensor_max_age_seconds=None)},
    )
    await runtime.async_start()
    await runtime.async_finish_startup()
    attempts = 0

    async def turn_on(call):
        nonlocal attempts
        if call.data["entity_id"] == failed_entity:
            attempts += 1
            raise HomeAssistantError("persistent transport failure")
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("switch", "turn_on", turn_on)
    try:
        await runtime.rooms["room"].async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert attempts == 1
        for count, delay in enumerate((5, 10, 20, 40, 60, 60), start=1):
            freezer.tick(timedelta(seconds=delay - 1))
            async_fire_time_changed(hass, utcnow())
            await hass.async_block_till_done()
            assert attempts == count
            freezer.tick(timedelta(seconds=2))
            async_fire_time_changed(hass, utcnow())
            await hass.async_block_till_done()
            assert attempts == count + 1
        assert hass.states.get(failed_entity).state == "off"
    finally:
        await runtime.async_stop()


@pytest.mark.asyncio
async def test_stale_neutralization_failure_retries_without_rearming(hass, freezer):
    register_devices(hass)
    set_climate(hass, "climate.trv", target=10)
    room = await start_room(
        hass,
        room_config(heater_entity_ids=("climate.trv",), temperature_sensor_max_age_seconds=None),
    )
    entered, release = asyncio.Event(), asyncio.Event()
    off_attempts = 0

    async def target(call):
        entered.set()
        await release.wait()
        old = hass.states.get("climate.trv")
        hass.states.async_set(
            "climate.trv", old.state, old.attributes | {"temperature": call.data["temperature"]}
        )

    async def mode(call):
        nonlocal off_attempts
        desired = call.data["hvac_mode"]
        if desired == "off":
            off_attempts += 1
            if off_attempts == 1:
                raise HomeAssistantError("transient stale neutralization failure")
        old = hass.states.get("climate.trv")
        hass.states.async_set("climate.trv", desired, old.attributes)

    hass.services.async_register("climate", "set_temperature", target)
    hass.services.async_register("climate", "set_hvac_mode", mode)
    try:
        first = asyncio.create_task(room.async_set_mode(VirtualMode.HEAT))
        await entered.wait()
        second = asyncio.create_task(room.async_set_preset(Preset.SLEEP))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        await hass.async_block_till_done()
        assert not room._ready
        assert not room.heat_demand
        assert hass.states.get("climate.trv").state == "heat"
        freezer.tick(timedelta(seconds=6))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert hass.states.get("climate.trv").state == "off"
        assert off_attempts == 2
        hass.states.async_set("sensor.winter", "19")
        await hass.async_block_till_done()
        assert not room._ready
        assert not room.heat_demand
        assert hass.states.get("climate.trv").state == "off"
    finally:
        release.set()
        await room.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("entity_id", ["switch.heater", "switch.rapid"])
@pytest.mark.parametrize("state", ["unknown", "unavailable", "unexpected"])
async def test_off_status_never_treats_indeterminate_outputs_as_off(hass, entity_id, state):
    register_devices(hass)
    hass.states.async_set("switch.rapid", "off")
    room = await start_room(hass, room_config(rapid_entity_id="switch.rapid"))
    try:
        hass.states.async_set(entity_id, state)
        await hass.async_block_till_done()
        if state == "unexpected":
            # A known but invalid switch state may be corrected by an acknowledged OFF.
            assert hass.states.get(entity_id).state == "off"
        else:
            assert room.physical_status != "outputs_confirmed"
        assert not room.heat_demand
    finally:
        await room.async_stop()
        room._cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [VirtualMode.HEAT, VirtualMode.COOL])
@pytest.mark.parametrize(
    "target,expected_heat,expected_cool", [(22, 73, 72), (35, 95, 95), (5, 45, 45)]
)
async def test_fahrenheit_outputs_convert_before_grid_and_clamp(
    hass, mode, target, expected_heat, expected_cool
):
    hass.config.units = US_CUSTOMARY_SYSTEM
    calls = register_devices(hass)
    hass.states.async_set(
        "climate.output",
        "off",
        {
            "hvac_modes": ["off", "heat", "cool"],
            "temperature": 68,
            "min_temp": 45,
            "max_temp": 95,
            "target_temp_step": 1,
        },
    )
    hass.states.async_set(
        "sensor.winter", "0" if mode is VirtualMode.HEAT else "40", {"unit_of_measurement": "°C"}
    )
    room = await start_room(
        hass,
        room_config(
            heater_entity_ids=("climate.output",) if mode is VirtualMode.HEAT else (),
            ac_entity_ids=("climate.output",) if mode is VirtualMode.COOL else (),
            minimum_seconds_cooling_off=0,
        ),
    )
    try:
        await room.async_set_target_temperature(target)
        await room.async_set_mode(mode)
        await hass.async_block_till_done()
        expected = expected_heat if mode is VirtualMode.HEAT else expected_cool
        assert hass.states.get("climate.output").attributes["temperature"] == expected
        assert any(
            isinstance(data, dict) and data.get("temperature") == expected for _, data in calls
        )
        assert room.target_temperature == target
        assert room.physical_status == "outputs_confirmed"
    finally:
        await room.async_stop()


@pytest.mark.asyncio
async def test_platform_unload_refusal_retains_all_off_only_owners(hass, monkeypatch):
    register_devices(hass)
    hass.states.async_set("switch.shared", "off")
    hass.states.async_set("switch.second", "off")
    runtime = ControllerRuntime(
        hass,
        "winter",
        ControllerConfig(name="Winter", shared_heat_source_entity_id="switch.shared"),
        {
            "first": room_config(),
            "second": room_config(heater_entity_ids=("switch.second",)),
        },
    )
    await runtime.async_start()
    await runtime.async_finish_startup()
    for room in runtime.rooms.values():
        await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    entry = SimpleNamespace(runtime_data=runtime)
    unload = AsyncMock(return_value=False)
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload)
    try:
        assert not await async_unload_entry(hass, entry)
        assert entry.runtime_data is runtime
        for entity_id in ("switch.heater", "switch.second", "switch.shared"):
            hass.states.async_set(entity_id, "on")
        hass.states.async_set("sensor.winter", "19")
        await hass.async_block_till_done()
        for entity_id in ("switch.heater", "switch.second", "switch.shared"):
            assert hass.states.get(entity_id).state == "off"
        assert runtime._remove_shared_listener is not None
        for room in runtime.rooms.values():
            assert room._remove_state_listener is not None
            assert not room._ready
            assert room.mode is VirtualMode.HEAT
            assert not room.heat_demand
        unload.return_value = True
        assert await async_unload_entry(hass, entry)
        assert entry.runtime_data is None
        assert runtime._remove_shared_listener is None
        assert all(room._remove_state_listener is None for room in runtime.rooms.values())
    finally:
        await runtime.async_abort_startup()


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_entity", ["switch.heater", "switch.shared"])
async def test_refused_shutdown_retries_only_off_and_never_rearms(hass, freezer, failed_entity):
    register_devices(hass)
    hass.states.async_set("switch.shared", "off")
    runtime = ControllerRuntime(
        hass,
        "winter",
        ControllerConfig(name="Winter", shared_heat_source_entity_id="switch.shared"),
        {"room": room_config(temperature_sensor_max_age_seconds=None)},
    )
    await runtime.async_start()
    await runtime.async_finish_startup()
    room = runtime.rooms["room"]
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    attempts = 0

    async def turn_off(call):
        nonlocal attempts
        if call.data["entity_id"] == failed_entity:
            attempts += 1
            if attempts <= 2:
                raise HomeAssistantError("transient shutdown failure")
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("switch", "turn_off", turn_off)
    try:
        assert not await runtime.async_stop()
        assert attempts == 1
        assert not room.heat_demand
        assert not runtime.aggregate_heat_demand
        freezer.tick(timedelta(seconds=6))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 2
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 3
        assert hass.states.get(failed_entity).state == "off"
        hass.states.async_set("sensor.winter", "19")
        await hass.async_block_till_done()
        assert room.mode is VirtualMode.HEAT
        assert not room._ready
        assert not room.heat_demand
        assert hass.states.get("switch.heater").state == "off"
        assert hass.states.get("switch.shared").state == "off"
    finally:
        await runtime.async_abort_startup()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_shared_command_failure_retries_without_new_source_events(hass, freezer, enabled):
    register_devices(hass)
    hass.states.async_set("switch.shared", "off")
    runtime = ControllerRuntime(
        hass,
        "winter",
        ControllerConfig(name="Winter", shared_heat_source_entity_id="switch.shared"),
        {"room": room_config(temperature_sensor_max_age_seconds=None)},
    )
    await runtime.async_start()
    await runtime.async_finish_startup()
    room = runtime.rooms["room"]
    if not enabled:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert hass.states.get("switch.shared").state == "on"
    attempts = 0

    async def command(call):
        nonlocal attempts
        if call.data["entity_id"] == "switch.shared":
            attempts += 1
            if attempts <= 2:
                raise HomeAssistantError("transient relay failure")
        hass.states.async_set(call.data["entity_id"], "on" if enabled else "off")

    hass.services.async_register("switch", "turn_on" if enabled else "turn_off", command)
    try:
        if enabled:
            await room.async_set_mode(VirtualMode.HEAT)
        else:
            hass.states.async_set("sensor.winter", "unavailable")
        await hass.async_block_till_done()
        assert attempts == 1
        freezer.tick(timedelta(seconds=6))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 2
        freezer.tick(timedelta(seconds=9))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 2
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 3
        assert hass.states.get("switch.shared").state == ("on" if enabled else "off")
        assert runtime.aggregate_heat_demand is enabled
    finally:
        await runtime.async_stop()


@pytest.mark.asyncio
async def test_failed_heater_start_retries_with_backoff_without_sensor_changes(hass, freezer):
    register_devices(hass)
    room = await start_room(hass, room_config(temperature_sensor_max_age_seconds=None))
    attempts = 0

    async def turn_on(call):
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise HomeAssistantError("transient transport failure")
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("switch", "turn_on", turn_on)
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert attempts == 1
        assert not room.heat_demand
        freezer.tick(timedelta(seconds=4))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 1
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 2
        assert not room.heat_demand
        freezer.tick(timedelta(seconds=9))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 2
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 3
        assert room.heat_demand
        assert hass.states.get("switch.heater").state == "on"
    finally:
        await room.async_stop()


@pytest.mark.asyncio
async def test_coalesced_target_preset_preserves_acknowledged_minimum_off(hass, freezer):
    calls = register_devices(hass)
    set_climate(hass)
    hass.states.async_set("sensor.winter", "28", {"unit_of_measurement": "°C"})
    room = await start_room(
        hass,
        room_config(
            heater_entity_ids=(),
            ac_entity_ids=("climate.ac",),
            enable_safe_cooling_delay=True,
            minimum_seconds_cooling_off=60,
            minimum_seconds_cooling_on=300,
            temperature_sensor_max_age_seconds=None,
        ),
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def target(call):
        entered.set()
        await release.wait()
        old = hass.states.get("climate.ac")
        hass.states.async_set(
            "climate.ac", old.state, old.attributes | {"temperature": call.data["temperature"]}
        )

    try:
        freezer.tick(timedelta(seconds=61))
        await room.async_set_mode(VirtualMode.COOL)
        assert hass.states.get("climate.ac").state == "cool"
        freezer.tick(timedelta(seconds=120))
        hass.services.async_register("climate", "set_temperature", target)
        calls.clear()
        first = asyncio.create_task(room.async_set_target_temperature(21))
        await entered.wait()
        second = asyncio.create_task(room.async_set_preset(Preset.SLEEP))
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        await hass.async_block_till_done()
        assert hass.states.get("climate.ac").state == "off"
        assert room.status == "ac_minimum_off"
        assert room._timestamps.elapsed(room._ac_timestamp_key, utcnow()) == 0
        assert room._timestamps.elapsed(room._output_timestamp_key, utcnow()) == 0
        freezer.tick(timedelta(seconds=59))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert hass.states.get("climate.ac").state == "off"
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert hass.states.get("climate.ac").state == "cool"
    finally:
        release.set()
        await room.async_stop()


@pytest.mark.asyncio
async def test_neutralization_rechecks_complete_off_bank(hass):
    register_devices(hass)
    hass.states.async_set("switch.heater", "on")
    hass.states.async_set("switch.second", "on")

    async def turn_off(call):
        if call.data["entity_id"] == "switch.second":
            hass.states.async_set("switch.heater", "on")
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("switch", "turn_off", turn_off)
    result = await ActuatorAdapter(
        hass, room_config(heater_entity_ids=("switch.heater", "switch.second"))
    ).async_neutralize(22)
    assert not result.success
    assert hass.states.get("switch.second").state == "off"


@pytest.mark.asyncio
async def test_bank_final_ack_rechecks_prior_target(hass):
    register_devices(hass)
    set_climate(hass, "climate.trv", target=10)
    hass.states.async_set("switch.second", "off")
    room = await start_room(hass, room_config(heater_entity_ids=("climate.trv", "switch.second")))

    async def turn_on(call):
        old = hass.states.get("climate.trv")
        hass.states.async_set("climate.trv", "heat", old.attributes | {"temperature": 7})
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("switch", "turn_on", turn_on)
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert hass.states.get("climate.trv").state == "off"
        assert hass.states.get("switch.second").state == "off"
    finally:
        await room.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["off", "unavailable", "unknown"])
async def test_bank_final_ack_rechecks_prior_member(hass, drift):
    register_devices(hass)
    hass.states.async_set("switch.second", "off")
    room = await start_room(hass, room_config(heater_entity_ids=("switch.heater", "switch.second")))
    attempts = 0

    async def turn_on(call):
        nonlocal attempts
        attempts += 1
        if call.data["entity_id"] == "switch.second":
            hass.states.async_set("switch.heater", drift)
            await asyncio.sleep(0)
        hass.states.async_set(call.data["entity_id"], "on")

    hass.services.async_register("switch", "turn_on", turn_on)
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert room.physical_status != "outputs_confirmed"
        assert hass.states.get("switch.second").state == "off"
        assert attempts == 2  # Own ACKs must not invalidate/reapply forever.
    finally:
        await room.async_stop()
        room._cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_mode", ["unavailable", "unknown", "off", "cool"])
async def test_target_ack_requires_available_expected_mode(hass, monkeypatch, ack_mode):
    register_devices(hass)
    set_climate(hass, "climate.trv", target=10)
    monkeypatch.setattr("custom_components.virtual_hvac.actuators.COMMAND_ACK_TIMEOUT", 0.01)
    room = await start_room(hass, room_config(heater_entity_ids=("climate.trv",)))

    async def target(call):
        old = hass.states.get("climate.trv")
        hass.states.async_set(
            "climate.trv", ack_mode, old.attributes | {"temperature": call.data["temperature"]}
        )

    hass.services.async_register("climate", "set_temperature", target)
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert room.physical_status != "outputs_confirmed"
    finally:
        await room.async_stop()
        room._cleanup()


@pytest.mark.asyncio
async def test_known_off_recovery_resumes_heat_with_unchanged_fresh_sensors(hass):
    register_devices(hass)
    room = await start_room(hass, room_config())
    try:
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        hass.states.async_set("switch.heater", "unavailable")
        await hass.async_block_till_done()
        assert not room.heat_demand
        hass.states.async_set("switch.heater", "off")
        await hass.async_block_till_done()
        hass.states.async_set("sensor.winter", "20", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert hass.states.get("switch.heater").state == "on"
        assert room.heat_demand
        assert room.physical_status == "outputs_confirmed"
    finally:
        await room.async_stop()
