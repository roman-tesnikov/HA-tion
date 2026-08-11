"""Verify that every Home Assistant entity platform can be imported."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module, util
import sys
from types import ModuleType

import homeassistant.components
import pytest

if util.find_spec("homeassistant") is None:
    pytest.skip(
        "Home Assistant is not installed in the local test environment",
        allow_module_level=True,
    )


@pytest.fixture(scope="module", autouse=True)
def isolate_homeassistant_bluetooth() -> Iterator[None]:
    """Keep platform imports independent from built-in integration setup."""
    bluetooth = ModuleType("homeassistant.components.bluetooth")
    bluetooth.BluetoothCallbackMatcher = dict

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(sys.modules, "homeassistant.components.bluetooth", bluetooth)
    monkeypatch.setattr(
        homeassistant.components,
        "bluetooth",
        bluetooth,
        raising=False,
    )
    yield
    monkeypatch.undo()


@pytest.mark.parametrize("platform", ("climate", "fan", "select", "sensor"))
def test_platform_import(platform: str) -> None:
    """Import platform code against the installed Home Assistant API."""
    import_module(f"custom_components.ha_tion_btle.{platform}")
