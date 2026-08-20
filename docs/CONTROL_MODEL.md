# Control Model

## Decision inputs and outputs

Each room evaluation uses:

- virtual mode, target, and preset;
- averaged current temperature;
- whether a window input is configured and its current category;
- elapsed AC off time;
- elapsed time since the room output path changed;
- previous active output and heating latch state;
- immutable room settings.

It returns exactly one output path:

- off;
- heat;
- heat assist;
- cool;
- dry;
- fan only.

Heating and cooling paths are mutually exclusive. Heat assist is still a heating path: it can use the primary heater and AC heating together only when explicitly enabled.

## Temperature aggregation

For each selected sensor:

1. Missing, unknown, unavailable, non-numeric, NaN, and infinite states are invalid.
2. Readings older than the configured maximum age are invalid.
3. Supported temperature units are converted to Celsius.
4. Valid finite fresh values are averaged arithmetically.

If no valid value remains, the room climate entity is unavailable, heat demand is false, and off outputs are requested.

The default maximum age is 300 seconds and the configured range is 1–604800 seconds. Freshness uses the source state's authoritative report time when available.

## Startup barrier

Restored mode, target, and preset are intent only. Runtime starts internally disarmed, neutralizes the shared relay and every room output, requires reported off acknowledgement, validates fresh temperature, configured window state, and all output states, then arms restored intent. Any failed neutralization or non-authoritative required input aborts setup rather than issuing an on request.

## Common interlocks

The following checks happen before mode-specific decisions:

1. No valid current temperature → off, `no_valid_temperature`.
2. Non-finite target → off, `invalid_target`.
3. Configured window input missing, unknown, or unavailable → off, `window_unavailable`.
4. Configured window open → off, `window_open`.
5. Virtual mode off → off, `mode_off`.

Rapid and silent outputs are also suppressed during these interlocks.

## Heating hysteresis

Let:

- `T` be current temperature;
- `S` be target temperature;
- `H_on` be heating start offset;
- `H_off` be heating stop offset.

When heating is inactive, demand starts when:

```text
T <= S - H_on
```

When heating is active, demand continues while:

```text
T < S + H_off
```

At or above the stop threshold, the room selects off with `heat_target_satisfied`.

Heating requests the primary heater. A heating climate output receives target plus TRV offset. Boost can select heat assist when AC heat assist is enabled and an AC output exists.

## Cooling hysteresis

Cooling hysteresis applies to automatic mode.

Let `C_on` be cooling start offset and `C_off` be cooling stop offset. Cooling starts when:

```text
T >= S + C_on
```

Once active, cooling continues while:

```text
T > S - C_off
```

At or below the cooling stop boundary, automatic mode falls into the dead band unless heating is required.

Explicit cool mode requests cooling continuously after common interlocks and protection timers pass. It does not cycle on room hysteresis; the physical AC remains responsible for its own target regulation.

## Automatic mode

Automatic mode follows this order:

1. Continue the prior heating path while below its heating stop threshold.
2. Continue the prior cooling path while above its cooling stop threshold.
3. If at or below the heating start threshold, request heat.
4. Otherwise, if at or above the cooling start threshold, request cool.
5. Otherwise select off with `auto_dead_band`.

A heat/cool reversal and a cooling start may be delayed by the protection rules below.

## Other modes

| Virtual mode | Effective behavior |
|---|---|
| Off | AC off, heater off, preset switches off, no heat demand. |
| Heat | Hysteresis-controlled primary heating; optional boost heat assist. |
| Cool | AC cool with the virtual target after protection timers. |
| Dry | AC dry after protection timers; target is not forced. |
| Fan only | Heater off and AC fan-only; target is not forced. |
| Auto | Hysteresis-controlled heat, dead band, and cool. |

Only modes supported by configured and currently reported physical outputs are exposed. Automatic mode requires both heating and cooling availability.

## Presets

| Preset | Rapid switch | Silent switch | Other effect |
|---|---:|---:|---|
| Comfort | Off | Off | Normal operation. |
| Boost | On while active | Off | May use AC heat assist when explicitly enabled. |
| Sleep | Off | On while active | No other vendor-specific changes. |

## Room protection timers

### AC minimum-off

Before cool or dry starts, the AC must have reported off for at least the configured minimum-off interval. If not, output remains off with `ac_minimum_off` and a retry is scheduled for the rounded-up remaining seconds.

Elapsed time comes from an integration-owned wall-clock timestamp recorded after a confirmed AC transition to off. It is persisted privately across restart. Missing, corrupt, or future timestamps yield zero elapsed time and therefore the full conservative delay. Missing, unknown, or unavailable AC state also prevents startup arming or yields conservative delay.

### Heat/cool reversal guard

A transition from a prior heating path to cool or dry, or from a prior cooling path to heat, remains off with `mode_reversal_guard` until the configured interval has elapsed. Confirmed room output-path transitions are recorded in the same durable private wall-clock store. Missing, corrupt, or future values apply the full guard.

### Window close

Normal evaluation resumes as soon as explicit closed is reported. There is no separate configurable post-close interval in the current release.

## Actuation result and effective demand

The adapter first requests the opposite path off and requires Home Assistant state acknowledgement, then requests the selected path with another bounded acknowledgement. If a required path is unavailable, unsupported, times out, or does not converge, runtime changes the effective decision to off, clears heat demand, records a known failure reason, and attempts acknowledged neutralization.

Reported state acknowledgement is stronger than service-call completion but still does not prove mechanical movement, valve travel, or safe hydraulic flow.

## Shared heat-source arbitration

Global heat demand is logical OR across effective room heat demands.

If no shared relay is configured, the aggregate demand remains available for an external controller and shared status is `not_configured`.

With a shared relay:

- demand true and relay already on → no call, `steady_on`;
- demand false and relay already off → no call, `steady_off`;
- on request during minimum-off → no call, `minimum_off`, then retry;
- off request during minimum-on → no call, `minimum_on`, then retry;
- eligible on request → `turn_on`;
- eligible off request → `turn_off`;
- relay missing, unknown, or unavailable → no call, `relay_unavailable`;
- service timeout or missing state acknowledgement → `command_not_confirmed`.

Minimum timing uses an integration-owned timestamp recorded after confirmed relay transitions and persisted privately across restart. Missing, corrupt, or future values apply the full guard. If the unreachable relay was already physically on, `relay_unavailable` cannot make it physically safe.

## Status reason reference

Common room status values include:

- normal selection: `explicit_cool`, `explicit_dry`, `explicit_fan_only`, `heat_demand`, `auto_heat`, `auto_cool`, `auto_continue_heat`, `auto_continue_cool`;
- satisfied or idle: `mode_off`, `heat_target_satisfied`, `auto_dead_band`;
- startup, shutdown, or interlock: `startup_disarmed`, `startup_inputs_not_authoritative`, `startup_neutralization_failed`, `shutdown_neutralized`, `no_valid_temperature`, `invalid_target`, `window_open`, `window_unavailable`, `ac_minimum_off`, `mode_reversal_guard`;
- output fault: `ac_stop_not_confirmed`, `ac_stop_or_start_not_confirmed`, `heater_start_not_confirmed`, `heater_stop_not_confirmed`, `neutralization_not_confirmed`, `preset_output_not_confirmed`, `stale_command_neutralization_failed`, `service_call_failed`.

Unknown arbitrary text is not included in downloadable diagnostics.
