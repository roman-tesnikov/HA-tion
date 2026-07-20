"""Validate files and documentation used by HACS and Home Assistant."""

import json
import re
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
    assert manifest["documentation"] == FORK_URL
    assert manifest["issue_tracker"] == f"{FORK_URL}/issues"
    assert manifest["codeowners"] == ["@roman-tesnikov"]


def test_readme_yaml_examples_parse() -> None:
    """Keep published Home Assistant configuration examples valid YAML."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", readme, flags=re.DOTALL)

    assert blocks
    for block in blocks:
        yaml.safe_load(block)
