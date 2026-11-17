"""Use the real fixture helpers for each explicitly pinned Core test lane."""

from importlib.util import find_spec

if find_spec("pytest_homeassistant") is not None:
    from pytest_homeassistant.common import (
        MockConfigEntry,
        async_fire_time_changed,
        async_mock_service,
    )
else:
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        async_fire_time_changed,
        async_mock_service,
    )

__all__ = ["MockConfigEntry", "async_fire_time_changed", "async_mock_service"]
