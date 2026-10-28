"""Downstream schema rejection must follow the real fail-closed retry path."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from test_winter_regressions import register_devices, room_config, set_climate

from custom_components.virtual_hvac.control import OutputMode, Preset, VirtualMode
from custom_components.virtual_hvac.models import ControllerConfig
from custom_components.virtual_hvac.runtime import ControllerRuntime


@pytest.mark.parametrize("rejected_service", ["set_hvac_mode", "set_temperature"])
async def test_downstream_value_error_neutralizes_demand_retries_and_recovers(
    hass, freezer, caplog, rejected_service
):
    register_devices(hass)
    set_climate(hass, target=19)
    controller = ControllerRuntime(
        hass,
        "schema-rejection",
        ControllerConfig(name="Schema rejection", shared_heat_source_entity_id=None),
        {"room": room_config(ac_entity_ids=("climate.ac",), boost_ac_heat_assist=True)},
    )
    await controller.async_start()
    await controller.async_finish_startup()
    room = controller.rooms["room"]
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    assert room.heat_demand and controller.aggregate_heat_demand
    assert hass.states.get("switch.heater").state == "on"
    attempts = 0
    reject = True
    calls = []

    async def climate(call):
        nonlocal attempts
        entity_id = call.data["entity_id"]
        old = hass.states.get(entity_id)
        calls.append((call.service, dict(call.data)))
        if call.service == rejected_service and call.data.get("hvac_mode") != "off":
            attempts += 1
            if reject:
                # A downstream integration rejects its own command parameters.
                # For target rejection the preceding AC HEAT has already succeeded.
                raise ValueError("Downstream command does not satisfy parameter schema")
        if call.service == "set_hvac_mode":
            hass.states.async_set(entity_id, call.data["hvac_mode"], old.attributes)
        else:
            hass.states.async_set(
                entity_id, old.state, old.attributes | {"temperature": call.data["temperature"]}
            )

    for service in ("set_hvac_mode", "set_temperature"):
        hass.services.async_register("climate", service, climate)

    def assert_failed_closed():
        assert room.mode is VirtualMode.HEAT
        assert room.preset is Preset.BOOST
        assert room.target_temperature == 22
        assert room.decision.output_mode is OutputMode.OFF
        assert room.decision.ac_target_temperature is None
        assert not room.heat_demand and not controller.aggregate_heat_demand
        assert not room.decision.heater_active
        assert room.status == "ac_heat_assist_not_confirmed"
        assert room.physical_status == "outputs_neutral_after_failure"
        assert room._cancel_timer is not None
        assert not room._lock.locked() and not room._actuating
        assert room._ready and not room._stopping
        assert all(hass.states.get(eid).state == "off" for eid in room.config.output_entity_ids())

    try:
        await room.async_set_preset(Preset.BOOST)
        await hass.async_block_till_done()
        assert attempts == 1
        assert_failed_closed()
        assert room._command_failures == 1
        freezer.tick(timedelta(seconds=4))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 1  # No busy loop / repeated uncaught reconcile tasks.
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 2
        assert room._command_failures == 2
        assert_failed_closed()
        reject = False
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert attempts == 3
        assert room.decision.output_mode is OutputMode.HEAT_ASSIST
        assert room.heat_demand and controller.aggregate_heat_demand
        assert room.physical_status == "outputs_confirmed"
        assert hass.states.get("switch.heater").state == "on"
        assert hass.states.get("climate.ac").state == "heat"
        assert hass.states.get("climate.ac").attributes["temperature"] == 22
        if rejected_service == "set_temperature":
            assert any(data.get("hvac_mode") == "off" for _, data in calls)
        assert room._cancel_timer is None
        assert room._command_failures == 0
        assert not [record for record in caplog.records if record.levelno >= 40]
    finally:
        reject = False
        assert await controller.async_stop()


@pytest.mark.parametrize("neutralization_rejected", [False, True])
async def test_failed_fan_transition_drops_previous_shared_heat_demand(
    hass, freezer, caplog, neutralization_rejected
):
    """A failed FAN command must not leave the preceding HEAT decision published."""
    register_devices(hass)
    set_climate(hass)
    hass.states.async_set("sensor.winter", "22.4", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.rapid", "off")
    controller = ControllerRuntime(
        hass,
        "heat-to-fan",
        ControllerConfig(name="Heat to fan", shared_heat_source_entity_id=None),
        {
            "room": room_config(
                ac_entity_ids=("climate.ac",),
                heater_entity_ids=(),
                shared_heat_request=True,
                rapid_entity_id="switch.rapid",
            )
        },
    )
    await controller.async_start()
    await controller.async_finish_startup()
    room = controller.rooms["room"]
    await room.async_set_target_temperature(24)
    await room.async_set_preset(Preset.BOOST)
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    assert room.decision.output_mode is OutputMode.HEAT
    assert room.heat_demand and controller.aggregate_heat_demand
    assert hass.states.get("switch.rapid").state == "off"
    reject = True
    rejected_prior_heat = []

    async def climate(call):
        old = hass.states.get("climate.ac")
        if call.data["hvac_mode"] == "fan_only" and reject:
            rejected_prior_heat.append(room.decision.output_mode is OutputMode.HEAT)
            # Model a stale downstream flag separately from the newly restricted preset policy.
            hass.states.async_set("switch.rapid", "on")
            raise ValueError("Downstream FAN command has invalid parameters")
        hass.states.async_set("climate.ac", call.data["hvac_mode"], old.attributes)

    async def off(call):
        if neutralization_rejected and reject:
            raise ValueError("Downstream optional output OFF rejected")
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("climate", "set_hvac_mode", climate)
    hass.services.async_register("switch", "turn_off", off)
    try:
        # The next real evaluate observes FAN, fresh 22.4 C and the old HEAT decision.
        await room.async_set_temperature_intent(22, VirtualMode.FAN_ONLY)
        await hass.async_block_till_done()
        assert rejected_prior_heat == [True]
        assert room.mode is VirtualMode.FAN_ONLY
        assert room.target_temperature == 22
        assert room.current_temperature == 22.4
        assert room.preset is Preset.BOOST
        assert room.decision.output_mode is OutputMode.OFF
        assert not room.decision.heat_demand
        assert not room.heat_demand and not controller.aggregate_heat_demand
        assert room.status == "ac_stop_or_start_not_confirmed"
        assert room.physical_status == (
            "physical_neutralization_failed"
            if neutralization_rejected
            else "outputs_neutral_after_failure"
        )
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("switch.rapid").state == ("on" if neutralization_rejected else "off")
        assert room._cancel_timer is not None
        assert room._command_failures == 1
        reject = False
        freezer.tick(timedelta(seconds=6))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert room.decision.output_mode is OutputMode.FAN_ONLY
        assert room.physical_status == "outputs_confirmed"
        assert hass.states.get("climate.ac").state == "fan_only"
        assert hass.states.get("switch.rapid").state == "off"
        assert not room.heat_demand and not controller.aggregate_heat_demand
        assert room._cancel_timer is None
        assert room._command_failures == 0
        assert not [record for record in caplog.records if record.levelno >= 40]
    finally:
        reject = False
        assert await controller.async_stop()
