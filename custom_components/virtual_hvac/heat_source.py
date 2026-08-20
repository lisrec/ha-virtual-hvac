"""Pure shared heat-source arbitration."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HeatSourceDecision:
    """Optional relay action and an observable decision reason."""

    action: bool | None
    reason: str
    retry_after_seconds: int | None = None


def decide_heat_source(
    demand: bool,
    relay_state: bool | None,
    state_elapsed_seconds: float,
    minimum_on_seconds: int,
    minimum_off_seconds: int,
    *,
    safe_delay_enabled: bool = True,
) -> HeatSourceDecision:
    """Return the safe next action for a shared heat-source relay."""
    if relay_state is None:
        return HeatSourceDecision(None, "relay_unavailable")
    if demand and relay_state:
        return HeatSourceDecision(None, "steady_on")
    if not demand and not relay_state:
        return HeatSourceDecision(None, "steady_off")
    if not safe_delay_enabled:
        return HeatSourceDecision(demand, "turn_on" if demand else "turn_off")
    if demand:
        remaining = minimum_off_seconds - state_elapsed_seconds
        if remaining > 0:
            return HeatSourceDecision(None, "minimum_off", math.ceil(remaining))
        return HeatSourceDecision(True, "turn_on")
    remaining = minimum_on_seconds - state_elapsed_seconds
    if remaining > 0:
        return HeatSourceDecision(None, "minimum_on", math.ceil(remaining))
    return HeatSourceDecision(False, "turn_off")
