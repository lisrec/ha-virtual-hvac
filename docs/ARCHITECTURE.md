# Architecture

## Scope

Virtual HVAC coordinates room-level HVAC decisions inside Home Assistant. A singleton controller config entry owns global arbitration. Each room is a config subentry with its own inputs, settings, runtime, and entities.

The integration is an orchestration layer. It does not replace equipment safety controls, prove actuator movement, or create a physical fail-safe.

## Component model

```text
Controller config entry
├── controller configuration
├── ControllerRuntime
│   ├── room runtime collection
│   ├── aggregate heat-demand calculation
│   └── optional shared heat-source arbiter
├── aggregate heat-demand binary sensor
├── shared heat-source status sensor
└── room config subentries
    ├── room configuration
    ├── RoomRuntime
    │   ├── temperature aggregation
    │   ├── deterministic RoomController
    │   ├── ActuatorAdapter
    │   └── state listeners and retry timer
    ├── virtual climate entity
    ├── heat-demand binary sensor
    └── controller-status sensor
```

Home Assistant associates room entities and room devices with their owning config subentry. Unique IDs derive from opaque Home Assistant entry and subentry identifiers rather than names or physical entity identifiers.

## Separation of concerns

### Immutable configuration

`ControllerConfig` and `RoomConfig` validate stored data and expose immutable runtime settings. Config flows provide selectors and bounded numeric fields.

### Pure decisions

`RoomController` and the shared heat-source decision function have no Home Assistant side effects. Given inputs and compact memory, they return an output mode, demand, reason, and optional retry delay.

### Runtime orchestration

`RoomRuntime` reads Home Assistant states, converts temperature units, serializes evaluation with an asynchronous lock, schedules protection retries, calls the adapter, and notifies entities. `ControllerRuntime` owns a separate lock and retry timer for the shared source.

### Side-effect adapter

`ActuatorAdapter` converts a decision to bounded Home Assistant service calls and requires reported state acknowledgement. It avoids calls when reported state already matches the request. For a cooling path, it confirms the heater off before requesting the AC mode. For every heating path, including boost heat assist, it first confirms AC off, then enables the primary heater, and only then may request AC heat. A failed heat-assist request triggers fail-closed neutralization and is never reported as successful.

A reported Home Assistant state acknowledgement does not prove physical movement. Deployment must provide independent mechanical acknowledgement or physical protection where required.

## Ownership model

A room can own:

- one optional AC climate output;
- one optional heater, TRV, or heating switch;
- one optional rapid-mode switch;
- one optional silent-mode switch.

The controller alone can own the optional shared heat-demand relay. Configuration rejects duplicate output assignment within Virtual HVAC, including attempts to reuse the shared relay as a room output.

This is **integration-local ownership**, not system-wide exclusivity. Home Assistant automations, scripts, dashboards, voice assistants, other integrations, external controllers, and manual commands remain possible competing writers. System-wide exclusive ownership is a commissioning requirement.

## Data flow

1. A source state, virtual setting, or protection timer requests room evaluation.
2. Fresh temperature readings are normalized to Celsius and valid finite values are averaged.
3. Window state, virtual mode, target, preset, elapsed times, and control memory form a `ControlInput`.
4. The pure controller returns one mutually exclusive room decision.
5. The adapter applies that decision in a conservative order.
6. If required actuation cannot be requested, runtime clears effective demand and requests off outputs.
7. Room entities publish the effective decision and reason.
8. The controller computes logical OR over room heat demand.
9. The shared arbiter applies minimum-on or minimum-off timing and may request the optional relay.

## Startup, restoration, and shutdown

### Startup

Room listeners are registered before entities are forwarded. Each virtual climate entity restores only intent: its last valid mode, target, and preset. Runtime remains internally disarmed. The startup barrier then neutralizes the shared relay and every room output, requires off acknowledgement, validates fresh temperature, window state, and output states, and only then arms restored intent. Setup fails if the barrier cannot establish a known neutral state.

This internal startup barrier is not a user-selectable shadow mode. During first commissioning, keep physical outputs independently isolated until startup and restored intent have been observed safely.

### Reconfiguration

Controller or room updates reload the parent config entry. Reconfiguration can therefore cause immediate reconciliation. Back up first and keep the installation in a safe state while changing ownership.

### Unload

Unload first requests neutral state for the shared relay and every room output and requires Home Assistant state acknowledgement. If any reachable output cannot confirm off, runtime refuses cleanup so ownership is not silently abandoned. After successful neutralization it removes entity platforms, listeners, delayed callbacks, and flushes protection timestamps. An already unreachable active device still requires physical intervention.

## Logical demand fail-closed versus physical fail-safe

Logical fail-closed behavior means the integration clears effective heat demand and avoids an on request when required information or an output path is invalid. It does not mean an unreachable active relay, valve, heater, or AC becomes physically off.

Physical fail-safe behavior must come from installation design:

- normally-open low-voltage heat-demand contacts;
- de-energize-to-stop wiring where supported;
- manufacturer controls and independent limits retained;
- safe hydraulic flow, including bypass where required;
- suitable contact ratings and electrical isolation;
- a tested manual shutdown path.

The shared output must be a heat-demand input only. It must never interrupt boiler mains, burner power, pump power, or domestic-hot-water power.

## TRV and hydraulic readiness

A room publishes heat demand only after heating mode and target are acknowledged in Home Assistant. The integration still does not confirm valve travel, valve position, end-switch state, or minimum system flow. Consequently, a shared source must not depend solely on a TRV state acknowledgement unless the installation separately guarantees readiness and safe circulation.

Use a manufacturer-approved bypass, a permanently available circuit, a proven valve-ready interlock, or another professionally designed solution appropriate to the system.

## Protection timing

Confirmed integration-owned shared-relay, AC, and room output-path transitions are recorded as wall-clock timestamps in private Home Assistant storage. The records survive restart. Missing, corrupt, or future timestamps produce zero elapsed time, which conservatively applies the full guard interval. Startup records fresh off baselines before restored intent is armed. External writers are unsupported and may invalidate timer assumptions, so every physical output must remain exclusively owned by Virtual HVAC while it is active.

See [CONTROL_MODEL.md](CONTROL_MODEL.md) for exact threshold and timer behavior.

## Privacy boundary

The integration has no cloud dependency, telemetry, credentials, or remote API. Runtime configuration necessarily contains user-selected names and physical entity identifiers inside Home Assistant. Downloadable diagnostics do not expose them. Unknown runtime text is replaced with `unrecognized` rather than copied into diagnostics.

See [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).
