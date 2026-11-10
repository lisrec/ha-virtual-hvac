"""Normal unchanged reports must retain recovery facade expiry publication."""

import asyncio
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EVENT_CALL_SERVICE
from homeassistant.util.dt import utcnow
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from test_integration import entity_id
from test_winter_regressions import register_devices, room_config, set_climate

from custom_components.virtual_hvac import actuators


@pytest.mark.parametrize("report", [False, True])
async def test_unchanged_report_preserves_recovery_expiry(hass, monkeypatch, report):
    register_devices(hass)
    set_climate(hass)
    set_climate(hass, "climate.trv", target=19)
    hass.states.async_set("sensor.winter", "18.7", {"unit_of_measurement": "°C"})
    cfg = room_config(
        ac_entity_ids=("climate.ac",),
        heater_entity_ids=("climate.trv",),
        temperature_sensor_max_age_seconds=1,
    )
    sub = ConfigSubentry(
        data=MappingProxyType(cfg.to_mapping()),
        subentry_type="room",
        title=cfg.name,
        unique_id=None,
    )
    entry = MockConfigEntry(
        domain="virtual_hvac",
        data={"name": "Recovery expiry"},
        subentries_data=[sub.as_dict()],
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
    tasks = []

    async def physical(event):
        nonlocal rejected, superseded
        data = event.data["service_data"]
        eid = data.get("entity_id")
        if eid not in ("climate.ac", "climate.trv"):
            return
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
            hass.states.async_set(eid, mode, attrs)

    unsub = hass.bus.async_listen(EVENT_CALL_SERVICE, physical)
    monkeypatch.setattr(actuators, "COMMAND_ACK_TIMEOUT", 0.03)
    try:
        await service("set_temperature", temperature=20)
        await asyncio.gather(*tasks)
        await hass.async_block_till_done()
        assert room.status == "stale_command_neutralization_failed"
        assert not room.available and not room._ready and not room.heat_demand
        assert hass.states.get(facade).attributes["recovering"] is True
        armed = False
        if report:
            hass.states.async_set("sensor.winter", "18.7", {"unit_of_measurement": "°C"})
        await hass.async_block_till_done()
        # Real wall time and real scheduled expiry, before the >=5s recovery retry.
        await asyncio.sleep(1.1)
        async_fire_time_changed(hass, utcnow())
        await hass.async_block_till_done()
        assert not room._ready and not room.heat_demand
        assert room.current_temperature is None
        published = hass.states.get(facade)
        assert published.attributes["hvac_action"] == "off"
        assert published.attributes["recovering"] is True
        assert published.attributes["current_temperature"] is None
    finally:
        armed = False
        assert await hass.config_entries.async_unload(entry.entry_id)
        unsub()
        room._cleanup()
