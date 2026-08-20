from __future__ import annotations

import pytest

from custom_components.virtual_hvac.models import (
    ControllerConfig,
    RoomConfig,
    validate_output_ownership,
)


def test_controller_defaults_are_safe() -> None:
    config = ControllerConfig(name="Virtual HVAC")
    assert config.shared_heat_source_entity_id is None
    assert config.shared_minimum_on_seconds == 300
    assert config.shared_minimum_off_seconds == 180


def test_room_requires_at_least_one_temperature_sensor() -> None:
    with pytest.raises(ValueError, match="temperature sensor"):
        RoomConfig(name="Room", temperature_sensor_entity_ids=(), ac_entity_id="climate.ac")


def test_room_requires_at_least_one_actuator() -> None:
    with pytest.raises(ValueError, match="actuator"):
        RoomConfig(name="Room", temperature_sensor_entity_ids=("sensor.temperature",))


def test_room_rejects_same_ac_and_heater() -> None:
    with pytest.raises(ValueError, match="distinct"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_id="climate.shared",
            heater_entity_id="climate.shared",
        )


def test_room_rejects_collision_between_any_output_roles() -> None:
    with pytest.raises(ValueError, match="output roles must be distinct"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            ac_entity_id="climate.ac",
            heater_entity_id="climate.trv",
            rapid_entity_id="climate.ac",
        )


def test_controller_rejects_shared_output_reused_by_room() -> None:
    controller = ControllerConfig(name="Controller", shared_heat_source_entity_id="switch.shared")
    room = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.temperature",),
        heater_entity_id="climate.trv",
        silent_entity_id="switch.shared",
    )
    with pytest.raises(ValueError, match="assigned to multiple output roles"):
        validate_output_ownership(controller, {"room": room})


def test_controller_rejects_output_reused_between_rooms() -> None:
    room_one = RoomConfig(
        name="One",
        temperature_sensor_entity_ids=("sensor.one",),
        heater_entity_id="climate.shared",
    )
    room_two = RoomConfig(
        name="Two",
        temperature_sensor_entity_ids=("sensor.two",),
        ac_entity_id="climate.shared",
    )
    with pytest.raises(ValueError, match="assigned to multiple output roles"):
        validate_output_ownership(
            ControllerConfig(name="Controller"), {"one": room_one, "two": room_two}
        )


def test_temperature_freshness_accepts_bounded_and_unbounded_values() -> None:
    bounded = RoomConfig(
        name="Bounded",
        temperature_sensor_entity_ids=("sensor.temperature",),
        heater_entity_id="climate.trv",
        temperature_sensor_max_age_seconds=120,
    )
    unbounded = RoomConfig(
        name="Unbounded",
        temperature_sensor_entity_ids=("sensor.temperature",),
        heater_entity_id="climate.trv",
        temperature_sensor_max_age_seconds=None,
    )
    assert bounded.temperature_sensor_max_age_seconds == 120
    assert unbounded.temperature_sensor_max_age_seconds is None


def test_temperature_freshness_rejects_non_positive_value() -> None:
    with pytest.raises(ValueError, match="freshness"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            heater_entity_id="climate.trv",
            temperature_sensor_max_age_seconds=0,
        )


def test_room_rejects_duplicate_temperature_sensors() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.one", "sensor.one"),
            ac_entity_id="climate.ac",
        )


def test_room_rejects_unsafe_hysteresis() -> None:
    with pytest.raises(ValueError, match="hysteresis"):
        RoomConfig(
            name="Room",
            temperature_sensor_entity_ids=("sensor.temperature",),
            heater_entity_id="climate.trv",
            heating_hysteresis_on=0.0,
        )


def test_room_configuration_round_trips_through_mapping() -> None:
    expected = RoomConfig(
        name="Room",
        temperature_sensor_entity_ids=("sensor.one", "sensor.two"),
        ac_entity_id="climate.ac",
        heater_entity_id="climate.trv",
        window_entity_id="binary_sensor.window",
        rapid_entity_id="switch.rapid",
        silent_entity_id="switch.silent",
        heating_hysteresis_on=0.4,
        heating_hysteresis_off=0.3,
        cooling_hysteresis_on=0.6,
        cooling_hysteresis_off=0.4,
        ac_minimum_off_seconds=240,
        mode_reversal_guard_seconds=360,
        trv_target_offset=1.5,
        boost_ac_heat_assist=True,
    )
    assert RoomConfig.from_mapping(expected.to_mapping()) == expected
