"""Platform imports must work with the actual Core installed in every lane."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("platform", ["climate", "sensor", "binary_sensor"])
def test_platform_import_and_device_metadata_types(platform: str) -> None:
    """Older supported Core has DeviceInfo but no ChildDeviceInfo symbol."""
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            f"import custom_components.virtual_hvac.{platform}; "
            "from typing import get_type_hints; "
            "from custom_components.virtual_hvac.entity import "
            "VirtualHVACRoomEntity, VirtualHVACControllerEntity; "
            "assert get_type_hints(VirtualHVACRoomEntity.device_info.fget)['return']; "
            "assert get_type_hints(VirtualHVACControllerEntity)['_attr_device_info']",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
