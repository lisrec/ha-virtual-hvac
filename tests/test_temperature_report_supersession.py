"""Temperature-only reports must not supersede accepted primary commands."""

import asyncio
from datetime import timedelta
from unittest.mock import Mock

import pytest
from homeassistant.core import Event, State, callback
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from test_audit_regressions import config, switches
from test_runtime_branches import make_room, set_authoritative_states

from custom_components.virtual_hvac import actuators
from custom_components.virtual_hvac.control import Preset, VirtualMode
from custom_components.virtual_hvac.runtime import RoomRuntime


@pytest.mark.parametrize("report", ["attribute", "force"])
async def test_other_temperature_source_expiry_is_not_hidden_by_equal_report(hass, freezer, report):
    """Multi-source averages must still reconcile when another source has expired."""
    switches(hass)
    hass.states.async_set("sensor.audit_temp", "10", {"unit_of_measurement": "°C"})
    freezer.tick(timedelta(seconds=200))
    hass.states.async_set("sensor.second", "30", {"unit_of_measurement": "°C"})
    room = RoomRuntime(
        hass,
        "multi-source",
        config(temperature_sensor_entity_ids=("sensor.audit_temp", "sensor.second")),
        Mock(),
    )
    await room.async_start()
    assert await room.async_finish_startup()
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    assert room.current_temperature == 20
    assert room.heat_demand
    try:
        freezer.tick(timedelta(seconds=101))
        attrs = {"unit_of_measurement": "°C"}
        if report == "attribute":
            attrs["timestamp"] = "new"
        hass.states.async_set("sensor.second", "30", attrs, force_update=report == "force")
        await hass.async_block_till_done()
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert room.current_temperature == 30
        assert not room.heat_demand
        assert hass.states.get("switch.audit_heater").state == "off"
        assert room._cancel_freshness_timer is not None
    finally:
        room._cleanup()


@pytest.mark.parametrize(
    ("sample", "unit", "extra", "reported_sample"),
    [
        ("23.4", "°C", {}, "23.4"),  # Salon forced identical state_changed
        ("21.5", "°C", {"last_seen": "new-report"}, "21.5"),  # Biuro
        ("21.8", "°C", {"timestamp": "new-report"}, "21.8"),  # Sypialnia
        ("20.0", "°C", {}, "20"),
        ("68", "°F", {}, "68.0"),
        ("20", None, {}, "20"),
    ],
)
async def test_unchanged_temperature_during_fan_ack_does_not_disarm(
    hass, monkeypatch, sample, unit, extra, reported_sample
):
    """Real state_changed + adapter ACK wait, including a late redundant OFF ACK."""
    set_authoritative_states(hass)
    source_attrs = {} if unit is None else {"unit_of_measurement": unit}
    hass.states.async_set("sensor.temperature", sample, source_attrs)
    attrs = {"hvac_modes": ["off", "cool", "fan_only"], "temperature": 22}
    hass.states.async_set("climate.ac", "off", attrs)
    room = make_room(hass)
    await room.async_start()
    assert await room.async_finish_startup()
    await hass.async_block_till_done()
    generation = room._generation
    monkeypatch.setattr(actuators, "COMMAND_ACK_TIMEOUT", 0.04)
    calls, handles = [], []
    loop = asyncio.get_running_loop()

    @callback
    def state(mode):
        hass.states.async_set("climate.ac", mode, attrs)

    @callback
    def report():
        old = hass.states.get("sensor.temperature")
        hass.states.async_set(
            old.entity_id, reported_sample, {**old.attributes, **extra}, force_update=True
        )

    async def setter(call):
        mode = call.data["hvac_mode"]
        calls.append(str(mode))
        handles.append(loop.call_later(0.02 if mode == "fan_only" else 0.08, state, mode))
        if mode == "fan_only":
            handles.append(loop.call_later(0.005, report))

    hass.services.async_register("climate", "set_hvac_mode", setter)
    try:
        await room.async_set_mode(VirtualMode.FAN_ONLY)
        await hass.async_block_till_done()
        assert calls == ["fan_only"]
        assert room.available
        assert room.status == "explicit_fan_only"
        assert room.physical_status == "outputs_confirmed"
        assert room._generation == generation + 1
        assert hass.states.get("climate.ac").state == "fan_only"
        assert hass.states.get("switch.heater").state == "off"
        assert room._cancel_timer is None
    finally:
        for handle in handles:
            handle.cancel()
        room._cleanup()


@pytest.mark.parametrize(
    "change", ["temperature", "unit", "invalid", "missing", "window", "mode", "preset", "target"]
)
async def test_real_changes_still_supersede_pending_fan_ack(hass, change):
    """Safety and intent events must retain stale-generation neutralization."""
    set_authoritative_states(hass)
    attrs = {"hvac_modes": ["off", "cool", "fan_only"], "temperature": 22}
    hass.states.async_set("climate.ac", "off", attrs)
    room = make_room(hass)
    await room.async_start()
    assert await room.async_finish_startup()
    await hass.async_block_till_done()
    generation = room._generation
    calls, handles = [], []
    loop = asyncio.get_running_loop()

    @callback
    def change_input():
        if change == "temperature":
            hass.states.async_set("sensor.temperature", "21", {"unit_of_measurement": "°C"})
        elif change == "unit":
            hass.states.async_set("sensor.temperature", "20.0", {"unit_of_measurement": "°F"})
        elif change == "invalid":
            hass.states.async_set("sensor.temperature", "unavailable")
        elif change == "missing":
            hass.states.async_remove("sensor.temperature")
        elif change == "window":
            hass.states.async_set("binary_sensor.window", "on")
        else:
            intent = {
                "mode": lambda: room.async_set_mode(VirtualMode.OFF),
                "preset": lambda: room.async_set_preset(Preset.BOOST),
                "target": lambda: room.async_set_target_temperature(23),
            }[change]
            hass.async_create_task(intent())

    @callback
    def ack(mode):
        hass.states.async_set("climate.ac", mode, attrs)

    async def setter(call):
        mode = call.data["hvac_mode"]
        calls.append(str(mode))
        if len(calls) == 1:
            handles.append(loop.call_later(0.005, change_input))
            handles.append(loop.call_later(0.02, ack, mode))
        else:
            ack(mode)

    hass.services.async_register("climate", "set_hvac_mode", setter)
    try:
        await room.async_set_mode(VirtualMode.FAN_ONLY)
        await hass.async_block_till_done()
        assert calls[:2] == ["fan_only", "off"]
        assert room._generation >= generation + 2
        assert room._ready and not room._stopping
        assert room.physical_status == "outputs_confirmed"
        if change in ("invalid", "missing", "mode"):
            assert hass.states.get("climate.ac").state == "off"
        if change in ("invalid", "missing"):
            assert room.status == "no_valid_temperature"
            assert not room.heat_demand
            hass.states.async_set("sensor.temperature", "20", {"unit_of_measurement": "°C"})
            await hass.async_block_till_done()
            assert room.available
            assert room.status == "explicit_fan_only"
            assert hass.states.get("climate.ac").state == "fan_only"
    finally:
        for handle in handles:
            handle.cancel()
        room._cleanup()


@pytest.mark.parametrize(
    ("old", "new", "old_unit", "new_unit"),
    [
        ("20", "21", "°C", "°C"),
        ("20", "20", "°C", "°F"),
        ("20", "20", None, "°C"),
        ("20", "20", "invalid", "invalid"),
        ("nan", "nan", "°C", "°C"),
        ("inf", "inf", "°C", "°C"),
        ("-inf", "-inf", "°C", "°C"),
        ("bad", "bad", "°C", "°C"),
        ("unavailable", "20", "°C", "°C"),
        ("unknown", "20", "°C", "°C"),
        (None, "20", "°C", "°C"),
        ("20", None, "°C", "°C"),
    ],
)
async def test_non_equivalent_temperature_events_always_reconcile(
    hass, old, new, old_unit, new_unit
):
    """Even unchanged invalid data is not evidence for skipping reconciliation."""
    set_authoritative_states(hass)
    room = make_room(hass)
    assert await room.async_finish_startup()
    generation = room._generation
    event = Event(
        "state_changed",
        {
            "entity_id": "sensor.temperature",
            "old_state": None
            if old is None
            else State("sensor.temperature", old, {"unit_of_measurement": old_unit}),
            "new_state": None
            if new is None
            else State("sensor.temperature", new, {"unit_of_measurement": new_unit}),
        },
    )
    try:
        room._async_source_changed(event)
        await hass.async_block_till_done()
        assert room._generation == generation + 1
    finally:
        room._cleanup()


@pytest.mark.parametrize("source_age", [300, 301])
async def test_equal_report_after_source_expiry_still_reconciles(hass, freezer, source_age):
    """An expired old sample is not an unchanged effective input, even before its timer runs."""
    set_authoritative_states(hass)
    room = make_room(hass)
    await room.async_start()
    assert await room.async_finish_startup()
    await hass.async_block_till_done()
    generation = room._generation
    try:
        freezer.tick(timedelta(seconds=source_age))
        old = hass.states.get("sensor.temperature")
        hass.states.async_set(old.entity_id, old.state, old.attributes, force_update=True)
        await hass.async_block_till_done()
        assert room._generation > generation
        assert room.available
    finally:
        room._cleanup()


async def test_attribute_report_does_not_postpone_real_protection_retry(hass, freezer):
    set_authoritative_states(hass)
    hass.states.async_set("sensor.temperature", "24", {"unit_of_measurement": "°C"})
    attrs = {"hvac_modes": ["off", "cool"], "temperature": 22}
    hass.states.async_set("climate.ac", "off", attrs)
    room = make_room(hass, enable_safe_cooling_delay=True, minimum_seconds_cooling_off=30)
    calls = []

    async def setter(call):
        calls.append(call.data["hvac_mode"])
        hass.states.async_set("climate.ac", call.data["hvac_mode"], attrs)

    hass.services.async_register("climate", "set_hvac_mode", setter)
    await room.async_start()
    assert await room.async_finish_startup()
    await room.async_set_mode(VirtualMode.COOL)
    await hass.async_block_till_done()
    assert room.status == "ac_minimum_off"
    protection_retry = room._cancel_timer
    assert protection_retry is not None
    generation = room._generation
    try:
        freezer.tick(timedelta(seconds=10))
        hass.states.async_set(
            "sensor.temperature", "24", {"unit_of_measurement": "°C", "last_seen": "new"}
        )
        await hass.async_block_till_done()
        assert room._generation == generation
        assert room._cancel_timer is protection_retry
        freezer.tick(timedelta(seconds=21))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert room.status == "explicit_cool"
        assert hass.states.get("climate.ac").state == "cool"
        assert "cool" in calls
        assert room._cancel_timer is None
    finally:
        room._cleanup()


async def test_attribute_report_preserves_protection_retry_and_refreshes_expiry(hass, freezer):
    switches(hass)
    room = RoomRuntime(hass, "report", config(), Mock())
    await room.async_start()
    assert await room.async_finish_startup()
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    generation = room._generation
    freshness = room._cancel_freshness_timer
    room._cancel_timer = protection_cancel = Mock()
    try:
        freezer.tick(timedelta(seconds=200))
        hass.states.async_set(
            "sensor.audit_temp", "20", {"unit_of_measurement": "°C", "last_seen": "new"}
        )
        await hass.async_block_till_done()
        assert room._generation == generation
        assert room._cancel_freshness_timer is not freshness
        protection_cancel.assert_not_called()
        assert room._cancel_timer is protection_cancel
        freezer.tick(timedelta(seconds=101))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert room.heat_demand
        freezer.tick(timedelta(seconds=200))
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert room.status == "no_valid_temperature"
        assert not room.heat_demand
        assert hass.states.get("switch.audit_heater").state == "off"
        hass.states.async_set(
            "sensor.audit_temp", "20", {"unit_of_measurement": "°C", "last_seen": "recovered"}
        )
        await hass.async_block_till_done()
        assert room.heat_demand
        assert hass.states.get("switch.audit_heater").state == "on"
    finally:
        room._cleanup()
