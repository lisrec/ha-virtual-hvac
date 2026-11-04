"""Shared-CO BOOST uses acknowledged AC heat without inventing a heater ACK."""

from datetime import timedelta

import pytest
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from test_winter_regressions import register_devices, room_config, set_climate, start_room

from custom_components.virtual_hvac.control import OutputMode, Preset, VirtualMode
from custom_components.virtual_hvac.models import ControllerConfig
from custom_components.virtual_hvac.runtime import ControllerRuntime


@pytest.mark.asyncio
async def test_shared_boost_runs_ac_heat_at_normal_target_without_local_heater(hass):
    calls = register_devices(hass)
    set_climate(hass, target=19)
    hass.states.async_set("switch.rapid", "off")
    room = await start_room(
        hass,
        room_config(
            shared_heat_request=True,
            heater_entity_ids=(),
            ac_entity_ids=("climate.ac",),
            rapid_entity_id="switch.rapid",
        ),
    )
    try:
        await room.async_set_target_temperature(24)
        await room.async_set_preset(Preset.BOOST)
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert room.decision.output_mode is OutputMode.HEAT_ASSIST
        assert room.heat_demand
        assert not room.decision.heater_active
        assert room.decision.ac_target_temperature == 24
        assert room.decision.rapid
        assert hass.states.get("climate.ac").state == "heat"
        assert hass.states.get("climate.ac").attributes["temperature"] == 24
        assert hass.states.get("switch.rapid").state == "on"
        assert hass.states.get("switch.heater").state == "off"
        assert not any(entity_id == "switch.heater" for entity_id, _ in calls)
        assert room._decision_uses_ac(room.decision)
        assert room.physical_status == "outputs_confirmed"
        calls.clear()
        for _ in range(3):
            await room.async_evaluate()
            await hass.async_block_till_done()
        assert calls == []
        await room.async_set_target_temperature(25)
        await hass.async_block_till_done()
        assert hass.states.get("climate.ac").attributes["temperature"] == 25
        assert not any(
            isinstance(data, dict) and data.get("hvac_mode") == "off" for _, data in calls
        )
    finally:
        assert await room.async_stop()


async def start_shared_controller(hass, **overrides):
    runtime = ControllerRuntime(
        hass,
        "shared_boost",
        ControllerConfig(name="Shared BOOST", shared_heat_source_entity_id=None),
        {
            "room": room_config(
                **(
                    {
                        "shared_heat_request": True,
                        "heater_entity_ids": (),
                        "ac_entity_ids": ("climate.ac",),
                        "rapid_entity_id": "switch.rapid",
                        "silent_entity_id": "switch.silent",
                    }
                    | overrides
                )
            )
        },
    )
    await runtime.async_start()
    await runtime.async_finish_startup()
    await hass.async_block_till_done()
    return runtime, runtime.rooms["room"]


def shared_devices(hass):
    calls = register_devices(hass)
    set_climate(hass, target=19)
    for entity_id in ("switch.rapid", "switch.silent"):
        hass.states.async_set(entity_id, "off")
    return calls


async def activate_boost(hass, room, mode=VirtualMode.HEAT):
    await room.async_set_target_temperature(24)
    await room.async_set_preset(Preset.BOOST)
    await room.async_set_mode(mode)
    await hass.async_block_till_done()
    assert room.decision.output_mode is OutputMode.HEAT_ASSIST
    assert room.heat_demand
    assert not room.decision.heater_active
    assert hass.states.get("climate.ac").state == "heat"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [VirtualMode.HEAT, VirtualMode.AUTO])
@pytest.mark.parametrize("preset", [Preset.COMFORT, Preset.SLEEP])
async def test_shared_boost_to_nonboost_keeps_co_without_ac(hass, mode, preset):
    shared_devices(hass)
    runtime, room = await start_shared_controller(hass)
    try:
        await activate_boost(hass, room, mode)
        assert runtime.aggregate_heat_demand
        await room.async_set_preset(preset)
        await hass.async_block_till_done()
        assert room.decision.output_mode is OutputMode.HEAT
        assert room.heat_demand
        assert runtime.aggregate_heat_demand
        assert not room.decision.heater_active
        assert room.target_temperature == 24
        assert not room.decision.rapid
        assert not room.decision.silent
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("switch.rapid").state == "off"
        assert hass.states.get("switch.silent").state == "off"
        assert room.physical_status == "outputs_confirmed"
    finally:
        assert await runtime.async_stop()
    assert not room.heat_demand
    assert not runtime.aggregate_heat_demand


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stop",
    [
        "target",
        "window",
        "window_unknown",
        "window_unavailable",
        "source_unknown",
        "source_unavailable",
        "expiry",
        "off",
        "unload",
    ],
)
async def test_shared_boost_safety_withdraws_ac_and_aggregate_demand(hass, freezer, stop):
    shared_devices(hass)
    hass.states.async_set("binary_sensor.window", "off")
    runtime, room = await start_shared_controller(
        hass, window_entity_ids=("binary_sensor.window",), window_open_delay_minutes=0
    )
    try:
        await activate_boost(hass, room)
        assert runtime.aggregate_heat_demand
        if stop == "target":
            hass.states.async_set("sensor.winter", "24", {"unit_of_measurement": "°C"})
        elif stop.startswith("window"):
            state = {
                "window": "on",
                "window_unknown": "unknown",
                "window_unavailable": "unavailable",
            }[stop]
            hass.states.async_set("binary_sensor.window", state)
        elif stop.startswith("source"):
            hass.states.async_set("sensor.winter", stop.removeprefix("source_"))
        elif stop == "expiry":
            freezer.tick(timedelta(seconds=299))
            async_fire_time_changed(hass, utcnow())
            await hass.async_block_till_done()
            assert room.heat_demand
            assert hass.states.get("climate.ac").state == "heat"
            freezer.tick(timedelta(seconds=2))
            async_fire_time_changed(hass, utcnow())
        elif stop == "off":
            await room.async_set_mode(VirtualMode.OFF)
        else:
            assert await runtime.async_stop()
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert not runtime.aggregate_heat_demand
        assert not room.decision.heater_active
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("switch.rapid").state == "off"
        assert hass.states.get("switch.silent").state == "off"
    finally:
        assert await runtime.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", ["disabled", "no_ac", "unsupported", "one_unsupported"])
async def test_shared_boost_falls_back_to_co_only_without_usable_assist(hass, fallback):
    calls = shared_devices(hass)
    overrides = {}
    if fallback == "disabled":
        overrides["boost_ac_heat_assist"] = False
    elif fallback == "no_ac":
        overrides["ac_entity_ids"] = ()
    elif fallback == "unsupported":
        old = hass.states.get("climate.ac")
        hass.states.async_set("climate.ac", "off", old.attributes | {"hvac_modes": ["off", "cool"]})
    else:
        set_climate(hass, "climate.second")
        old = hass.states.get("climate.second")
        hass.states.async_set(
            "climate.second", "off", old.attributes | {"hvac_modes": ["off", "cool"]}
        )
        overrides["ac_entity_ids"] = ("climate.ac", "climate.second")
    runtime, room = await start_shared_controller(hass, **overrides)
    try:
        await room.async_set_target_temperature(24)
        await room.async_set_preset(Preset.BOOST)
        await room.async_set_mode(VirtualMode.HEAT)
        await hass.async_block_till_done()
        assert room.decision.output_mode is OutputMode.HEAT
        assert room.heat_demand
        assert runtime.aggregate_heat_demand
        assert not room.decision.heater_active
        assert room.decision.ac_target_temperature is None
        assert not room.decision.rapid
        assert not room.decision.silent
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("switch.rapid").state == "off"
        assert not any(
            isinstance(data, dict) and data.get("hvac_mode") == "heat" for _, data in calls
        )
    finally:
        assert await runtime.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault", ["mode_ack", "target_ack", "final_bank", "ac_unknown", "ac_unavailable", "ac_missing"]
)
async def test_shared_boost_unconfirmed_ac_withdraws_demand(hass, monkeypatch, fault):
    shared_devices(hass)
    monkeypatch.setattr("custom_components.virtual_hvac.actuators.COMMAND_ACK_TIMEOUT", 0.01)
    runtime, room = await start_shared_controller(hass)
    try:
        # First acknowledge shared CO so a later failed assist must withdraw it.
        await room.async_set_target_temperature(24)
        await room.async_set_mode(VirtualMode.HEAT)
        assert room.heat_demand
        assert runtime.aggregate_heat_demand

        async def mode(call):
            old = hass.states.get(call.data["entity_id"])
            desired = call.data["hvac_mode"]
            if desired == "heat" and fault == "mode_ack":
                return
            hass.states.async_set(old.entity_id, desired, old.attributes)

        async def target(call):
            if fault == "target_ack":
                return
            old = hass.states.get(call.data["entity_id"])
            hass.states.async_set(
                old.entity_id, old.state, old.attributes | {"temperature": call.data["temperature"]}
            )

        async def rapid(call):
            hass.states.async_set(call.data["entity_id"], "on")
            if fault == "final_bank":
                old = hass.states.get("climate.ac")
                hass.states.async_set("climate.ac", "off", old.attributes)

        hass.services.async_register("climate", "set_hvac_mode", mode)
        hass.services.async_register("climate", "set_temperature", target)
        hass.services.async_register("switch", "turn_on", rapid)
        if fault.startswith("ac_"):
            old = hass.states.get("climate.ac")
            if fault == "ac_missing":
                hass.states.async_remove("climate.ac")
            else:
                hass.states.async_set("climate.ac", fault.removeprefix("ac_"), old.attributes)
            await hass.async_block_till_done()
        await room.async_set_preset(Preset.BOOST)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert not runtime.aggregate_heat_demand
        assert room.decision.output_mode is OutputMode.OFF
        assert not room.decision.heater_active
        assert room.physical_status != "outputs_confirmed"
        assert hass.states.get("switch.rapid").state == "off"
        if not fault.startswith("ac_"):
            assert hass.states.get("climate.ac").state == "off"
    finally:
        # Restore authoritative telemetry only after disarming the room.
        if fault.startswith("ac_"):
            await runtime.async_stop()
            set_climate(hass)
            await hass.async_block_till_done()
        assert await runtime.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [VirtualMode.HEAT, VirtualMode.AUTO])
async def test_shared_boost_retains_hysteresis_and_never_raises_target(hass, mode):
    shared_devices(hass)
    hass.states.async_set("sensor.winter", "23", {"unit_of_measurement": "°C"})
    runtime, room = await start_shared_controller(
        hass, heating_hysteresis_on=1, heating_hysteresis_off=0.4
    )
    try:
        await room.async_set_target_temperature(24)
        await room.async_set_preset(Preset.BOOST)
        await room.async_set_mode(mode)
        await hass.async_block_till_done()
        # HEAT is inclusive at the start boundary; AUTO remains strict.
        assert room.heat_demand is (mode is VirtualMode.HEAT)
        if mode is VirtualMode.AUTO:
            assert hass.states.get("switch.rapid").state == "off"
        hass.states.async_set("sensor.winter", "22.9", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert room.heat_demand
        assert room.decision.output_mode is OutputMode.HEAT_ASSIST
        for temperature in (23.5, 24.3):
            hass.states.async_set("sensor.winter", str(temperature), {"unit_of_measurement": "°C"})
            await hass.async_block_till_done()
            assert room.heat_demand
            assert hass.states.get("climate.ac").attributes["temperature"] == 24
            assert room.target_temperature == 24
        hass.states.async_set("sensor.winter", "24.4", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert not runtime.aggregate_heat_demand
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("switch.rapid").state == "off"
        hass.states.async_set("sensor.winter", "23.5", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert hass.states.get("climate.ac").state == "off"
    finally:
        assert await runtime.async_stop()


@pytest.mark.asyncio
async def test_shared_boost_live_heat_capability_loss_stops_ac_but_keeps_co(hass):
    shared_devices(hass)
    runtime, room = await start_shared_controller(hass)
    try:
        await activate_boost(hass, room)
        old = hass.states.get("climate.ac")
        hass.states.async_set(
            "climate.ac", "heat", old.attributes | {"hvac_modes": ["off", "cool"]}
        )
        await hass.async_block_till_done()
        await room.async_evaluate()
        assert room.decision.output_mode is OutputMode.HEAT
        assert room.heat_demand
        assert runtime.aggregate_heat_demand
        assert not room.decision.heater_active
        assert not room.decision.rapid
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("switch.rapid").state == "off"
    finally:
        assert await runtime.async_stop()
