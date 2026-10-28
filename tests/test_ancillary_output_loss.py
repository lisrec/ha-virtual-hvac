"""Output faults during ancillary writes must not strand shared CO demand."""

from __future__ import annotations

import asyncio

import pytest
from test_winter_regressions import register_devices, room_config, set_climate

from custom_components.virtual_hvac.control import OutputMode, VirtualMode
from custom_components.virtual_hvac.models import ControllerConfig
from custom_components.virtual_hvac.runtime import ControllerRuntime


@pytest.mark.parametrize(("kind", "value"), [("fan", "low"), ("swing", "vertical")])
@pytest.mark.parametrize("fault", ["ac_unavailable", "rapid_unknown"])
@pytest.mark.parametrize("reject", [False, True])
async def test_ancillary_output_loss_withdraws_prior_heat_on_success_or_failure(
    hass, kind, value, fault, reject
):
    register_devices(hass)
    set_climate(hass)
    old = hass.states.get("climate.ac")
    hass.states.async_set(
        "climate.ac",
        "off",
        old.attributes | {"fan_modes": ["low"], "swing_modes": ["vertical"]},
    )
    hass.states.async_set("switch.rapid", "off")
    controller = ControllerRuntime(
        hass,
        "ancillary-loss",
        ControllerConfig(name="Output loss"),
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
    await room.async_set_mode(VirtualMode.HEAT)
    await hass.async_block_till_done()
    assert room.heat_demand and controller.aggregate_heat_demand
    entered, release = asyncio.Event(), asyncio.Event()

    async def setter_service(call):
        entered.set()
        await release.wait()
        current = hass.states.get("climate.ac")
        hass.states.async_set(
            "climate.ac",
            "unavailable" if fault == "ac_unavailable" else "off",
            current.attributes | {kind + "_mode": value},
        )
        if fault == "rapid_unknown":
            hass.states.async_set("switch.rapid", "unknown")
        await asyncio.sleep(0)
        if reject:
            raise ValueError("Expected downstream ancillary rejection")

    hass.services.async_register("climate", "set_" + kind + "_mode", setter_service)
    pending = asyncio.create_task(getattr(room, "async_set_" + kind + "_mode")(value))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert room._lock.locked()
        release.set()
        await asyncio.wait_for(pending, 2)
        await hass.async_block_till_done()
        assert not room.heat_demand
        assert not controller.aggregate_heat_demand
        assert room.decision.output_mode is OutputMode.OFF
        assert room.physical_status == "physical_neutralization_failed"
        assert room._cancel_timer is not None
        assert room.mode is VirtualMode.HEAT
        assert room.target_temperature == 22
        assert room.config.temperature_sensor_max_age_seconds == 300
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        current = hass.states.get("climate.ac")
        hass.states.async_set("climate.ac", "off", current.attributes)
        hass.states.async_set("switch.rapid", "off")
        assert await controller.async_stop()
