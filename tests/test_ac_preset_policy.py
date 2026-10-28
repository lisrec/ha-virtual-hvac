"""Physical AC preset flags belong only to an active heat/cool compressor path."""

from __future__ import annotations

import pytest
from test_winter_regressions import register_devices, room_config, set_climate, start_room

from custom_components.virtual_hvac.const import WindowOpenBehavior
from custom_components.virtual_hvac.control import OutputMode, Preset, VirtualMode


@pytest.mark.parametrize("preset", [Preset.BOOST, Preset.SLEEP])
@pytest.mark.parametrize("path", ["shared_heat", "primary_heat", "fan", "dry", "window_fan"])
async def test_nonthermal_ac_paths_never_enable_rapid_or_silent(hass, path, preset):
    calls = register_devices(hass)
    set_climate(hass)
    for entity_id in ("switch.rapid", "switch.silent"):
        hass.states.async_set(entity_id, "off")
    overrides = {
        "ac_entity_ids": ("climate.ac",),
        "rapid_entity_id": "switch.rapid",
        "silent_entity_id": "switch.silent",
        "boost_ac_heat_assist": False,
        "minimum_seconds_cooling_off": 0,
    }
    mode = {"fan": VirtualMode.FAN_ONLY, "dry": VirtualMode.DRY}.get(path, VirtualMode.HEAT)
    if path == "shared_heat":
        overrides.update(heater_entity_ids=(), shared_heat_request=True)
    if path == "window_fan":
        hass.states.async_set("binary_sensor.window", "on")
        overrides.update(
            window_entity_ids=("binary_sensor.window",),
            window_open_delay_minutes=0,
            window_open_behavior=WindowOpenBehavior.FALLBACK_TO_FAN_ONLY,
        )
    room = await start_room(hass, room_config(**overrides))
    try:
        await room.async_set_mode(mode)
        await room.async_set_preset(preset)
        await hass.async_block_till_done()
        assert room.physical_status == "outputs_confirmed"
        assert room.target_temperature == 22
        assert room.preset is preset
        assert hass.states.get("switch.rapid").state == "off"
        assert hass.states.get("switch.silent").state == "off"
        assert room.decision.rapid is False
        assert room.decision.silent is False
        assert not any(
            entity_id in ("switch.rapid", "switch.silent") and service == "turn_on"
            for entity_id, service in calls
        )
        if path in ("shared_heat", "primary_heat"):
            assert room.heat_demand
            assert room.decision.output_mode is OutputMode.HEAT
            assert room.decision.heater_active is (path == "primary_heat")
            assert hass.states.get("climate.ac").state == "off"
        else:
            assert not room.heat_demand
            assert hass.states.get("climate.ac").state == ("dry" if path == "dry" else "fan_only")
    finally:
        assert await room.async_stop()


@pytest.mark.parametrize(
    ("path", "preset"),
    [
        ("cool", Preset.BOOST),
        ("cool", Preset.SLEEP),
        ("ac_heat", Preset.BOOST),
        ("ac_heat", Preset.SLEEP),
        ("heat_assist", Preset.BOOST),
    ],
)
async def test_thermal_ac_paths_preserve_preset_flags_targets_and_heat_assist(hass, path, preset):
    register_devices(hass)
    set_climate(hass, target=19)
    for entity_id in ("switch.rapid", "switch.silent"):
        hass.states.async_set(entity_id, "off")
    if path == "cool":
        hass.states.async_set("sensor.winter", "28", {"unit_of_measurement": "°C"})
    room = await start_room(
        hass,
        room_config(
            ac_entity_ids=("climate.ac",),
            heater_entity_ids=("switch.heater",) if path == "heat_assist" else (),
            rapid_entity_id="switch.rapid",
            silent_entity_id="switch.silent",
            boost_ac_heat_assist=True,
            minimum_seconds_cooling_off=0,
        ),
    )
    try:
        await room.async_set_mode(VirtualMode.COOL if path == "cool" else VirtualMode.HEAT)
        await room.async_set_preset(preset)
        await hass.async_block_till_done()
        assert room.physical_status == "outputs_confirmed"
        assert room.target_temperature == 22
        assert hass.states.get("climate.ac").attributes["temperature"] == 22
        assert room.decision.rapid is (preset is Preset.BOOST)
        assert room.decision.silent is (preset is Preset.SLEEP)
        assert hass.states.get("switch.rapid").state == ("on" if preset is Preset.BOOST else "off")
        assert hass.states.get("switch.silent").state == ("on" if preset is Preset.SLEEP else "off")
        if path == "heat_assist":
            assert room.decision.output_mode is OutputMode.HEAT_ASSIST
            assert room.heat_demand
            assert hass.states.get("switch.heater").state == "on"
    finally:
        assert await room.async_stop()
