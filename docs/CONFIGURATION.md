# Configuration

## Before configuration

Complete these checks before assigning any physical output:

- Back up Home Assistant and record the previous HVAC configuration.
- Confirm every selected output supports off and the modes Virtual HVAC will request.
- Remove or disable competing automations, scripts, dashboards, voice routines, external controllers, and manual workflows.
- Confirm the optional shared output is a normally-open, low-voltage heat-demand relay, never equipment mains or domestic-hot-water power.
- Confirm all manufacturer safety controls remain active.
- For TRVs, confirm valve readiness and provide a safe hydraulic bypass or equivalent flow path where required.
- Follow the shadow and rollback procedure in [MIGRATION.md](MIGRATION.md).

Only one Virtual HVAC controller entry is allowed.

## Controller settings

| Setting | Required | Default | Allowed range | Meaning |
|---|---:|---:|---:|---|
| Controller name | Yes | Virtual HVAC | Non-empty text | Display name only. It does not affect unique IDs. |
| Shared heat-demand relay | No | Not configured | Switch entity | Optional controller-owned request contact for a shared heat source. |
| Shared minimum-on time | Yes | 300 seconds | 0–86400 seconds | Minimum reported on duration before an off request. |
| Shared minimum-off time | Yes | 180 seconds | 0–86400 seconds | Minimum reported off duration before an on request. |

### Shared relay requirements

The relay must:

- be normally open;
- carry only the manufacturer-supported low-voltage heat-demand circuit;
- open when control power is lost;
- be electrically rated and installed for that demand circuit;
- remain independent from domestic-hot-water and equipment power circuits.

Do not select boiler mains, burner power, pump power, or domestic-hot-water power. If the relay is unavailable while already active, Virtual HVAC cannot guarantee physical shutdown.

Leave the relay unconfigured if another dedicated controller will consume the aggregate heat-demand sensor. That controller then owns all physical source safety and timing.

## Adding a room

Open the controller entry and add a **Room** subentry. A room requires:

- a non-empty display name;
- one or more temperature sensors;
- at least one AC or heater output.

No physical output may be reused by another room or by the controller relay. Rapid and silent switches must be different.

## Room input and output settings

| Setting | Required | Meaning |
|---|---:|---|
| Room name | Yes | Display name for the subentry and room device. |
| Temperature sensors | Yes | One or more sensors with temperature device class or a supported temperature unit. |
| Air-conditioning climate output | No | Climate entity used for supported cool, dry, fan-only, and optional heat-assist modes. |
| Heater or TRV output | No | Heating climate entity, TRV, or switch. At least one AC or heater output is required. |
| Window sensor | No | Binary sensor; open, unknown, or unavailable suppresses room outputs. |
| Rapid-mode switch | No | Switch enabled by boost while an HVAC path is active. |
| Silent-mode switch | No | Switch enabled by sleep while an HVAC path is active. |

### Temperature sensors

At setup, every selected sensor must exist and report either a temperature device class or Celsius, Fahrenheit, or Kelvin. At runtime, valid finite fresh readings are converted to Celsius and averaged. A reading older than the configured maximum age is excluded. If no valid fresh reading remains, the room stays logically unavailable and demand is off.

### AC output

The virtual climate entity exposes only AC modes currently reported by the selected physical climate entity. Automatic mode is available only when both room heating and AC cooling are available.

Verify that the AC reports off and each intended mode, and accepts target temperature where required. Dry and fan-only do not force a target.

### Heater or TRV output

A heating climate entity is requested in heat mode and receives the virtual target plus the configured TRV offset. A heating switch is simply requested on or off.

A successful request does not prove that a TRV opened. Virtual HVAC does not currently wait for position or flow acknowledgement before publishing heat demand. Do not use the optional shared relay unless a separate design guarantees safe readiness and circulation.

### Window sensor

Open suppresses heating and cooling. Unknown, unavailable, missing, or any state other than explicit closed also suppresses outputs. After explicit closed is reported, normal evaluation resumes immediately; the current release has no configurable close-delay field.

## Room control settings

| Setting | Default | Allowed range | Meaning |
|---|---:|---:|---|
| Heating start offset | 0.5 degrees | 0.1–5.0 | Start heating at or below target minus this value. |
| Heating stop offset | 0.3 degrees | 0.1–5.0 | Continue heating until temperature reaches target plus this value. |
| Cooling start offset | 0.5 degrees | 0.1–5.0 | In automatic mode, start cooling at or above target plus this value. |
| Cooling stop offset | 0.3 degrees | 0.1–5.0 | In automatic mode, continue cooling until temperature reaches target minus this value. |
| Air conditioner minimum-off time | 300 seconds | 0–86400 seconds | Delay cooling or dry startup after the AC reports off. |
| Heat/cool reversal guard | 300 seconds | 0–86400 seconds | Delay a transition between heating and cooling paths. |
| TRV target offset | 1.0 degrees | 0–5.0 | Added to the virtual target sent to a heating climate entity. |
| Allow AC heat assist in boost | Off | Boolean | Allows boost to request AC heat alongside the primary heater. |
| Temperature reading maximum age | 300 seconds | 1–604800 seconds | Excludes readings older than this interval. |

The virtual target range is 5–35 degrees with a 0.5-degree step.

## Presets

- **Comfort:** rapid and silent switches off.
- **Boost:** rapid switch on while active; may enable AC heat assist when explicitly configured.
- **Sleep:** silent switch on and rapid switch off while active.

Preset switches are requested off whenever the selected HVAC output path is off.

## Reconfiguration and removal

Reconfiguring a controller or room reloads the parent entry. Before changing output ownership:

1. Set every virtual room to off.
2. Verify every physical output is off.
3. Back up Home Assistant.
4. Apply the change.
5. Verify current source states before selecting an active mode.

Before removing a room or the integration, use the same shutdown checks. Unload requests every output off and refuses cleanup when Home Assistant state acknowledgement fails. This acknowledgement does not prove mechanical shutdown, and unreachable hardware still requires physical verification.

## Validation failures

- **Invalid controller configuration:** a name or protection time is outside its accepted form or range.
- **Invalid room configuration:** required inputs or outputs are missing, duplicated, equal where separation is required, or outside accepted ranges.
- **Invalid temperature sensor:** a selected source is missing or lacks a supported temperature classification.
- **Output already assigned:** an output is already owned elsewhere in Virtual HVAC, including the shared relay.

See [CONTROL_MODEL.md](CONTROL_MODEL.md) for exact behavior after configuration.
