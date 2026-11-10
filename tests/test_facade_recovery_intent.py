"""HA must dispatch intent to a suspended facade without arming outputs."""

import asyncio
from datetime import timedelta
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_integration import entity_id
from test_winter_regressions import register_devices, room_config, set_climate

from custom_components.virtual_hvac import actuators
from custom_components.virtual_hvac.control import Preset, VirtualMode


@pytest.mark.parametrize("command", ["off", "turn_off", "target_preset", "unload"])
@pytest.mark.parametrize(
    ("blocker", "during_ack"),
    [(None, False), ("stale_temperature", False), ("output_unavailable", False), (None, True)],
)
async def test_loaded_facade_accepts_latest_intent_during_recovery(
    hass, monkeypatch, command, blocker, during_ack
):
    """Use the actual climate dispatcher; emulate downstream physical ACKs only."""
    register_devices(hass)
    set_climate(hass)
    set_climate(hass, "climate.trv", target=19)
    hass.states.async_set("sensor.winter", "18.7", {"unit_of_measurement": "°C"})
    cfg = room_config(ac_entity_ids=("climate.ac",), heater_entity_ids=("climate.trv",))
    sub = ConfigSubentry(
        data=MappingProxyType(cfg.to_mapping()),
        subentry_type="room",
        title=cfg.name,
        unique_id=None,
    )
    entry = MockConfigEntry(
        domain="virtual_hvac", data={"name": "Recovery intent"}, subentries_data=[sub.as_dict()]
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[sub.subentry_id]
    facade = entity_id(hass, "climate", entry, sub, "climate")

    async def service(name, **data):
        await hass.services.async_call(
            "climate", name, {"entity_id": facade, **data}, blocking=True
        )

    await service("set_temperature", temperature=19, hvac_mode="heat")
    await service("set_preset_mode", preset_mode="boost")
    armed, rejected, superseded = True, False, False
    tasks, calls = [], []
    entered, release = asyncio.Event(), asyncio.Event()
    hold_recovery = False

    async def physical(event):
        nonlocal rejected, superseded
        data = event.data["service_data"]
        eid = data.get("entity_id")
        if eid not in ("climate.ac", "climate.trv"):
            return
        calls.append(dict(data))
        old = hass.states.get(eid)
        attrs = dict(old.attributes)
        if event.data["service"] == "set_temperature":
            attrs["temperature"] = data["temperature"]
            hass.states.async_set(eid, old.state, attrs)
        else:
            mode = data["hvac_mode"]
            if armed and eid == "climate.ac" and mode == "heat" and not superseded:
                superseded = True
                tasks.append(asyncio.create_task(service("set_temperature", temperature=21)))
                await asyncio.sleep(0)
            if armed and eid == "climate.trv" and mode == "off" and not rejected:
                rejected = True
                return
            if hold_recovery and eid == "climate.trv" and mode == "off":
                entered.set()
                await release.wait()
            if blocker == "output_unavailable" and old.state == "unavailable":
                return
            hass.states.async_set(eid, mode, attrs)

    unsub = hass.bus.async_listen(EVENT_CALL_SERVICE, physical)
    monkeypatch.setattr(actuators, "COMMAND_ACK_TIMEOUT", 0.03)
    try:
        await service("set_temperature", temperature=20)
        await asyncio.gather(*tasks)
        await hass.async_block_till_done()
        assert room.status == "stale_command_neutralization_failed"
        assert not room.available and not room.heat_demand
        assert room.target_temperature == 21 and room.preset is Preset.BOOST
        armed = False
        if blocker == "stale_temperature":
            # Age the actual input, without freezing monotonic adapter deadlines.
            state = hass.states.get("sensor.winter")
            monkeypatch.setattr(
                "custom_components.virtual_hvac.runtime.utcnow",
                lambda: state.last_reported + timedelta(seconds=301),
            )
        elif blocker == "output_unavailable":
            hass.states.async_set("climate.ac", "unavailable")
        await hass.async_block_till_done()
        if blocker == "stale_temperature":
            assert room._cancel_freshness_timer is not None
            room._cancel_freshness_timer()
            room._async_freshness_expired(utcnow())
            await hass.async_block_till_done()
            assert hass.states.get(facade).attributes["current_temperature"] is None
        calls.clear()
        if during_ack:
            hold_recovery = True
            monkeypatch.setattr(actuators, "COMMAND_ACK_TIMEOUT", 1)
            room._cancel_retry_timer()
            room._async_retry(utcnow())
            await asyncio.wait_for(entered.wait(), 1)

            def accepted():
                if command == "target_preset":
                    return room.target_temperature == 22 and room.preset is Preset.SLEEP
                if command == "unload":
                    return room._stopping
                return room.mode is VirtualMode.OFF

            if command == "target_preset":
                pending = [
                    asyncio.create_task(service("set_temperature", temperature=22)),
                    asyncio.create_task(service("set_preset_mode", preset_mode="sleep")),
                ]
            elif command == "unload":
                pending = [asyncio.create_task(hass.config_entries.async_unload(entry.entry_id))]
            else:
                pending = [
                    asyncio.create_task(
                        service("turn_off")
                        if command == "turn_off"
                        else service("set_hvac_mode", hvac_mode="off")
                    )
                ]

            async def wait_for_intent():
                # Pump a bounded number of dispatcher turns while the writer is held.
                for _ in range(100):
                    if accepted():
                        return
                    await asyncio.sleep(0)
                assert accepted()

            try:
                await asyncio.wait_for(wait_for_intent(), 0.5)
                assert not room.available and not room.heat_demand
                assert not any(call.get("hvac_mode") == "heat" for call in calls)
            finally:
                release.set()
            await asyncio.gather(*pending)
            await hass.async_block_till_done()
            if command == "target_preset":
                assert room.available and room.preset is Preset.SLEEP
                assert hass.states.get("climate.trv").attributes["temperature"] == 23
            else:
                assert not room.heat_demand
                assert hass.states.get("climate.ac").state == "off"
                assert hass.states.get("climate.trv").state == "off"
                assert not any(call.get("hvac_mode") == "heat" for call in calls)
            if command == "unload":
                room._async_retry(utcnow())
                await hass.async_block_till_done()
                assert room._stopping and not room.command_available
                assert not room.available and not room.heat_demand
            return
        if command == "off":
            await service("set_hvac_mode", hvac_mode="off")
        elif command == "turn_off":
            await service("turn_off")
        elif command == "target_preset":
            await service("set_temperature", temperature=22)
            await service("set_preset_mode", preset_mode="sleep")
        else:
            # Restore telemetry so actual unload can confirm neutralization.
            if blocker == "output_unavailable":
                set_climate(hass)
            assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        if command != "unload":
            published = hass.states.get(facade)
            assert published.state != "unavailable"
            assert published.attributes["hvac_action"] == "off"
            assert published.attributes["recovering"] is True
            assert (
                published.attributes["controller_status"] == "stale_command_neutralization_failed"
            )
            if blocker == "stale_temperature":
                assert published.attributes["current_temperature"] is None
            assert not room.available and not room.heat_demand
            assert not calls  # Intents do not bypass the neutralization retry.
            if command == "target_preset":
                assert room.target_temperature == 22 and room.preset is Preset.SLEEP
                assert published.attributes["temperature"] == 22
                assert published.attributes["preset_mode"] == "sleep"
            else:
                assert room.mode is VirtualMode.OFF
                assert published.state == "off"
        room._cancel_retry_timer()
        room._async_retry(utcnow())
        await hass.async_block_till_done()
        if command == "unload":
            assert room._stopping and not room.available and not room.heat_demand
        elif blocker is not None:
            assert not room.available and not room.heat_demand
            assert not any(call.get("hvac_mode") == "heat" for call in calls)
            # Fresh authoritative inputs must still pass a later OFF barrier.
            monkeypatch.undo()
            if blocker == "output_unavailable":
                set_climate(hass)
            await hass.async_block_till_done()
            room._cancel_retry_timer()
            room._async_retry(utcnow())
            await hass.async_block_till_done()
        if command != "unload":
            assert room.available
        if command in ("off", "turn_off", "unload"):
            assert not room.heat_demand
            assert hass.states.get("climate.ac").state == "off"
            assert hass.states.get("climate.trv").state == "off"
            assert not any(call.get("hvac_mode") == "heat" for call in calls)
        else:
            assert room.target_temperature == 22 and room.preset is Preset.SLEEP
            assert hass.states.get("climate.trv").attributes["temperature"] == 23
    finally:
        armed = False
        if blocker == "output_unavailable":
            set_climate(hass)
        if command != "unload" or not room._stopping:
            assert await hass.config_entries.async_unload(entry.entry_id)
        unsub()
        room._cleanup()
