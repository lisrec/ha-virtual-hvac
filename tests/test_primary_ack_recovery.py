"""Superseded primary ACK failures recover, unlike explicit owner shutdown."""

import asyncio
from datetime import timedelta
from types import MappingProxyType, SimpleNamespace

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from test_integration import entity_id
from test_winter_regressions import register_devices, room_config, set_climate, start_room

from custom_components.virtual_hvac import actuators
from custom_components.virtual_hvac.control import Preset, VirtualMode


async def test_target_steps_during_boost_ack_recover_after_late_trv_off(hass, monkeypatch):
    """Real adapter waits: separate AC mode/target feedback and TRV OFF past budget."""
    register_devices(hass)
    set_climate(hass)
    set_climate(hass, "climate.trv", target=19)
    hass.states.async_set("sensor.winter", "18.7", {"unit_of_measurement": "°C"})
    cfg = room_config(ac_entity_ids=("climate.ac",), heater_entity_ids=("climate.trv",))
    subentry = ConfigSubentry(
        data=MappingProxyType(cfg.to_mapping()),
        subentry_type="room",
        title=cfg.name,
        unique_id=None,
    )
    entry = MockConfigEntry(
        domain="virtual_hvac", data={"name": "ACK recovery"}, subentries_data=[subentry.as_dict()]
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    facade_id = entity_id(hass, "climate", entry, subentry, "climate")

    async def service(service, **data):
        await hass.services.async_call(
            "climate", service, {"entity_id": facade_id, **data}, blocking=True
        )

    await service("set_temperature", temperature=19, hvac_mode="heat")
    await service("set_preset_mode", preset_mode="boost")
    assert not room.heat_demand
    assert hass.states.get("climate.ac").state == "off"
    monkeypatch.setattr(actuators, "COMMAND_ACK_TIMEOUT", 0.05)
    loop = asyncio.get_running_loop()
    handles, calls, intent_tasks = [], [], []
    first_ac = True
    first_off = True

    @callback
    def ack(entity_id, mode=None, target=None):
        old = hass.states.get(entity_id)
        attrs = dict(old.attributes)
        if target is not None:
            attrs["temperature"] = target
        hass.states.async_set(entity_id, old.state if mode is None else mode, attrs)

    @callback
    def target(value):
        intent_tasks.append(asyncio.create_task(service("set_temperature", temperature=value)))

    async def setter(call):
        nonlocal first_ac, first_off
        entity_id = call.data["entity_id"]
        calls.append((entity_id, call.service, dict(call.data)))
        if call.service == "set_temperature":
            handles.append(loop.call_later(0.01, ack, entity_id, None, call.data["temperature"]))
        else:
            mode = call.data["hvac_mode"]
            delay = 0.01
            if entity_id == "climate.ac" and mode == "heat" and first_ac:
                first_ac = False
                handles.extend(
                    [loop.call_later(0.002, target, 20.5), loop.call_later(0.004, target, 21)]
                )
            if entity_id == "climate.trv" and mode == "off" and first_off:
                first_off = False
                delay = 0.08
            handles.append(loop.call_later(delay, ack, entity_id, mode))

    async def physical_service(event):
        data = event.data
        if data["service_data"].get("entity_id") in ("climate.ac", "climate.trv"):
            await setter(SimpleNamespace(service=data["service"], data=data["service_data"]))

    unsub = hass.bus.async_listen(EVENT_CALL_SERVICE, physical_service)
    try:
        await service("set_temperature", temperature=20)
        await asyncio.gather(*intent_tasks)
        await hass.async_block_till_done()
        # Earliest failure, before a shutdown retry can overwrite its reason.
        assert room.status == "stale_command_neutralization_failed"
        assert not room.available
        assert hass.states.get(facade_id).state == "heat"
        assert hass.states.get(facade_id).attributes["hvac_action"] == "off"
        assert hass.states.get(facade_id).attributes["recovering"] is True
        assert not room.heat_demand
        assert room._cancel_timer is not None
        await asyncio.sleep(0.09)
        await hass.async_block_till_done()
        assert hass.states.get("climate.ac").state == "off"
        assert hass.states.get("climate.trv").state == "off"
        room._cancel_retry_timer()
        room._async_retry(utcnow())
        await hass.async_block_till_done()
        assert room.available
        assert not room._stopping
        assert room.target_temperature == 21
        assert room.preset is Preset.BOOST
        assert room.physical_status == "outputs_confirmed"
        assert room.heat_demand
        assert hass.states.get("climate.ac").state == "heat"
        assert hass.states.get("climate.ac").attributes["temperature"] == 21
        assert hass.states.get("climate.trv").state == "heat"
        assert hass.states.get("climate.trv").attributes["temperature"] == 22
        assert room._cancel_timer is None
        assert hass.states.get(facade_id).state == "heat"
        assert hass.states.get(facade_id).attributes["temperature"] == 21
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)
        unsub()
        for handle in handles:
            handle.cancel()
        room._cleanup()


@pytest.fixture
async def suspended_room(hass):
    """Reach suspension through real supersession and a rejected neutral command."""
    calls = register_devices(hass)
    set_climate(hass)
    set_climate(hass, "climate.trv", target=10)
    hass.states.async_set("binary_sensor.window", "off")
    hass.states.async_set("switch.rapid", "off")
    room = await start_room(
        hass,
        room_config(
            heater_entity_ids=("climate.trv",),
            ac_entity_ids=("climate.ac",),
            enable_safe_cooling_delay=True,
            minimum_seconds_cooling_off=30,
            window_entity_ids=("binary_sensor.window",),
            rapid_entity_id="switch.rapid",
        ),
    )
    entered, release = asyncio.Event(), asyncio.Event()
    reject_off = True

    async def target(call):
        entered.set()
        await release.wait()
        old = hass.states.get(call.data["entity_id"])
        hass.states.async_set(
            old.entity_id, old.state, old.attributes | {"temperature": call.data["temperature"]}
        )

    async def mode(call):
        nonlocal reject_off
        if call.data["hvac_mode"] == "off" and reject_off:
            reject_off = False
            raise HomeAssistantError("transient OFF failure after supersession")
        old = hass.states.get(call.data["entity_id"])
        calls.append((old.entity_id, dict(call.data)))
        hass.states.async_set(old.entity_id, call.data["hvac_mode"], old.attributes)

    hass.services.async_register("climate", "set_temperature", target)
    hass.services.async_register("climate", "set_hvac_mode", mode)
    first = asyncio.create_task(room.async_set_mode(VirtualMode.HEAT))
    await entered.wait()
    second = asyncio.create_task(room.async_set_preset(Preset.SLEEP))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(first, second)
    await hass.async_block_till_done()
    assert room.status == "stale_command_neutralization_failed"
    assert not room.available and not room.heat_demand
    assert room._cancel_timer is not None
    calls.clear()
    try:
        yield room, calls
    finally:
        await room.async_stop()
        room._cleanup()


@pytest.mark.parametrize("blocker", ["stale_temperature", "unknown_window", "optional_output"])
async def test_recovery_requires_full_neutral_bank_and_authoritative_inputs(
    hass, freezer, suspended_room, blocker
):
    room, calls = suspended_room
    if blocker == "stale_temperature":
        freezer.tick(timedelta(seconds=301))
    elif blocker == "unknown_window":
        hass.states.async_set("binary_sensor.window", "unknown")
    else:
        hass.states.async_set("switch.rapid", "unavailable")
    async_fire_time_changed(hass, utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert not room.available and not room.heat_demand
    assert not room._stopping
    assert room.status == "stale_command_neutralization_failed"
    assert room._cancel_timer is not None
    assert hass.states.get("climate.trv").state == "off"
    assert not any(data.get("hvac_mode") == "heat" for _, data in calls)
    # Even unchanged reports may not bypass this barrier; the bounded retry owns it.
    hass.states.async_set("sensor.winter", "20", {"unit_of_measurement": "°C"}, force_update=True)
    hass.states.async_set("binary_sensor.window", "off")
    hass.states.async_set("switch.rapid", "off")
    await hass.async_block_till_done()
    assert not room.available
    freezer.tick(timedelta(seconds=21))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert room.available and room.heat_demand
    assert room.preset is Preset.SLEEP
    assert hass.states.get("climate.trv").state == "heat"


async def test_recovery_retains_confirmed_off_protection(hass, freezer, suspended_room):
    room, calls = suspended_room
    await room.async_set_temperature_intent(18, VirtualMode.COOL)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert room.available
    assert room.status == "ac_minimum_off"
    assert hass.states.get("climate.ac").state == "off"
    assert room._cancel_timer is not None
    assert not any(data.get("hvac_mode") == "cool" for _, data in calls)
    freezer.tick(timedelta(seconds=31))
    async_fire_time_changed(hass, utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("climate.ac").state == "cool"
    assert hass.states.get("climate.ac").attributes["temperature"] == 18
    assert room.physical_status == "outputs_confirmed"


@pytest.mark.parametrize("during_ack", [False, True])
async def test_explicit_stop_cannot_be_rearmed_by_supersession_recovery(
    hass, suspended_room, during_ack
):
    room, calls = suspended_room
    entered, release = asyncio.Event(), asyncio.Event()

    async def off(call):
        assert call.data["hvac_mode"] == "off"
        entered.set()
        await release.wait()
        old = hass.states.get("climate.trv")
        hass.states.async_set(old.entity_id, "off", old.attributes)

    if during_ack:
        hass.services.async_register("climate", "set_hvac_mode", off)
        room._cancel_retry_timer()
        recovery = asyncio.create_task(room._async_recover_superseded())
        await entered.wait()
        shutdown = asyncio.create_task(room.async_stop(cleanup=False))
        await asyncio.sleep(0)
        release.set()
        await recovery
        assert await shutdown
    else:
        assert await room.async_stop(cleanup=False)
        await room._async_recover_superseded()  # A previously queued recovery is harmless.
    calls.clear()
    await room.async_set_target_temperature(24)
    room._async_retry(utcnow())
    hass.states.async_set("sensor.winter", "18", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    assert room._stopping and not room._ready
    assert not room.available and not room.heat_demand
    assert hass.states.get("climate.trv").state == "off"
    assert not calls
