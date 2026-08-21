from __future__ import annotations

from custom_components.virtual_hvac.const import WindowOpenBehavior
from custom_components.virtual_hvac.control import (
    ControlInput,
    ControlMemory,
    OutputMode,
    Preset,
    RoomController,
    VirtualMode,
)
from custom_components.virtual_hvac.models import RoomConfig


def room_config(**overrides: object) -> RoomConfig:
    values: dict[str, object] = {
        "name": "Room",
        "temperature_sensor_entity_ids": ("sensor.temperature",),
        "ac_entity_ids": ("climate.ac",),
        "heater_entity_ids": ("climate.trv",),
        "enable_safe_cooling_delay": True,
    }
    values.update(overrides)
    return RoomConfig(**values)  # type: ignore[arg-type]


def decide(
    mode: VirtualMode,
    temperature: float | None = 21.0,
    target: float = 22.0,
    *,
    memory: ControlMemory | None = None,
    window_open: bool | None = False,
    window_configured: bool = False,
    ac_off_elapsed: float = 1000,
    ac_on_elapsed: float = float("inf"),
    mode_elapsed: float = 1000,
    preset: Preset = Preset.COMFORT,
    config: RoomConfig | None = None,
):
    controller = RoomController(config or room_config())
    return controller.decide(
        ControlInput(
            mode=mode,
            preset=preset,
            target_temperature=target,
            current_temperature=temperature,
            window_configured=window_configured,
            window_open=window_open,
            ac_off_elapsed_seconds=ac_off_elapsed,
            mode_elapsed_seconds=mode_elapsed,
            ac_on_elapsed_seconds=ac_on_elapsed,
        ),
        memory or ControlMemory(),
    )


def test_off_disables_all_outputs() -> None:
    result = decide(VirtualMode.OFF)
    assert result.output_mode is OutputMode.OFF
    assert result.heat_demand is False
    assert result.heater_active is False


def test_missing_temperature_fails_closed() -> None:
    result = decide(VirtualMode.HEAT, temperature=None)
    assert result.output_mode is OutputMode.OFF
    assert result.heat_demand is False
    assert result.reason == "no_valid_temperature"


def test_open_window_fails_closed() -> None:
    result = decide(VirtualMode.COOL, window_configured=True, window_open=True)
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "window_open"


def test_open_window_falls_back_to_fan_only() -> None:
    result = decide(
        VirtualMode.COOL,
        window_configured=True,
        window_open=True,
        config=room_config(
            window_open_behavior=WindowOpenBehavior.FALLBACK_TO_FAN_ONLY,
        ),
    )
    assert result.output_mode is OutputMode.FAN_ONLY
    assert result.heat_demand is False
    assert result.reason == "window_open_fan_only"


def test_open_window_can_be_ignored() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.5,
        window_configured=True,
        window_open=True,
        config=room_config(
            window_open_behavior=WindowOpenBehavior.IGNORE_OPEN_WINDOW,
        ),
    )
    assert result.output_mode is OutputMode.COOL


def test_open_window_fallback_does_not_override_explicit_off() -> None:
    result = decide(
        VirtualMode.OFF,
        window_configured=True,
        window_open=True,
        config=room_config(
            window_open_behavior=WindowOpenBehavior.FALLBACK_TO_FAN_ONLY,
        ),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "mode_off"


def test_unavailable_configured_window_fails_closed() -> None:
    result = decide(VirtualMode.HEAT, window_configured=True, window_open=None)
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "window_unavailable"


def test_explicit_cool_stops_at_lower_hysteresis() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=21.5,
        target=22.0,
        memory=ControlMemory(last_output_mode=OutputMode.COOL, cooling_active=True),
        config=room_config(
            cooling_hysteresis_on=0.5,
            cooling_hysteresis_off=0.5,
            minimum_seconds_cooling_on=0,
        ),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "cool_target_satisfied"


def test_explicit_cool_stays_on_inside_dead_band() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.0,
        target=22.0,
        memory=ControlMemory(last_output_mode=OutputMode.COOL, cooling_active=True),
        config=room_config(
            cooling_hysteresis_on=0.5,
            cooling_hysteresis_off=0.5,
        ),
    )
    assert result.output_mode is OutputMode.COOL


def test_explicit_cool_waits_until_upper_hysteresis() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.49,
        target=22.0,
        config=room_config(
            cooling_hysteresis_on=0.5,
            cooling_hysteresis_off=0.5,
        ),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "cool_target_satisfied"


def test_explicit_cool_starts_at_upper_hysteresis() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.5,
        target=22.0,
        config=room_config(
            cooling_hysteresis_on=0.5,
            cooling_hysteresis_off=0.5,
        ),
    )
    assert result.output_mode is OutputMode.COOL
    assert result.reason == "explicit_cool"


def test_explicit_cool_does_not_restart_inside_dead_band() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.0,
        target=22.0,
        memory=ControlMemory(last_output_mode=OutputMode.COOL, cooling_active=False),
        config=room_config(
            cooling_hysteresis_on=0.5,
            cooling_hysteresis_off=0.5,
        ),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "cool_target_satisfied"


def test_explicit_cool_obeys_minimum_on_before_stop() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=21.5,
        target=22.0,
        ac_on_elapsed=60,
        memory=ControlMemory(cooling_active=True),
        config=room_config(
            cooling_hysteresis_on=0.5,
            cooling_hysteresis_off=0.5,
            minimum_seconds_cooling_on=300,
        ),
    )
    assert result.output_mode is OutputMode.COOL
    assert result.reason == "ac_minimum_on"
    assert result.retry_after_seconds == 240


def test_explicit_cool_obeys_compressor_minimum_off() -> None:
    result = decide(VirtualMode.COOL, temperature=22.5, ac_off_elapsed=60)
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "ac_minimum_off"


def test_explicit_dry_obeys_compressor_minimum_off() -> None:
    result = decide(VirtualMode.DRY, ac_off_elapsed=60)
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "ac_minimum_off"


def test_explicit_cool_obeys_heat_to_cool_reversal_guard() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.5,
        mode_elapsed=60,
        memory=ControlMemory(last_output_mode=OutputMode.HEAT),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "mode_reversal_guard"


def test_explicit_cool_does_not_guard_when_below_start_threshold() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=21.0,
        mode_elapsed=60,
        memory=ControlMemory(last_output_mode=OutputMode.HEAT),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "cool_target_satisfied"


def test_explicit_heat_obeys_cool_to_heat_reversal_guard() -> None:
    result = decide(
        VirtualMode.HEAT,
        temperature=20.0,
        mode_elapsed=60,
        memory=ControlMemory(last_output_mode=OutputMode.COOL),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "mode_reversal_guard"


def test_heat_starts_below_lower_hysteresis() -> None:
    result = decide(VirtualMode.HEAT, temperature=21.4, target=22.0)
    assert result.output_mode is OutputMode.HEAT
    assert result.heat_demand is True
    assert result.heater_active is True


def test_heat_stays_on_until_upper_hysteresis_is_reached() -> None:
    result = decide(
        VirtualMode.HEAT,
        temperature=22.2,
        target=22.0,
        memory=ControlMemory(last_output_mode=OutputMode.HEAT, heating_active=True),
    )
    assert result.heat_demand is True


def test_heat_stops_at_upper_hysteresis() -> None:
    result = decide(
        VirtualMode.HEAT,
        temperature=22.5,
        target=22.0,
        memory=ControlMemory(last_output_mode=OutputMode.HEAT, heating_active=True),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.heat_demand is False


def test_auto_enters_cooling_above_upper_threshold() -> None:
    result = decide(VirtualMode.AUTO, temperature=22.6, target=22.0)
    assert result.output_mode is OutputMode.COOL


def test_auto_stays_idle_inside_dead_band() -> None:
    result = decide(VirtualMode.AUTO, temperature=22.0, target=22.0)
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "auto_dead_band"


def test_auto_does_not_restart_ac_before_minimum_off_time() -> None:
    result = decide(VirtualMode.AUTO, temperature=23.0, target=22.0, ac_off_elapsed=60)
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "ac_minimum_off"
    assert result.retry_after_seconds == 240


def test_disabled_safe_cooling_delay_allows_immediate_start() -> None:
    result = decide(
        VirtualMode.COOL,
        temperature=22.5,
        ac_off_elapsed=0,
        config=room_config(
            enable_safe_cooling_delay=False,
            minimum_seconds_cooling_on=300,
            minimum_seconds_cooling_off=300,
        ),
    )
    assert result.output_mode is OutputMode.COOL


def test_auto_keeps_cooling_until_minimum_on_time() -> None:
    config = room_config(
        enable_safe_cooling_delay=True,
        minimum_seconds_cooling_on=300,
        minimum_seconds_cooling_off=300,
    )
    controller = RoomController(config)
    result = controller.decide(
        ControlInput(
            mode=VirtualMode.AUTO,
            preset=Preset.COMFORT,
            target_temperature=22.0,
            current_temperature=21.6,
            window_configured=False,
            window_open=False,
            ac_off_elapsed_seconds=0,
            ac_on_elapsed_seconds=60,
            mode_elapsed_seconds=1000,
        ),
        ControlMemory(last_output_mode=OutputMode.COOL),
    )
    assert result.output_mode is OutputMode.COOL
    assert result.reason == "ac_minimum_on"
    assert result.retry_after_seconds == 240


def test_auto_does_not_reverse_heat_to_cool_inside_guard() -> None:
    result = decide(
        VirtualMode.AUTO,
        temperature=23.0,
        target=22.0,
        mode_elapsed=60,
        memory=ControlMemory(last_output_mode=OutputMode.HEAT),
    )
    assert result.output_mode is OutputMode.OFF
    assert result.reason == "mode_reversal_guard"


def test_boost_requests_rapid_and_optional_ac_heat_assist() -> None:
    result = decide(
        VirtualMode.HEAT,
        temperature=20.0,
        target=22.0,
        preset=Preset.BOOST,
        config=room_config(boost_ac_heat_assist=True),
    )
    assert result.output_mode is OutputMode.HEAT_ASSIST
    assert result.rapid is True
    assert result.silent is False


def test_sleep_requests_silent_without_rapid() -> None:
    result = decide(VirtualMode.COOL, preset=Preset.SLEEP)
    assert result.rapid is False
    assert result.silent is True


def test_no_decision_requests_heating_and_cooling_together() -> None:
    for mode in VirtualMode:
        for temperature in (18.0, 22.0, 26.0):
            result = decide(mode, temperature=temperature)
            assert not (result.heat_demand and result.output_mode is OutputMode.COOL)
