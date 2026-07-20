"""Validate files and documentation used by HACS and Home Assistant."""

import ast
import json
import re
import struct
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
COMPONENT = ROOT / "custom_components" / "ha_tion_btle"
FORK_URL = "https://github.com/roman-tesnikov/HA-tion"


def test_distribution_metadata_targets_the_fork() -> None:
    """Keep HACS and Home Assistant metadata aligned with the README."""
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))

    assert hacs["homeassistant"] == "2026.6.0"
    assert "zip_release" not in hacs
    assert "filename" not in hacs
    assert manifest["documentation"] == FORK_URL
    assert manifest["issue_tracker"] == f"{FORK_URL}/issues"
    assert manifest["codeowners"] == ["@roman-tesnikov"]


def test_hacs_brand_icon_is_valid_rgba_png() -> None:
    """HACS requires a local 512x512 brand icon with an alpha channel."""
    data = (COMPONENT / "brand" / "icon.png").read_bytes()

    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[12:16] == b"IHDR"

    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (512, 512)
    assert data[24] == 8
    assert data[25] == 6


def test_local_const_imports_reference_existing_names() -> None:
    """Prevent component modules from importing missing local constants."""
    const_tree = ast.parse((COMPONENT / "const.py").read_text(encoding="utf-8"))
    exported_names: set[str] = set()

    for node in const_tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            exported_names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Assign):
            exported_names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            exported_names.add(node.target.id)

    missing_imports: list[str] = []
    for module_path in COMPONENT.glob("*.py"):
        module_tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(module_tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.level == 1
                and node.module == "const"
            ):
                missing_imports.extend(
                    f"{module_path.name}:{alias.name}"
                    for alias in node.names
                    if alias.name not in exported_names
                )

    assert not missing_imports


def test_readme_yaml_examples_parse() -> None:
    """Keep published Home Assistant configuration examples valid YAML."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", readme, flags=re.DOTALL)

    assert blocks
    for block in blocks:
        yaml.safe_load(block)
