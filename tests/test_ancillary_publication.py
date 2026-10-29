"""Publish raw ancillary telemetry independently of primary reconciliation."""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.core import callback
from pytest_homeassistant_custom_component.common import async_mock_service
from test_integration import entity_id, make_entry, set_source_states

from custom_components.virtual_hvac.control import VirtualMode


@pytest.fixture
async def publication_room(hass):
    """Load the real facade with acknowledged state-only primary outputs."""
    set_source_states(hass)
    old = hass.states.get("climate.test_ac")
    hass.states.async_set(
        old.entity_id,
        old.state,
        old.attributes | {"fan_modes": ["low", "medium"], "fan_mode": "low", "swing_mode": "off"},
    )
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
    facade = entity_id(hass, "climate", entry, subentry, "climate")
    try:
        yield entry, room, facade
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("kind", "initial", "value"), [("fan", "low", "medium"), ("swing", "off", "vertical")]
)
async def test_late_ancillary_ack_updates_published_facade_without_primary_commands(
    hass, publication_room, monkeypatch, kind, initial, value
):
    """An actual adapter ACK timeout must not strand the facade after raw recovery."""
    _, room, facade = publication_room
    monkeypatch.setattr("custom_components.virtual_hvac.actuators.COMMAND_ACK_TIMEOUT", 0.01)
    calls = []

    @callback
    def record(event):
        calls.append(event.data)

    remove = hass.bus.async_listen(EVENT_CALL_SERVICE, record)
    async_mock_service(hass, "climate", f"set_{kind}_mode")
    decision = room.decision
    generation = room._generation
    try:
        await getattr(room, f"async_set_{kind}_mode")(value)
        await hass.async_block_till_done()
        assert not room._lock.locked()
        assert hass.states.get("climate.test_ac").attributes[f"{kind}_mode"] == initial
        assert hass.states.get(facade).attributes[f"{kind}_mode"] == initial
        before_late_ack = list(calls)
        old = hass.states.get("climate.test_ac")
        hass.states.async_set(old.entity_id, old.state, old.attributes | {f"{kind}_mode": value})
        await hass.async_block_till_done()
        assert hass.states.get(facade).attributes[f"{kind}_mode"] == value
        assert calls == before_late_ack
        assert all(call["service"] == f"set_{kind}_mode" for call in calls)
        assert room.decision is decision
        assert room._generation == generation
        assert len(calls) == 1
        assert calls[0]["service_data"]["entity_id"] == "climate.test_ac"
        assert room.target_temperature == 18
        assert room.preset.value == "comfort"
        assert room.mode is VirtualMode.COOL
        assert room.physical_status == "outputs_confirmed"
        assert hass.states.get("climate.test_ac").state == "cool"
        assert not room.heat_demand
        assert room._cancel_timer is None
    finally:
        remove()


@pytest.mark.parametrize(("kind", "value"), [("fan", "medium"), ("swing", "vertical")])
async def test_ancillary_ack_during_locked_setter_is_published_after_confirmation(
    hass, publication_room, kind, value
):
    """Keep own ACK suppression under the lock and publish when the setter drains."""
    _, room, facade = publication_room
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    @callback
    def record(event):
        calls.append(event.data)

    async def setter(call):
        old = hass.states.get("climate.test_ac")
        hass.states.async_set(old.entity_id, old.state, old.attributes | {f"{kind}_mode": value})
        # Deliver the real state event before allowing adapter confirmation.
        await asyncio.sleep(0)
        assert room._lock.locked()
        entered.set()
        await release.wait()

    remove = hass.bus.async_listen(EVENT_CALL_SERVICE, record)
    hass.services.async_register("climate", f"set_{kind}_mode", setter)
    decision = room.decision
    generation = room._generation
    pending = asyncio.create_task(getattr(room, f"async_set_{kind}_mode")(value))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert room._lock.locked()
        release.set()
        await asyncio.wait_for(pending, 1)
        await hass.async_block_till_done()
        assert hass.states.get(facade).attributes[f"{kind}_mode"] == value
        assert len(calls) == 1
        assert calls[0]["service"] == f"set_{kind}_mode"
        assert calls[0]["service_data"]["entity_id"] == "climate.test_ac"
        assert room.decision is decision
        assert room._generation == generation
        assert room.mode is VirtualMode.COOL
        assert room.target_temperature == 18
        assert room.preset.value == "comfort"
        assert room.physical_status == "outputs_confirmed"
        assert room._cancel_timer is None
    finally:
        release.set()
        await pending
        remove()
