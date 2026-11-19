"""The fixture owns Core and runner pins; each lane remains independently exact."""

from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

import pytest
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
LEGACY_FIXTURE = "ecc26f34993d166edac608197b1df172d9da23de"


def requirements(filename: str) -> list[Requirement]:
    """Read this project's plain, deliberately non-recursive requirements files."""
    return [
        Requirement(line)
        for raw in (ROOT / filename).read_text().splitlines()
        if (line := raw.strip()) and not line.startswith("#")
    ]


@pytest.mark.parametrize("filename", ["requirements_dev.txt", "requirements_compat.txt"])
def test_fixture_is_the_only_owner_of_core_and_runners(filename: str) -> None:
    """Independent bot edits must not create contradictory duplicate constraints."""
    names = {item.name for item in requirements(filename)}
    assert names.isdisjoint({"homeassistant", "pytest", "pytest-asyncio"})


def test_modern_fixture_is_exact_and_legacy_fixture_is_immutable() -> None:
    """Removing redundant Core pins must not unpin either compatibility lane."""
    modern = {item.name: item for item in requirements("requirements_dev.txt")}
    legacy = {item.name: item for item in requirements("requirements_compat.txt")}
    assert str(modern["pytest-homeassistant"].specifier) == "==2026.11.2"
    assert "pytest-homeassistant-custom-component" not in modern
    assert legacy["pytest-homeassistant-custom-component"].url == (
        "git+https://github.com/MatthewFlamm/pytest-homeassistant-custom-component.git@"
        + LEGACY_FIXTURE
    )
    assert "pytest-homeassistant" not in legacy


def test_installed_fixture_owns_exact_core_and_runner_versions() -> None:
    """Exercise distribution metadata, not just comments or a resolver exit code."""
    try:
        fixture = distribution("pytest-homeassistant")
    except PackageNotFoundError:
        fixture = distribution("pytest-homeassistant-custom-component")
        # The additional advertised-minimum probe uses the separate 2026.8.2 fixture.
        assert version("homeassistant") in {"2026.9.4", "2026.8.2"}
    else:
        assert version("homeassistant") == fixture.version == "2026.11.2"

    owned = {
        item.name: item
        for raw in fixture.requires or []
        if (item := Requirement(raw)).name in {"homeassistant", "pytest", "pytest-asyncio"}
    }
    assert owned.keys() == {"homeassistant", "pytest", "pytest-asyncio"}
    for name, requirement in owned.items():
        assert str(requirement.specifier) == "==" + version(name)
        assert requirement.marker is None
