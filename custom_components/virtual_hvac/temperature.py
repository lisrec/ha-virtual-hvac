"""Pure temperature aggregation helpers."""

from __future__ import annotations

import math
from collections.abc import Buffer, Iterable
from typing import SupportsFloat, SupportsIndex, cast


def average_valid_temperatures(values: Iterable[object]) -> float | None:
    """Return the arithmetic mean of finite numeric temperature values."""
    valid: list[float] = []
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        try:
            number = float(cast(str | Buffer | SupportsFloat | SupportsIndex, value))
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            valid.append(number)
    if not valid:
        return None
    return sum(valid) / len(valid)
