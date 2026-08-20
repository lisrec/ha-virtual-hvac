from __future__ import annotations

import json
import re
from pathlib import Path
from types import MappingProxyType

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.virtual_hvac.const import DOMAIN, SUBENTRY_ROOM
from custom_components.virtual_hvac.control import ControlDecision, OutputMode, Preset, VirtualMode
from custom_components.virtual_hvac.diagnostics import async_get_config_entry_diagnostics
from custom_components.virtual_hvac.models import ControllerConfig, RoomConfig
from custom_components.virtual_hvac.runtime import ControllerRuntime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/ARCHITECTURE.md",
    "docs/CONFIGURATION.md",
    "docs/CONTROL_MODEL.md",
    "docs/SECURITY_AND_PRIVACY.md",
    "docs/MIGRATION.md",
    "custom_components/virtual_hvac/strings.json",
    "custom_components/virtual_hvac/translations/en.json",
)


def _private_entry() -> tuple[MockConfigEntry, RoomConfig, dict[str, str]]:
    private_values = {
        "controller": "Private controller name",
        "room": "Private room name",
        "sensor": "sensor.private_temperature",
        "ac": "climate.private_ac",
        "heater": "climate.private_heater",
        "window_one": "binary_sensor.private_window_one",
        "window_two": "binary_sensor.private_window_two",
        "rapid": "switch.private_rapid",
        "silent": "switch.private_silent",
        "shared": "switch.private_heat_source",
    }
    room_data = {
        "name": private_values["room"],
        "temperature_sensor_entity_ids": [private_values["sensor"]],
        "ac_entity_id": private_values["ac"],
        "heater_entity_id": private_values["heater"],
        "window_entity_ids": [private_values["window_one"], private_values["window_two"]],
        "rapid_entity_id": private_values["rapid"],
        "silent_entity_id": private_values["silent"],
        "heating_hysteresis_on": 0.5,
        "heating_hysteresis_off": 0.3,
        "cooling_hysteresis_on": 0.6,
        "cooling_hysteresis_off": 0.4,
        "enable_safe_cooling_delay": True,
        "minimum_seconds_cooling_on": 300,
        "minimum_seconds_cooling_off": 300,
        "mode_reversal_guard_seconds": 240,
        "trv_target_offset": 1.0,
        "boost_ac_heat_assist": False,
    }
    room = ConfigSubentry(
        data=MappingProxyType(room_data),
        subentry_type=SUBENTRY_ROOM,
        title=private_values["room"],
        unique_id="opaque-room-id",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=private_values["controller"],
        data={
            "name": private_values["controller"],
            "shared_heat_source_entity_id": private_values["shared"],
            "enable_safe_heating_delay": True,
            "minimum_seconds_heating_on": 300,
            "minimum_seconds_heating_off": 180,
        },
        subentries_data=[room.as_dict()],
    )
    return entry, RoomConfig.from_mapping(room_data), private_values


@pytest.mark.asyncio
async def test_diagnostics_redact_identifiers_but_expose_safe_structure(hass) -> None:
    entry, room_config, private_values = _private_entry()
    subentry_id = next(iter(entry.subentries))
    runtime = ControllerRuntime(
        hass,
        entry.entry_id,
        config=ControllerConfig.from_mapping(dict(entry.data)),
        rooms={subentry_id: room_config},
    )
    runtime.shared_status = "service_call_failed"
    runtime.shared_physical_status = "physical_command_failed"
    runtime._startup_complete = True
    room_runtime = next(iter(runtime.rooms.values()))
    hass.states.async_set(private_values["sensor"], "21.0")
    room_runtime._ready = True
    room_runtime.physical_status = "outputs_confirmed"
    room_runtime.mode = VirtualMode.HEAT
    room_runtime.preset = Preset.SLEEP
    room_runtime.decision = ControlDecision(
        OutputMode.HEAT,
        True,
        True,
        None,
        False,
        True,
        "heat_demand",
        17,
    )
    entry.runtime_data = runtime

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    rendered = repr(diagnostics)
    for private_value in (*private_values.values(), entry.entry_id, subentry_id):
        assert private_value not in rendered
    assert diagnostics == {
        "controller": {
            "room_count": 1,
            "shared_heat_source_configured": True,
            "enable_safe_heating_delay": True,
            "minimum_seconds_heating_on": 300,
            "minimum_seconds_heating_off": 180,
            "runtime": {
                "aggregate_heat_demand": True,
                "shared_status": "service_call_failed",
                "shared_physical_status": "physical_command_failed",
            },
        },
        "rooms": [
            {
                "temperature_sensor_count": 1,
                "window_sensor_count": 2,
                "configured_inputs": {"window": True},
                "configured_outputs": {
                    "ac": True,
                    "heater": True,
                    "rapid": True,
                    "silent": True,
                },
                "settings": {
                    "heating_hysteresis_on": 0.5,
                    "heating_hysteresis_off": 0.3,
                    "cooling_hysteresis_on": 0.6,
                    "cooling_hysteresis_off": 0.4,
                    "enable_safe_cooling_delay": True,
                    "minimum_seconds_cooling_on": 300,
                    "minimum_seconds_cooling_off": 300,
                    "mode_reversal_guard_seconds": 240,
                    "trv_target_offset": 1.0,
                    "boost_ac_heat_assist": False,
                    "temperature_sensor_max_age_seconds": 300,
                },
                "runtime": {
                    "available": True,
                    "mode": "heat",
                    "preset": "sleep",
                    "output_mode": "heat",
                    "heat_demand": True,
                    "status": "heat_demand",
                    "physical_status": "outputs_confirmed",
                    "retry_after_seconds": 17,
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_diagnostics_replace_unrecognized_runtime_text(hass) -> None:
    entry, room_config, _ = _private_entry()
    subentry_id = next(iter(entry.subentries))
    runtime = ControllerRuntime(
        hass,
        entry.entry_id,
        config=ControllerConfig.from_mapping(dict(entry.data)),
        rooms={subentry_id: room_config},
    )
    runtime.shared_status = "private-host.invalid"
    runtime.shared_physical_status = "private-controller-path"
    room_runtime = next(iter(runtime.rooms.values()))
    room_runtime.physical_status = "private-room-path"
    room_runtime.decision = ControlDecision(
        OutputMode.OFF, False, False, None, False, False, "sensor.private_temperature"
    )
    entry.runtime_data = runtime

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["controller"]["runtime"]["shared_status"] == "unrecognized"
    assert diagnostics["controller"]["runtime"]["shared_physical_status"] == "unrecognized"
    assert diagnostics["rooms"][0]["runtime"]["status"] == "unrecognized"
    assert diagnostics["rooms"][0]["runtime"]["physical_status"] == "unrecognized"
    assert "private-host.invalid" not in repr(diagnostics)
    assert "sensor.private_temperature" not in repr(diagnostics)


def test_public_documentation_and_english_translation_are_privacy_safe() -> None:
    missing = [path for path in PUBLIC_FILES if not (PROJECT_ROOT / path).is_file()]
    assert not missing, f"Missing required public files: {missing}"

    strings = json.loads((PROJECT_ROOT / "custom_components/virtual_hvac/strings.json").read_text())
    translation = json.loads(
        (PROJECT_ROOT / "custom_components/virtual_hvac/translations/en.json").read_text()
    )
    assert strings == translation

    polish_characters = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
    concrete_entity_id = re.compile(r"\b(?:binary_sensor|climate|sensor|switch)\.[a-z0-9_]+\b")
    ipv4_address = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    local_path = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\\\)")

    for relative_path in PUBLIC_FILES:
        text = (PROJECT_ROOT / relative_path).read_text()
        assert not polish_characters.search(text), relative_path
        assert not concrete_entity_id.search(text), relative_path
        assert not ipv4_address.search(text), relative_path
        assert not local_path.search(text), relative_path
        assert "lisrec" not in text.lower(), relative_path
