# Migration and Rollback

## Goal

Migrate from an existing HVAC control arrangement without allowing two controllers to write the same output and without letting restored state unexpectedly energize equipment.

Virtual HVAC has an internal startup-disarmed barrier but no user-selectable long-running shadow mode. A safe migration therefore uses non-physical test outputs for shadow observation and a separate, vendor-approved physical isolation step for first commissioning.

## Upgrade from 0.1.x to 0.2.0

Home Assistant migrates protection settings before setup:

- legacy shared minimum-on and minimum-off values become minimum heating on and off values;
- safe heating delay is enabled;
- the legacy AC minimum-off value becomes the initial minimum cooling on and off value;
- safe cooling delay is enabled;
- room identifiers, output ownership, targets, modes, and presets are unchanged.

After upgrading, review both checkboxes and all four intervals before enabling a room. Rollback to 0.1.x requires restoring a pre-upgrade Home Assistant backup because 0.1.x does not understand the new configuration keys.

## Phase 0: establish the safety boundary

Before changing Home Assistant:

1. Read [SECURITY_AND_PRIVACY.md](SECURITY_AND_PRIVACY.md).
2. Identify the equipment manufacturer's approved shutdown and commissioning procedures.
3. Confirm all independent limits, frost protection, and emergency controls remain operational.
4. Confirm the optional shared output is a normally-open, low-voltage heat-demand contact only.
5. Confirm safe hydraulic flow for every possible valve combination.
6. Define the person responsible for commissioning and rollback.

Do not proceed if any active output cannot be safely observed and manually disabled.

## Phase 1: back up and record the baseline

1. Create a full Home Assistant backup that includes configuration, automations, helpers, and entity registry data.
2. Verify that the backup is readable and record how it will be restored.
3. Record the previous controller's setpoints, schedules, protection times, and output ownership.
4. Record each physical output's normal off indication and manufacturer-approved manual shutdown method.
5. Record a short baseline of temperatures, calls for heat, equipment cycles, and window interlocks.

Keep the backup outside the device being changed when practical.

## Phase 2: inventory every writer

For each intended AC, heater, TRV, rapid switch, silent switch, and shared relay, search for all writers:

- Home Assistant automations and scripts;
- dashboards and manual controls;
- voice routines;
- other integrations and external automation tools;
- vendor schedules and applications;
- local wall controls or physical overrides.

Choose exactly one owner for each output. Virtual HVAC only prevents duplicate assignments inside itself; it cannot enforce this inventory.

Do not disable the existing production owner yet. The shadow phase must use different, non-physical outputs.

## Phase 3: create a shadow controller

1. Install Virtual HVAC while leaving all production ownership unchanged.
2. Create the controller with no shared heat-demand relay.
3. Create dedicated non-physical test switches or equivalent disposable outputs that are not connected to equipment.
4. Add shadow room subentries using real temperature and window inputs but only non-physical outputs.
5. Observe the startup barrier neutralize test outputs, validate current inputs, and arm restored intent; keep every virtual room off after startup.
6. Confirm current input availability and then exercise each intended mode and preset.
7. Compare virtual heat demand and status reasons with the existing controller over representative operating conditions.
8. Test missing temperature, unavailable window, open window, output unavailability, minimum-off timing, and reversal timing.
9. Restart Home Assistant and verify restored mode, target, preset, demand, and test-output behavior.

Because these room subentries write their selected outputs, they are safe for shadow use only when those outputs are non-physical and dedicated to the test.

## Phase 4: prepare a disarmed physical cutover

The startup barrier is automatic, not a user-operated commissioning interlock. Use a manufacturer-approved means to prevent physical actuation while preserving safety controls.

1. Set every virtual room to off and verify every shadow output is off.
2. Set the existing production controller to a documented safe state.
3. Create another full backup.
4. Apply the approved physical or vendor-level commissioning isolation.
5. Disable competing writers for the first physical output being transferred.
6. Reconfigure one room to use that physical output.
7. Allow the integration reload and startup barrier to complete. It must neutralize outputs and require current authoritative inputs before arming restored intent.
8. Verify the virtual mode is off, input states are current, output status is confirmed off, and no unexpected service request remains.
9. Restart Home Assistant while still isolated and repeat the verification.

Restored virtual intent can be reconciled during startup. Never remove physical isolation until the restored mode and every output have been checked.

## Phase 5: commission one room at a time

For each room:

1. Confirm no competing writer remains.
2. Confirm temperature, window, and actuator state are current and available.
3. Remove isolation only for the output under test.
4. Request one mode at a conservative target.
5. Observe the Home Assistant state and the physical equipment response.
6. Open or simulate the window interlock and verify physical shutdown.
7. Make the required output unavailable and verify logical demand clears; separately verify the physical device state.
8. Test the off command and manufacturer-approved manual shutdown.
9. Observe at least one complete controlled cycle before continuing.

Stop immediately if reported and physical state diverge.

## Phase 6: commission shared heat demand last

Do not connect the shared relay until every room path is proven and hydraulic readiness is assured.

1. Keep the controller's shared relay setting empty while observing aggregate demand.
2. Verify aggregate demand across all room combinations.
3. Confirm TRV or valve readiness does not depend only on a command being accepted.
4. Confirm bypass or equivalent minimum-flow protection.
5. Confirm the relay is normally open and low voltage, and does not switch equipment or domestic-hot-water power.
6. Disable the previous shared-source writer.
7. Assign the shared relay while the source is physically isolated.
8. Verify relay off, restored virtual states, minimum-on and minimum-off settings, and manual shutdown.
9. Remove isolation and test one controlled call for heat.
10. Test unavailable relay reporting and confirm the independent physical shutdown procedure.

## Acceptance checks

Migration is complete only when:

- each output has one system-wide owner;
- all virtual and physical off states agree;
- every configured window interlock works;
- sensor loss and output loss clear logical demand;
- restart behavior has been tested while isolated and while safely commissioned;
- compressor and reversal delays have been observed;
- shared source timing has been observed;
- hydraulic flow remains safe under every valve combination;
- manual shutdown and rollback have been rehearsed;
- a post-cutover backup exists.

## Rollback triggers

Rollback immediately for:

- heating and cooling overlap;
- an output that cannot be turned off or whose state does not converge;
- unexpected activation after restart or reload;
- stale or implausible temperature driving demand;
- competing writer activity;
- relay timing violations;
- inadequate flow, short cycling, or abnormal equipment behavior;
- any loss of manufacturer safety protection.

## Rollback procedure

1. Set all virtual rooms to off if Home Assistant is responsive.
2. Use the manufacturer-approved manual shutdown if any physical output remains active or unreachable.
3. Apply the commissioning isolation used during cutover.
4. Verify physical AC, heater, valves, and shared relay are safe; do not rely only on dashboard state.
5. Remove the shared relay assignment or unload Virtual HVAC only after physical shutdown is verified.
6. Restore the previous controller's exclusive output ownership.
7. Restore the verified pre-cutover Home Assistant backup if configuration or registry state must be reverted.
8. Restart Home Assistant with physical outputs isolated.
9. Verify the previous controller's states and settings before removing isolation.
10. Record the failure, relevant redacted diagnostics, and the change required before another attempt.

Unloading Virtual HVAC first requests acknowledged neutralization and refuses cleanup if acknowledgement fails. It still cannot prove that an already unreachable active output became off. Physical verification is mandatory.
