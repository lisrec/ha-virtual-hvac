from __future__ import annotations

import pytest

from custom_components.virtual_hvac.const import WindowOpenBehavior
from custom_components.virtual_hvac.models import (
    ControllerConfig,
    RoomConfig,
    validate_output_ownership,
)


def test_controller_defaults_disable_safe_heating_delay() -> None:
    config = ControllerConfig(name="Virtual HVAC")
    assert config.shared_heat_source_entity_id is None
    assert config.enable_safe_heating_delay is False
    assert config.minimum_seconds_heating_on == 300
    assert config.minimum_seconds_heating_off == 180


def test_room_defaults_disable_safe_cooling_delay() -> None:
    config = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.temperature",),
        ac_entity_ids=("climate.ac",),
    )
    assert config.enable_safe_cooling_delay is False
    assert config.minimum_seconds_cooling_on == 300
    assert config.minimum_seconds_cooling_off == 300


def test_protection_flags_must_be_boolean() -> None:
    with pytest.raises(ValueError, match="heating delay flag"):
        ControllerConfig(name="Controller", enable_safe_heating_delay="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cooling delay flag"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            heater_entity_ids=("switch.heater",),
            enable_safe_cooling_delay="yes",  # type: ignore[arg-type]
        )


def test_room_requires_at_least_one_temperature_sensor() -> None:
    with pytest.raises(ValueError, match="temperature sensor"):
        RoomConfig(name="Room", temperature_sensor_entity_ids=(), ac_entity_ids=("climate.ac",))


def test_room_requires_at_least_one_actuator() -> None:
    with pytest.raises(ValueError, match="actuator"):
        RoomConfig(name="Room", temperature_sensor_entity_ids=("sensor.temperature",))


def test_room_rejects_same_ac_and_heater() -> None:
    with pytest.raises(ValueError, match="distinct"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_ids=("climate.shared",),
            heater_entity_ids=("climate.shared",),
        )


def test_room_rejects_collision_between_any_output_roles() -> None:
    with pytest.raises(ValueError, match="output roles must be distinct"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_ids=("climate.ac",),
            heater_entity_ids=("climate.trv",),
            rapid_entity_id="climate.ac",
        )


def test_controller_rejects_shared_output_reused_by_room() -> None:
    controller = ControllerConfig(name="Controller", shared_heat_source_entity_id="switch.shared")
    room = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.temperature",),
        heater_entity_ids=("climate.trv",),
        silent_entity_id="switch.shared",
    )
    with pytest.raises(ValueError, match="assigned to multiple output roles"):
        validate_output_ownership(controller, {"room": room})


def test_controller_rejects_output_reused_between_rooms() -> None:
    room_one = RoomConfig(
        name="One",
        temperature_sensor_entity_ids=("sensor.one",),
        heater_entity_ids=("climate.shared",),
    )
    room_two = RoomConfig(
        name="Two",
        temperature_sensor_entity_ids=("sensor.two",),
        ac_entity_ids=("climate.shared",),
    )
    with pytest.raises(ValueError, match="assigned to multiple output roles"):
        validate_output_ownership(
            ControllerConfig(name="Controller"), {"one": room_one, "two": room_two}
        )


def test_temperature_freshness_accepts_bounded_and_unbounded_values() -> None:
    bounded = RoomConfig(
        name="Bounded",
        temperature_sensor_entity_ids=("sensor.temperature",),
        heater_entity_ids=("climate.trv",),
        temperature_sensor_max_age_seconds=120,
    )
    unbounded = RoomConfig(
        name="Unbounded",
        temperature_sensor_entity_ids=("sensor.temperature",),
        heater_entity_ids=("climate.trv",),
        temperature_sensor_max_age_seconds=None,
    )
    assert bounded.temperature_sensor_max_age_seconds == 120
    assert unbounded.temperature_sensor_max_age_seconds is None


def test_temperature_freshness_rejects_non_positive_value() -> None:
    with pytest.raises(ValueError, match="freshness"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            heater_entity_ids=("climate.trv",),
            temperature_sensor_max_age_seconds=0,
        )


def test_room_rejects_duplicate_temperature_sensors() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.one", "sensor.one"),
            ac_entity_ids=("climate.ac",),
        )


def test_room_accepts_multiple_window_sensors() -> None:
    config = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.temperature",),
        ac_entity_ids=("climate.ac",),
        window_entity_ids=("binary_sensor.window_one", "binary_sensor.window_two"),
    )

    assert config.window_entity_ids == (
        "binary_sensor.window_one",
        "binary_sensor.window_two",
    )
    assert RoomConfig.from_mapping(config.to_mapping()) == config


def test_room_mapping_converts_legacy_window_sensor_to_list() -> None:
    config = RoomConfig.from_mapping(
        {
            "name": "Room",
            "temperature_sensor_entity_ids": ["sensor.temperature"],
            "ac_entity_id": "climate.ac",
            "window_entity_id": "binary_sensor.legacy_window",
        }
    )

    assert config.window_entity_ids == ("binary_sensor.legacy_window",)
    assert "window_entity_id" not in config.to_mapping()


def test_room_mapping_ignores_empty_legacy_window_sensor() -> None:
    config = RoomConfig.from_mapping(
        {
            "name": "Room",
            "temperature_sensor_entity_ids": ["sensor.temperature"],
            "ac_entity_id": "climate.ac",
            "window_entity_id": "",
        }
    )

    assert config.window_entity_ids == ()


def test_room_rejects_duplicate_window_sensors() -> None:
    with pytest.raises(ValueError, match="duplicate window"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_ids=("climate.ac",),
            window_entity_ids=("binary_sensor.window", "binary_sensor.window"),
        )


def test_room_rejects_unsafe_hysteresis() -> None:
    with pytest.raises(ValueError, match="hysteresis"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            heater_entity_ids=("climate.trv",),
            heating_hysteresis_on=0.0,
        )


def test_room_configuration_round_trips_through_mapping() -> None:
    expected = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.one", "sensor.two"),
        ac_entity_ids=("climate.ac",),
        heater_entity_ids=("climate.trv",),
        window_entity_ids=("binary_sensor.window", "binary_sensor.door"),
        rapid_entity_id="switch.rapid",
        silent_entity_id="switch.silent",
        heating_hysteresis_on=0.4,
        heating_hysteresis_off=0.3,
        cooling_hysteresis_on=0.6,
        cooling_hysteresis_off=0.4,
        enable_safe_cooling_delay=False,
        minimum_seconds_cooling_on=180,
        minimum_seconds_cooling_off=240,
        mode_reversal_guard_seconds=360,
        trv_target_offset=1.5,
        boost_ac_heat_assist=True,
        window_open_behavior=WindowOpenBehavior.FALLBACK_TO_FAN_ONLY,
    )
    assert RoomConfig.from_mapping(expected.to_mapping()) == expected


def test_room_accepts_multiple_ac_and_heater_entities() -> None:
    config = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.temperature",),
        ac_entity_ids=("climate.ac_one", "climate.ac_two"),
        heater_entity_ids=("climate.trv_one", "climate.trv_two"),
    )

    assert config.output_entity_ids() == (
        "climate.ac_one",
        "climate.ac_two",
        "climate.trv_one",
        "climate.trv_two",
    )
    assert RoomConfig.from_mapping(config.to_mapping()) == config


def test_room_rejects_duplicate_entities_inside_an_actuator_bank() -> None:
    with pytest.raises(ValueError, match="duplicate AC"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_ids=("climate.ac", "climate.ac"),
        )
    with pytest.raises(ValueError, match="duplicate heater"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            heater_entity_ids=("climate.trv", "climate.trv"),
        )


def test_room_mapping_migrates_legacy_actuator_fields() -> None:
    config = RoomConfig.from_mapping(
        {
            "name": "Room",
            "temperature_sensor_entity_ids": ["sensor.temperature"],
            "ac_entity_id": "climate.ac",
            "heater_entity_id": "climate.trv",
        }
    )

    assert config.ac_entity_ids == ("climate.ac",)
    assert config.heater_entity_ids == ("climate.trv",)
    assert "ac_entity_id" not in config.to_mapping()
    assert "heater_entity_id" not in config.to_mapping()


def test_room_rejects_invalid_window_open_behavior() -> None:
    with pytest.raises(ValueError, match="window open behavior"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_ids=("climate.ac",),
            window_open_behavior="unknown",  # type: ignore[arg-type]
        )
