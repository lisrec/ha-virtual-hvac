from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_project_and_integration_versions_match() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = json.loads((ROOT / "custom_components/virtual_hvac/manifest.json").read_text())

    assert project["project"]["version"] == manifest["version"]
