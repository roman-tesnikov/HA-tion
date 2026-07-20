"""Verify that every Home Assistant entity platform can be imported."""

from __future__ import annotations

from importlib import import_module, util

import pytest

if util.find_spec("homeassistant") is None:
    pytest.skip(
        "Home Assistant is not installed in the local test environment",
        allow_module_level=True,
    )


@pytest.mark.parametrize("platform", ("climate", "fan", "select", "sensor"))
def test_platform_import(platform: str) -> None:
    """Import platforms exactly as the Home Assistant loader does."""
    import_module(f"custom_components.ha_tion_btle.{platform}")
