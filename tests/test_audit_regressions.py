"""End-to-end regressions for the independent safety audit."""

import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.virtual_hvac.actuators import ActuatorAdapter
from custom_components.virtual_hvac.control import VirtualMode
from custom_components.virtual_hvac.models import ControllerConfig, RoomConfig
from custom_components.virtual_hvac.runtime import ControllerRuntime, RoomRuntime


def config(**overrides):
    values = {
        "name": "Audit",
        "temperature_sensor_entity_ids": ("sensor.audit_temp",),
        "heater_entity_ids": ("switch.audit_heater",),
        "mode_reversal_guard_seconds": 0,
    }
    values.update(overrides)
    return RoomConfig(**values)


def switches(hass):
    async def command(call):
        hass.states.async_set(call.data["entity_id"], "on" if call.service == "turn_on" else "off")

    hass.services.async_register("switch", "turn_on", command)
    hass.services.async_register("switch", "turn_off", command)
    hass.states.async_set("sensor.audit_temp", "20", {"unit_of_measurement": "°C"})
    hass.states.async_set("switch.audit_heater", "off")


@pytest.mark.asyncio
async def test_temperature_expiry_stops_heat_and_aggregate_without_source_event(hass, monkeypatch):
    switches(hass)
    hass.states.async_set("switch.shared", "off")
    controller = ControllerRuntime(
        hass,
        "audit",
        ControllerConfig(name="Audit", shared_heat_source_entity_id="switch.shared"),
        {"room": config()},
    )
    room = controller.rooms["room"]
    await room.async_start()
    assert await room.async_finish_startup()
    controller._startup_complete = True
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    assert room.heat_demand and controller.aggregate_heat_demand
    assert hass.states.get("switch.shared").state == "on"
    future = utcnow() + timedelta(seconds=301)
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: future)
    async_fire_time_changed(hass, future)
    await hass.async_block_till_done()
    assert room.current_temperature is None
    assert not room.heat_demand
    assert not controller.aggregate_heat_demand
    assert hass.states.get("switch.audit_heater").state == "off"
    assert room.status == "no_valid_temperature"
    assert hass.states.get("switch.shared").state == "off"
    await controller.async_stop()


@pytest.mark.asyncio
async def test_expired_temperature_withdraws_aggregate_before_off_acknowledgement(
    hass, monkeypatch
):
    switches(hass)
    controller = ControllerRuntime(
        hass, "audit", ControllerConfig(name="Audit"), {"room": config()}
    )
    room = controller.rooms["room"]
    assert await room.async_finish_startup()
    controller._startup_complete = True
    await room.async_set_mode(VirtualMode.HEAT)
    future = utcnow() + timedelta(seconds=301)
    monkeypatch.setattr("custom_components.virtual_hvac.runtime.utcnow", lambda: future)
    # Even before the scheduled writer can stop the hardware, stale input is not demand.
    assert not room.heat_demand
    assert not controller.aggregate_heat_demand
    await controller.async_stop()


@pytest.mark.asyncio
async def test_unchanged_temperature_report_reschedules_only_freshness(hass, freezer):
    switches(hass)
    room = RoomRuntime(hass, "audit", config(), Mock())
    await room.async_start()
    assert await room.async_finish_startup()
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    before = room._cancel_freshness_timer
    room._cancel_timer = protection_cancel = Mock()
    freezer.tick(timedelta(seconds=200))
    hass.states.async_set("sensor.audit_temp", "20", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert room._cancel_freshness_timer is not before
    protection_cancel.assert_not_called()
    assert room._cancel_timer is protection_cancel
    freezer.tick(timedelta(seconds=101))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert room.heat_demand
    freezer.tick(timedelta(seconds=200))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert not room.heat_demand
    assert hass.states.get("switch.audit_heater").state == "off"
    await room.async_stop()
    assert room._cancel_freshness_timer is None


@pytest.mark.asyncio
async def test_unchanged_report_recovers_after_freshness_shutdown(hass, freezer):
    switches(hass)
    room = RoomRuntime(hass, "audit", config(), Mock())
    await room.async_start()
    assert await room.async_finish_startup()
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=301))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert not room.heat_demand
    hass.states.async_set("sensor.audit_temp", "20", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert room.heat_demand
    assert hass.states.get("switch.audit_heater").state == "on"
    await room.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("bank", ["heater", "trv", "ac"])
@pytest.mark.parametrize("failure", ["unavailable", "service_error"])
async def test_neutralization_attempts_later_bank_members(hass, bank, failure):
    domain = "switch" if bank == "heater" else "climate"
    ids = (f"{domain}.failed", f"{domain}.reachable")
    calls = []

    async def command(call):
        entity_id = call.data["entity_id"]
        calls.append(entity_id)
        if entity_id == ids[0]:
            raise HomeAssistantError("offline")
        old = hass.states.get(entity_id)
        hass.states.async_set(entity_id, "off", old.attributes)

    hass.services.async_register(
        domain, "turn_off" if bank == "heater" else "set_hvac_mode", command
    )
    attrs = {"hvac_modes": ["off", "cool", "heat"]}
    active = "on" if bank == "heater" else "heat" if bank == "trv" else "cool"
    hass.states.async_set(ids[0], "unavailable" if failure == "unavailable" else active, attrs)
    hass.states.async_set(ids[1], active, attrs)
    cfg = config(
        **(
            {"heater_entity_ids": ids}
            if bank != "ac"
            else {"heater_entity_ids": (), "ac_entity_ids": ids}
        )
    )
    result = await ActuatorAdapter(hass, cfg).async_neutralize(22)
    assert not result.success
    assert ids[1] in calls
    assert hass.states.get(ids[1]).state == "off"


@pytest.mark.asyncio
async def test_failed_unload_retains_all_owners_for_disarmed_off_only_recovery(hass):
    switches(hass)
    hass.states.async_set("switch.second", "off")
    hass.states.async_set("switch.shared", "off")
    controller = ControllerRuntime(
        hass,
        "audit",
        ControllerConfig(name="Audit", shared_heat_source_entity_id="switch.shared"),
        {"room": config(), "second": config(heater_entity_ids=("switch.second",))},
    )
    await controller.async_start()
    await controller.async_finish_startup()
    for room in controller.rooms.values():
        await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    assert controller.aggregate_heat_demand
    hass.states.async_set("switch.audit_heater", "unavailable")
    assert not await controller.async_stop()
    assert not controller.aggregate_heat_demand
    for room in controller.rooms.values():
        assert not room._ready
        assert not room.decision.heat_demand
        assert room._remove_state_listener is not None
    assert controller._remove_shared_listener is not None
    # Recovered failed output, drift of a successfully stopped room, and shared drift
    # must all be turned OFF without restoring the retained HEAT intent.
    for entity_id in ("switch.audit_heater", "switch.second", "switch.shared"):
        hass.states.async_set(entity_id, "on")
    controller._async_shared_retry(utcnow())
    for room in controller.rooms.values():
        room._async_retry(utcnow())
    await hass.async_block_till_done()
    for entity_id in ("switch.audit_heater", "switch.second", "switch.shared"):
        assert hass.states.get(entity_id).state == "off"
    for room in controller.rooms.values():
        assert not room._ready
        assert room.mode is VirtualMode.HEAT
        assert not room.heat_demand
    assert not controller._startup_complete
    assert await controller.async_stop()
    for room in controller.rooms.values():
        assert room._remove_state_listener is None
        assert room._remove_report_listener is None
    assert controller._remove_shared_listener is None


@pytest.mark.asyncio
async def test_shutdown_recovery_is_not_lost_while_neutralization_is_in_flight(hass):
    switches(hass)
    hass.states.async_set("switch.second", "on")
    room = RoomRuntime(
        hass,
        "audit",
        config(
            heater_entity_ids=("switch.audit_heater", "switch.second"),
        ),
        Mock(),
    )
    await room.async_start()
    hass.states.async_set("switch.audit_heater", "unavailable")

    async def command(call):
        if call.data["entity_id"] == "switch.second":
            # The failed first bank member returns while the writer awaits a later OFF.
            hass.states.async_set("switch.audit_heater", "on")
            await asyncio.sleep(0)
        hass.states.async_set(call.data["entity_id"], "off")

    hass.services.async_register("switch", "turn_off", command)
    assert not await room.async_stop()
    await hass.async_block_till_done()
    assert hass.states.get("switch.audit_heater").state == "off"
    assert not room._ready
    assert await room.async_stop()


@pytest.mark.asyncio
async def test_shared_shutdown_recovery_is_not_lost_during_failed_off(hass):
    hass.states.async_set("switch.shared", "on")
    calls = 0

    async def command(call):
        nonlocal calls
        calls += 1
        if calls == 1:
            hass.states.async_set("switch.shared", "unavailable")
            hass.states.async_set("switch.shared", "on")
            await asyncio.sleep(0)
            raise HomeAssistantError("transient failure")
        hass.states.async_set("switch.shared", "off")

    hass.services.async_register("switch", "turn_off", command)
    controller = ControllerRuntime(
        hass,
        "audit",
        ControllerConfig(
            name="Audit",
            shared_heat_source_entity_id="switch.shared",
        ),
        {},
    )
    await controller.async_start()
    assert not await controller.async_stop()
    await hass.async_block_till_done()
    assert hass.states.get("switch.shared").state == "off"
    assert not controller._startup_complete
    assert await controller.async_stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [VirtualMode.HEAT, VirtualMode.AUTO])
async def test_ac_only_heat_latch_tracks_acknowledged_output_not_shared_demand(hass, mode):
    switches(hass)
    attrs = {"hvac_modes": ["off", "heat", "cool"], "temperature": 22}
    hass.states.async_set("climate.ac", "off", attrs)

    async def command(call):
        hass.states.async_set("climate.ac", call.data["hvac_mode"], attrs)

    hass.services.async_register("climate", "set_hvac_mode", command)
    room = RoomRuntime(
        hass,
        "audit",
        config(
            heater_entity_ids=(),
            ac_entity_ids=("climate.ac",),
            heating_hysteresis_on=1,
            heating_hysteresis_off=0,
        ),
        Mock(),
    )
    assert await room.async_finish_startup()
    await room.async_set_mode(mode)
    assert hass.states.get("climate.ac").state == "heat"
    assert not room.heat_demand
    hass.states.async_set("sensor.audit_temp", "21.5")
    await room.async_evaluate()
    assert hass.states.get("climate.ac").state == "heat"
    assert room._memory.heating_active
    hass.states.async_set("sensor.audit_temp", "22")
    await room.async_evaluate()
    assert hass.states.get("climate.ac").state == "off"
    hass.states.async_set("sensor.audit_temp", "21.9")
    await room.async_evaluate()
    assert hass.states.get("climate.ac").state == "off"
    assert not room._memory.heating_active
    await room.async_stop()


@pytest.mark.asyncio
async def test_each_source_expiry_recomputes_the_remaining_fresh_mean(hass, freezer):
    switches(hass)
    hass.states.async_set("sensor.second", "24")
    freezer.tick(timedelta(seconds=100))
    hass.states.async_set("sensor.audit_temp", "20", {"unit_of_measurement": "°C"})
    room = RoomRuntime(
        hass,
        "audit",
        config(
            temperature_sensor_entity_ids=("sensor.audit_temp", "sensor.second"),
        ),
        Mock(),
    )
    await room.async_start()
    assert await room.async_finish_startup()
    await room.async_set_mode(VirtualMode.HEAT)
    assert room.current_temperature == 22
    assert not room.heat_demand
    freezer.tick(timedelta(seconds=200))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert room.current_temperature == 20
    assert room.heat_demand
    freezer.tick(timedelta(seconds=100))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert room.current_temperature is None
    assert not room.heat_demand
    await room.async_stop()


@pytest.mark.asyncio
async def test_unbounded_freshness_has_no_timer_and_stop_cancels_bounded_timer(hass):
    switches(hass)
    room = RoomRuntime(hass, "audit", config(temperature_sensor_max_age_seconds=None), Mock())
    await room.async_start()
    assert await room.async_finish_startup()
    assert room._cancel_freshness_timer is None
    await room.async_stop()
    bounded = RoomRuntime(hass, "bounded", config(), Mock())
    await bounded.async_start()
    assert await bounded.async_finish_startup()
    assert bounded._cancel_freshness_timer is not None
    await bounded.async_stop()
    assert bounded._cancel_freshness_timer is None
    # A queued expiry callback cannot actuate after ownership cleanup.
    bounded._async_freshness_expired(utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("switch.audit_heater").state == "off"
