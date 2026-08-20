# Virtual HVAC

Virtual HVAC is a local Home Assistant custom integration that presents one virtual thermostat per room and coordinates an optional shared heat-demand relay. One controller config entry owns global settings; room config subentries own temperature inputs and room-level outputs.

> [!CAUTION]
> This project is beta software, not a certified safety control. Read the [security and safety guidance](docs/SECURITY_AND_PRIVACY.md) and follow the [staged migration procedure](docs/MIGRATION.md) before connecting physical equipment.

## What it provides

- One controller entry with independently managed room subentries.
- One virtual climate entity, one heat-demand binary sensor, and one controller-status sensor per room.
- Controller-level aggregate heat demand and shared heat-source status entities.
- Temperature averaging with unit conversion and bounded reading freshness.
- Heating and cooling hysteresis in automatic mode.
- Window interlock, compressor minimum-off timing, and heat/cool reversal timing.
- Comfort, boost, and sleep presets.
- Optional centralized control of a shared heat-demand relay.
- Local operation with no cloud service, telemetry, credentials, or remote API.
- Mandatory-redacted downloadable diagnostics.

## Safety boundary

Virtual HVAC **fails closed logically**: invalid temperature, invalid target, an open or unavailable configured window input, or an unavailable required output suppresses effective room demand and attempts to select an off state. This is not the same as a physical fail-safe.

If an output is already energized and Home Assistant cannot reach it, software cannot guarantee that it turns off. Use only a **normally-open, low-voltage heat-demand relay** for the optional shared source. Never switch boiler mains power, burner power, pump power, or domestic-hot-water power with that output. Preserve all manufacturer controls, high-limit protection, frost protection, and independent emergency shutdowns.

TRV installations require particular care. A command sent to a valve does not prove that the valve is open or that adequate flow exists. The current integration does not verify valve position before asserting shared demand. Provide a manufacturer-approved hydraulic bypass or another guaranteed safe flow path where required, and do not connect the shared relay until actuator readiness is independently assured.

Virtual HVAC enforces one writer only **inside this integration**. Automations, scripts, dashboards, voice assistants, other integrations, and manual physical controls can still compete. Remove or disable every competing writer before cutover.

## Current release limitations

- Startup includes an internal disarmed barrier: outputs are neutralized and current inputs must be authoritative before restored intent is armed. There is no user-selectable long-running shadow mode, so use non-physical test outputs for shadow validation.
- Confirmed physical transition times for the shared relay, AC, and room output path are stored as integration-owned wall-clock timestamps. Missing, corrupt, or future values are treated conservatively as just changed.
- Output service calls are bounded and require Home Assistant state acknowledgement. Reported state convergence still does not prove mechanical movement or safe hydraulic flow.
- Unload first neutralizes reachable outputs and refuses to complete if off acknowledgement fails. This cannot make already unreachable hardware physically safe.
- TRV mode and target acknowledgement does not confirm valve travel, an open flow path, or minimum circulation.

These limitations make staged commissioning and independent safety controls mandatory.

## Requirements

- Home Assistant 2026.8 or newer.
- One or more temperature sensors for each room.
- At least one room heating or cooling output.
- Exclusive output ownership during operation.
- Suitable physical interlocks and a safe HVAC installation.

## Installation

This project has not published a stable release. For development or controlled evaluation:

1. Back up Home Assistant.
2. Copy the `virtual_hvac` integration directory into the Home Assistant `custom_components` directory.
3. Restart Home Assistant.
4. Add **Virtual HVAC** from **Settings → Devices & services**.
5. Create the controller without a physical shared relay first.
6. Follow [MIGRATION.md](docs/MIGRATION.md) before adding real outputs.

## Configuration overview

1. Create the single controller entry.
2. Leave the shared heat-demand relay unconfigured during shadow testing.
3. Add room subentries through the controller entry.
4. Validate reported modes, targets, interlocks, demand, and status with non-physical outputs.
5. Back up again, remove competing writers, and commission one physical output at a time.

See [CONFIGURATION.md](docs/CONFIGURATION.md) for every field and validation rule.

## Control behavior

The room controller selects one mutually exclusive output path: off, heat, heat assist, cool, dry, or fan only. Room heat demand is aggregated with logical OR. Only the controller-level arbiter may request the optional shared relay.

See [CONTROL_MODEL.md](docs/CONTROL_MODEL.md) for formulas, modes, presets, timers, and status reasons. See [ARCHITECTURE.md](docs/ARCHITECTURE.md) for ownership and lifecycle details.

## Diagnostics and privacy

Downloadable diagnostics intentionally omit controller and room names, all entity identifiers, config-entry and subentry identifiers, temperatures, targets, and arbitrary runtime text. They contain only configuration shape, numeric protection settings, booleans, known state categories, timer values, and known reason codes.

See [SECURITY_AND_PRIVACY.md](docs/SECURITY_AND_PRIVACY.md).

## Development and support

- Contribution workflow: [CONTRIBUTING.md](CONTRIBUTING.md)
- Security reporting: [SECURITY.md](SECURITY.md)
- Migration and rollback: [MIGRATION.md](docs/MIGRATION.md)

Licensed under the MIT License.
