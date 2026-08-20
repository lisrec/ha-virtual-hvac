# Contributing

Thank you for improving Virtual HVAC. Changes must preserve deterministic control, conservative failure behavior, public privacy, and Home Assistant conventions.

## Before opening a change

- Discuss large behavior or architecture changes before implementation.
- Keep one concern per change.
- Never test against a production Home Assistant instance or real HVAC equipment.
- Use simulated Home Assistant states and mocked service boundaries.
- Add or update documentation whenever behavior, limits, diagnostics, or configuration changes.

## Development setup

The project requires Python 3.14.2 or newer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements_dev.txt
```

Run commands from the repository root. Do not commit the virtual environment, caches, Home Assistant storage, backups, or diagnostic downloads.

## Test-driven workflow

For behavior changes:

1. Add one focused test that expresses the required behavior.
2. Run it and confirm it fails for the expected reason.
3. Implement the smallest change that passes.
4. Run the focused test again.
5. Refactor only while tests remain green.
6. Run the complete verification suite.

Prioritize tests for:

- logical fail-closed decisions;
- heat/cool mutual exclusion;
- service-call order and failures;
- output availability and convergence;
- startup, restore, reload, unload, and removal;
- event storms and protection timers;
- duplicate output ownership;
- diagnostic redaction;
- configuration and translation parity.

## Verification

Run:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy custom_components/virtual_hvac
.venv/bin/python -m compileall custom_components/virtual_hvac
```

Also validate both translation JSON files and confirm they contain the same English structure and text.

A contribution is not ready if tests are skipped, expected failures are hidden, or a hardware claim has not been verified.

## Safety review checklist

Any control or configuration change must answer:

- Can heating and cooling be requested together?
- What happens when each input is missing, unknown, unavailable, stale, or invalid?
- What happens if an output was already active and becomes unreachable?
- Are opposite paths turned off and physically acknowledged before a new path starts?
- Are service calls serialized, bounded, and safe under event storms?
- Do protection times survive restart using trustworthy transition data?
- Does unload or removal leave an active output without an owner?
- Can a shared source start before valve or hydraulic readiness is assured?
- Does the change preserve manufacturer safety controls?

Use precise language: **logical demand fail-closed** is not **physical output fail-safe**.

## Documentation style

- Write public content in clear English.
- Use generic terms, never household-specific examples.
- Do not include real room names, entity identifiers, network details, hostnames, local paths, or credentials.
- Document current behavior and known limitations; do not claim future safeguards already exist.
- Use relative repository links where possible.
- Explain units, boundaries, defaults, and rollback consequences.

## Diagnostics rules

Downloadable diagnostics use an allowlist. New fields must be demonstrably necessary and must not expose:

- names or physical entity identifiers;
- entry, subentry, device, or unique identifiers;
- temperatures or targets;
- arbitrary exception or runtime text;
- host, network, filesystem, or credential data.

Use booleans, counts, bounded numeric settings, known categories, and known reason codes. Add a test that injects private sentinel values and proves none are rendered.

## Translation rules

`strings.json` is the English source and `translations/en.json` must match it. Every config-flow and room-subentry step, error, abort reason, field, and description must have English text.

Do not add machine-generated translations to this repository without review by a fluent contributor and a defined maintenance process.

## Commit and review quality

- Keep code simple and typed.
- Avoid unrelated formatting or generated-file churn.
- Explain safety-impacting decisions in the change description.
- Include exact test and lint results.
- Identify known limitations and follow-up work explicitly.
- Never include live Home Assistant data in commits, issues, or review screenshots.

By contributing, you agree that your work is licensed under the repository's MIT License.
