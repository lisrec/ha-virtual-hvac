# Security and Privacy

## Security posture

Virtual HVAC runs locally inside Home Assistant. It has no cloud service, telemetry, remote API, credential field, or network client. It requests Home Assistant services for user-selected local entities.

The integration is not a certified functional-safety system. Home Assistant availability, network transport, device firmware, radio links, relay hardware, valves, and HVAC equipment are outside its trust boundary.

## Safety terminology

### Logical demand fail-closed

Virtual HVAC clears effective heat demand and requests off outputs when required decision inputs are invalid or a required active output cannot be requested. Examples include:

- no valid finite temperature;
- an invalid target;
- an open, missing, unknown, or unavailable configured window input;
- an unavailable or unsupported required output;
- a Home Assistant service-call failure.

This prevents the integration from intentionally issuing a new shared on request from invalid demand.

### Physical output fail-safe

Physical fail-safe behavior is a hardware and installation property. If an actuator or relay is unreachable while already energized, software cannot guarantee that it turns off. State shown in Home Assistant may also lag physical equipment.

Use only a normally-open, low-voltage heat-demand relay for the optional shared source. Never use Virtual HVAC to interrupt:

- boiler mains power;
- burner power;
- circulation-pump power;
- domestic-hot-water power;
- manufacturer safety circuits.

Keep manufacturer controls, independent high limits, frost protection, pressure and flow protection, and manual emergency shutdowns intact. Use de-energize-to-stop wiring where the equipment supports it.

## Hydraulic and TRV safety

A service request accepted by Home Assistant does not prove that a valve opened. The current integration does not confirm TRV travel, end-switch state, or minimum flow before publishing room heat demand.

Before connecting the optional shared relay, provide a professionally appropriate readiness and circulation design. Depending on the installation, this may require a manufacturer-approved automatic bypass, a permanently open circuit, independent valve-ready logic, or another guaranteed safe flow path.

Do not energize a shared heat source solely because a TRV command was sent unless the installation independently guarantees safe readiness.

## Output ownership threat model

Virtual HVAC rejects duplicate output assignment within one controller. That does not stop other writers. Competing commands can come from:

- automations and scripts;
- dashboards and manual service calls;
- voice assistants;
- other integrations or external automation systems;
- vendor applications and schedules;
- local physical controls.

Exclusive system-wide output ownership is a migration and operating precondition. Inventory and disable competing writers before cutover. If a manual control must remain, define a clear priority and safe override outside Virtual HVAC.

## Data handled by the integration

Home Assistant config-entry storage contains:

- user-provided controller and room display names;
- selected physical entity identifiers;
- numeric control and timing settings;
- boolean feature choices.

Runtime memory also contains current modes, targets, demand, statuses, and opaque Home Assistant entry identifiers. This data remains in the Home Assistant instance under normal operation and may be included in Home Assistant backups.

Virtual HVAC does not store credentials or send this data elsewhere.

## Downloadable diagnostics

Diagnostics use an allowlist rather than best-effort redaction. They include only:

- room count and temperature-sensor count;
- whether optional input and output roles are configured;
- hysteresis, offsets, and protection durations;
- feature booleans;
- availability and demand booleans;
- known mode, preset, output, and status categories;
- optional retry seconds.

Diagnostics omit:

- controller and room names;
- every physical entity identifier;
- config-entry, subentry, device, and unique identifiers;
- current and target temperatures;
- hostnames, addresses, paths, and credentials;
- arbitrary exception or runtime text.

Unexpected runtime status text is replaced with `unrecognized`. Tests verify that private values and opaque identifiers do not appear in rendered diagnostics.

Home Assistant logs are a separate channel. Review logs before sharing because other integrations, service-call failures, or Home Assistant itself may include entity identifiers or environment details.

## Repository privacy rules

Public documentation, English translation content, fixtures, and diagnostics must not contain:

- real room or household names;
- concrete entity identifiers;
- network addresses or hostnames;
- local filesystem paths;
- credentials, tokens, or secret material;
- non-English user-facing text.

Generic terms and configuration key names are allowed. Automated tests check required public files, English translation parity, selected private-data patterns, and JSON validity. Automation supplements human review; it cannot detect every personal name or topology detail.

## Operational hardening

- Restrict Home Assistant administrative access.
- Protect backups and diagnostic downloads as configuration data.
- Keep Home Assistant, this integration, device firmware, and network infrastructure updated.
- Use reliable local connectivity and appropriate uninterruptible power where safety analysis requires it.
- Test sensor loss, window loss, output loss, Home Assistant restart, relay unavailability, and manual shutdown.
- Re-test after every output reassignment, firmware change, or automation change.
- Treat unknown or unavailable states as a fault requiring investigation.

## Reporting a vulnerability

Follow [SECURITY.md](../SECURITY.md). Do not publish security-sensitive details, private diagnostics, or home topology in a public issue.
