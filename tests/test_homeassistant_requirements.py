"""Tests for resolving Home Assistant integration requirements used by CI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "homeassistant_requirements.py"


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")


def test_cli_includes_transitive_integration_requirements(tmp_path: Path) -> None:
    """Install requirements from dependencies imported by direct dependencies."""
    site_packages = tmp_path / "site-packages"
    homeassistant = site_packages / "homeassistant"
    components = homeassistant / "components"
    custom_manifest = tmp_path / "manifest.json"
    homeassistant.mkdir(parents=True)
    (homeassistant / "__init__.py").write_text("", encoding="utf-8")
    _write_manifest(
        custom_manifest,
        {"dependencies": ["bluetooth"]},
    )
    _write_manifest(
        components / "bluetooth" / "manifest.json",
        {
            "dependencies": ["usb"],
            "requirements": ["bleak-retry-connector==4.6.3"],
        },
    )
    _write_manifest(
        components / "usb" / "manifest.json",
        {"requirements": ["aiousbwatcher==1.2.7"]},
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(custom_manifest),
        ],
        check=False,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(site_packages)},
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "bleak-retry-connector==4.6.3",
        "aiousbwatcher==1.2.7",
    ]
