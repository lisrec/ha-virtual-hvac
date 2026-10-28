"""Ancillary commands share the room writer and lifecycle safety barriers."""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest
from pytest_homeassistant_custom_component.common import async_mock_service
from test_integration import make_entry, set_source_states

from custom_components.virtual_hvac.control import VirtualMode
from custom_components.virtual_hvac.models import RoomConfig
from custom_components.virtual_hvac.runtime import RoomRuntime


@pytest.fixture
async def ancillary_room(hass):
    """Use the real entry lifecycle with state-only acknowledged output devices."""
    set_source_states(hass)
    async_mock_service(hass, "switch", "turn_on")
    async_mock_service(hass, "switch", "turn_off")
    entry, subentry = make_entry(shared=False)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    room = entry.runtime_data.rooms[subentry.subentry_id]
    await room.async_set_target_temperature(18)
    await room.async_set_mode(VirtualMode.COOL)
    await hass.async_block_till_done()
    assert hass.states.get("climate.test_ac").state == "cool"
    try:
        yield entry, room
    finally:
        # Stop directly as well: the test may already have unloaded the real entry.
        assert await room.async_stop()
        await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("kind", "value"), [("fan", "low"), ("swing", "vertical")])
@pytest.mark.parametrize("barrier", ["off", "unload"])
async def test_accepted_ancillary_write_finishes_before_off_or_unload_success(
    hass, ancillary_room, kind, value, barrier
):
    """A truthful whole-device setter must drain before final OFF/ownership removal."""
    entry, room = ancillary_room
    controller = entry.runtime_data
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def whole_device_setter(call):
        old = hass.states.get("climate.test_ac")
        captured_mode = old.state
        captured_attributes = dict(old.attributes) | {f"{kind}_mode": value}
        entered.set()
        await release.wait()
        hass.states.async_set("climate.test_ac", captured_mode, captured_attributes)
        order.append("ancillary_applied")

    hass.services.async_register("climate", f"set_{kind}_mode", whole_device_setter)

    async def ancillary():
        await getattr(room, f"async_set_{kind}_mode")(value)
        order.append("ancillary_returned")

    async def stop():
        if barrier == "off":
            await room.async_set_mode(VirtualMode.OFF)
        else:
            assert await hass.config_entries.async_unload(entry.entry_id)
        order.append("barrier_succeeded")

    setter = asyncio.create_task(ancillary())
    shutdown = None
    try:
        await asyncio.wait_for(entered.wait(), 1)
        shutdown = asyncio.create_task(stop())
        done, _ = await asyncio.wait({shutdown}, timeout=0.02)
        assert not done, "OFF/unload succeeded while an accepted output write was still pending"
        assert room._remove_state_listener is not None
        assert room._remove_report_listener is not None
        release.set()
        await asyncio.wait_for(asyncio.gather(setter, shutdown), 1)
        await hass.async_block_till_done()
        assert order == ["ancillary_applied", "ancillary_returned", "barrier_succeeded"]
        assert all(hass.states.get(eid).state == "off" for eid in room.config.output_entity_ids())
        assert not room.heat_demand
        assert not controller.aggregate_heat_demand
        assert room.target_temperature == 18
        assert room._cancel_timer is None
        if barrier == "off":
            assert room._ready and not room._stopping
            assert room._remove_state_listener is not None
            assert room._remove_report_listener is not None
        else:
            assert not room._ready and room._stopping
            assert room._remove_state_listener is None
            assert room._remove_report_listener is None
    finally:
        release.set()
        await asyncio.gather(*[task for task in (setter, shutdown) if task is not None])


@pytest.mark.parametrize(("kind", "value"), [("fan", "low"), ("swing", "vertical")])
@pytest.mark.parametrize("phase", ["startup", "queued_during_disarm", "stopped"])
async def test_unready_ancillary_commands_never_reach_outputs(hass, kind, value, phase):
    hass.states.async_set("sensor.temperature", "20", {"unit_of_measurement": "°C"})
    hass.states.async_set(
        "climate.test_ac",
        "off",
        {"hvac_modes": ["off"], "fan_modes": ["low"], "swing_modes": ["vertical"]},
    )
    room = RoomRuntime(
        hass,
        "lifecycle",
        RoomConfig(
            name="Lifecycle",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_ids=("climate.test_ac",),
        ),
        Mock(),
    )
    await room.async_start()
    if phase != "startup":
        assert await room.async_finish_startup()
    if phase == "stopped":
        assert await room.async_stop()
    calls = []
    listener = Mock()
    room.async_add_listener(listener)

    async def ancillary_service(call):
        calls.append(dict(call.data))
        old = hass.states.get("climate.test_ac")
        hass.states.async_set(
            "climate.test_ac", old.state, old.attributes | {f"{kind}_mode": value}
        )

    hass.services.async_register("climate", f"set_{kind}_mode", ancillary_service)
    try:
        async with room._lock:
            setter = asyncio.create_task(getattr(room, f"async_set_{kind}_mode")(value))
            await asyncio.sleep(0)
            if phase == "queued_during_disarm":
                # Accepted into the writer queue while ready; shutdown invalidates it.
                room._disarm()
        await asyncio.wait_for(setter, 1)
        assert calls == [], "Unready/stopping runtime still issued an ancillary service"
        listener.assert_not_called()
    finally:
        assert await room.async_stop()
