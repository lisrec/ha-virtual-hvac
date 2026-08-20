# Security Policy

## Supported versions

Virtual HVAC is currently beta software. Security and safety fixes are applied to the latest development version. No older release line is guaranteed to receive fixes.

| Version | Supported |
|---|---:|
| Latest development version | Yes |
| Older versions | No |

## Reporting a vulnerability

Use the repository's private security-reporting feature from its **Security** tab. Do not open a public issue for a suspected vulnerability.

Include only the minimum information needed to reproduce the problem:

- affected version or commit;
- Home Assistant version;
- generic reproduction steps using simulated entities;
- expected and observed behavior;
- safety or privacy impact;
- redacted diagnostics if relevant.

Do not include real names, physical entity identifiers, hostnames, network addresses, local paths, credentials, backups, screenshots of private dashboards, or home topology.

If private reporting is unavailable, open a public issue requesting a private contact channel without disclosing technical details.

## Response process

Maintainers will aim to:

1. acknowledge a complete private report;
2. reproduce it without live equipment;
3. assess confidentiality, integrity, availability, privacy, and physical-control impact;
4. prepare a tested fix and documentation update;
5. coordinate disclosure after users have a safe upgrade path.

Response timing is best effort because this is a community project.

## Security scope

Reports are especially important when they involve:

- heating and cooling overlap;
- an unintended on request;
- failure to clear logical demand;
- unsafe startup, restoration, reload, unload, or removal;
- bypass of output ownership validation;
- protection-timer errors;
- unredacted diagnostics;
- credential or private-data exposure;
- arbitrary service calls or code execution.

Device firmware defects, Home Assistant Core defects, network failures, incorrect wiring, and HVAC equipment faults may be outside this repository, but reports that reveal an unsafe integration interaction are still welcome.

## Physical safety notice

Virtual HVAC is not a certified safety controller. Logical demand fail-closed behavior cannot guarantee physical shutdown of unreachable or failed hardware.

The optional shared output must be a normally-open, low-voltage heat-demand relay. It must not switch boiler mains, burner power, pump power, domestic-hot-water power, or manufacturer safety circuits. Preserve independent limits, frost protection, hydraulic safeguards, and a tested manual shutdown.

Do not connect live equipment while reproducing a security issue. Use simulated states and non-physical outputs.

See [Security and Privacy](docs/SECURITY_AND_PRIVACY.md) and [Migration and Rollback](docs/MIGRATION.md).
