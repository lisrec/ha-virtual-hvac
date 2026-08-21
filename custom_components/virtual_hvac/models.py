"""Validated immutable configuration models for Virtual HVAC."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Self

from .const import (
    LEGACY_CONF_AC_ENTITY,
    LEGACY_CONF_AC_MIN_OFF,
    LEGACY_CONF_HEATER_ENTITY,
    LEGACY_CONF_WINDOW_ENTITY,
    WindowOpenBehavior,
)


@dataclass(frozen=True, slots=True)
class ControllerConfig:
    """Global controller settings shared by every room."""

    name: str
    shared_heat_source_entity_id: str | None = None
    enable_safe_heating_delay: bool = False
    minimum_seconds_heating_on: int = 300
    minimum_seconds_heating_off: int = 180

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("controller name must not be empty")
        if not isinstance(self.enable_safe_heating_delay, bool):
            raise ValueError("heating delay flag must be boolean")
        for value in (self.minimum_seconds_heating_on, self.minimum_seconds_heating_off):
            if not 0 <= value <= 86_400:
                raise ValueError("shared protection times must be between 0 and 86400 seconds")

    def to_mapping(self) -> dict[str, Any]:
        """Return a storage-safe mapping."""
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Self:
        """Build settings from Home Assistant config-entry data."""
        values = dict(data)
        values.setdefault("enable_safe_heating_delay", False)
        if "minimum_seconds_heating_on" not in values:
            values["minimum_seconds_heating_on"] = values.pop("shared_minimum_on_seconds", 300)
        if "minimum_seconds_heating_off" not in values:
            values["minimum_seconds_heating_off"] = values.pop("shared_minimum_off_seconds", 180)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class RoomConfig:
    """Validated settings for one room subentry."""

    name: str
    temperature_sensor_entity_ids: tuple[str, ...]
    ac_entity_ids: tuple[str, ...] = ()
    heater_entity_ids: tuple[str, ...] = ()
    window_entity_ids: tuple[str, ...] = ()
    window_open_behavior: WindowOpenBehavior = WindowOpenBehavior.TURN_OFF_HVAC
    rapid_entity_id: str | None = None
    silent_entity_id: str | None = None
    heating_hysteresis_on: float = 0.5
    heating_hysteresis_off: float = 0.3
    cooling_hysteresis_on: float = 0.5
    cooling_hysteresis_off: float = 0.3
    enable_safe_cooling_delay: bool = False
    minimum_seconds_cooling_on: int = 300
    minimum_seconds_cooling_off: int = 300
    mode_reversal_guard_seconds: int = 300
    trv_target_offset: float = 1.0
    boost_ac_heat_assist: bool = False
    temperature_sensor_max_age_seconds: int | None = 300

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("room name must not be empty")
        if not isinstance(self.enable_safe_cooling_delay, bool):
            raise ValueError("cooling delay flag must be boolean")
        if not isinstance(self.window_open_behavior, WindowOpenBehavior):
            raise ValueError("window open behavior must be a supported value")
        if not self.temperature_sensor_entity_ids:
            raise ValueError("at least one temperature sensor is required")
        if len(set(self.temperature_sensor_entity_ids)) != len(self.temperature_sensor_entity_ids):
            raise ValueError("duplicate temperature sensor entities are not allowed")
        if len(set(self.ac_entity_ids)) != len(self.ac_entity_ids):
            raise ValueError("duplicate AC entities are not allowed")
        if len(set(self.heater_entity_ids)) != len(self.heater_entity_ids):
            raise ValueError("duplicate heater entities are not allowed")
        if len(set(self.window_entity_ids)) != len(self.window_entity_ids):
            raise ValueError("duplicate window sensor entities are not allowed")
        if not self.ac_entity_ids and not self.heater_entity_ids:
            raise ValueError("at least one HVAC actuator is required")
        outputs = self.output_entity_ids()
        if len(outputs) != len(set(outputs)):
            raise ValueError("all configured output roles must be distinct")
        hysteresis = (
            self.heating_hysteresis_on,
            self.heating_hysteresis_off,
            self.cooling_hysteresis_on,
            self.cooling_hysteresis_off,
        )
        if any(not 0.1 <= value <= 5.0 for value in hysteresis):
            raise ValueError("hysteresis values must be between 0.1 and 5.0 degrees")
        for value in (self.minimum_seconds_cooling_on, self.minimum_seconds_cooling_off):
            if not 0 <= value <= 86_400:
                raise ValueError("AC protection times must be between 0 and 86400 seconds")
        if not 0 <= self.mode_reversal_guard_seconds <= 86_400:
            raise ValueError("mode reversal guard must be between 0 and 86400 seconds")
        if not 0 <= self.trv_target_offset <= 5.0:
            raise ValueError("TRV target offset must be between 0 and 5.0 degrees")
        if (
            self.temperature_sensor_max_age_seconds is not None
            and not 1 <= self.temperature_sensor_max_age_seconds <= 604_800
        ):
            raise ValueError("temperature sensor freshness must be between 1 and 604800 seconds")

    def output_entity_ids(self) -> tuple[str, ...]:
        """Return every physical entity written by this room."""
        return tuple(
            entity_id
            for entity_id in (
                *self.ac_entity_ids,
                *self.heater_entity_ids,
                self.rapid_entity_id,
                self.silent_entity_id,
            )
            if entity_id is not None
        )

    def to_mapping(self) -> dict[str, Any]:
        """Return a storage-safe mapping."""
        values = asdict(self)
        for key in (
            "temperature_sensor_entity_ids",
            "ac_entity_ids",
            "heater_entity_ids",
            "window_entity_ids",
        ):
            values[key] = list(values[key])
        values["window_open_behavior"] = self.window_open_behavior.value
        return values

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Self:
        """Build settings from Home Assistant config-subentry data."""
        values = dict(data)
        values["temperature_sensor_entity_ids"] = tuple(values["temperature_sensor_entity_ids"])

        for canonical, legacy in (
            ("ac_entity_ids", LEGACY_CONF_AC_ENTITY),
            ("heater_entity_ids", LEGACY_CONF_HEATER_ENTITY),
        ):
            legacy_value = values.pop(legacy, None)
            if canonical in values:
                values[canonical] = tuple(values[canonical])
            elif legacy_value not in (None, ""):
                values[canonical] = (legacy_value,)
            else:
                values[canonical] = ()

        legacy_window = values.pop(LEGACY_CONF_WINDOW_ENTITY, None)
        if "window_entity_ids" in values:
            values["window_entity_ids"] = tuple(values["window_entity_ids"])
        elif legacy_window not in (None, ""):
            values["window_entity_ids"] = (legacy_window,)
        else:
            values["window_entity_ids"] = ()

        values.setdefault("enable_safe_cooling_delay", False)
        legacy_minimum_off = values.pop(LEGACY_CONF_AC_MIN_OFF, 300)
        values.setdefault("minimum_seconds_cooling_on", legacy_minimum_off)
        values.setdefault("minimum_seconds_cooling_off", legacy_minimum_off)
        raw_window_behavior = values.get("window_open_behavior", WindowOpenBehavior.TURN_OFF_HVAC)
        try:
            values["window_open_behavior"] = WindowOpenBehavior(raw_window_behavior)
        except (TypeError, ValueError) as err:
            raise ValueError("window open behavior must be a supported value") from err
        return cls(**values)


def validate_output_ownership(controller: ControllerConfig, rooms: dict[str, RoomConfig]) -> None:
    """Reject duplicate writers across the shared source and every room output role."""
    owners: dict[str, str] = {}
    shared = controller.shared_heat_source_entity_id
    if shared is not None:
        owners[shared] = "controller.shared_heat_source"
    for subentry_id, room in rooms.items():
        for entity_id in room.output_entity_ids():
            if previous := owners.get(entity_id):
                raise ValueError(
                    f"{entity_id} is assigned to multiple output roles: "
                    f"{previous} and room.{subentry_id}"
                )
            owners[entity_id] = f"room.{subentry_id}"
