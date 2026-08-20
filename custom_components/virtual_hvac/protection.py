"""Durable wall-clock transition timestamps for equipment protection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util.dt import as_utc, parse_datetime, utcnow

from .const import DOMAIN

_STORE_VERSION = 1


class ProtectionTimestamps:
    """Persist integration-owned transition times and parse them conservatively."""

    def __init__(self, hass: HomeAssistant | None, entry_id: str = "test") -> None:
        self._store = (
            Store[dict[str, Any]](
                hass, _STORE_VERSION, f"{DOMAIN}.protection.{entry_id}", private=True
            )
            if hass is not None
            else None
        )
        self._raw: dict[str, str] = {}

    async def async_load(self) -> None:
        """Load timestamps; malformed storage is treated as empty protection state."""
        if self._store is None:
            return
        try:
            loaded = await self._store.async_load()
        except (OSError, ValueError, TypeError):
            loaded = None
        transitions = loaded.get("transitions") if isinstance(loaded, dict) else None
        self.replace_raw(transitions if isinstance(transitions, dict) else {})

    def replace_raw(self, values: dict[str, object]) -> None:
        """Replace raw values, retaining only string values for deferred validation."""
        self._raw = {key: value for key, value in values.items() if isinstance(value, str)}

    def elapsed(self, key: str, now: datetime | None = None) -> float:
        """Return elapsed seconds, or zero for missing/corrupt/future timestamps."""
        current = as_utc(now or utcnow())
        raw = self._raw.get(key)
        if raw is None or (parsed := parse_datetime(raw)) is None:
            return 0.0
        try:
            parsed = as_utc(parsed)
            seconds = (current - parsed).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0.0
        return seconds if seconds >= 0 else 0.0

    def record(self, key: str, when: datetime | None = None) -> None:
        """Record and schedule persistence of a confirmed physical transition."""
        self._raw[key] = as_utc(when or utcnow()).isoformat()
        if self._store is not None:
            self._store.async_delay_save(lambda: {"transitions": dict(self._raw)}, delay=0)

    async def async_flush(self) -> None:
        """Flush the latest state during safe shutdown."""
        if self._store is not None:
            await self._store.async_save({"transitions": dict(self._raw)})
