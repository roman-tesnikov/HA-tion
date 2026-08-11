"""Print requirements for Home Assistant integration dependencies."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from importlib.util import find_spec
import json
from pathlib import Path
from typing import Any


def _read_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def collect_requirements(components_dir: Path, domains: Sequence[str]) -> list[str]:
    """Collect requirements from integration dependencies recursively."""
    requirements: list[str] = []
    seen_domains: set[str] = set()

    def visit(domain: str) -> None:
        if domain in seen_domains:
            return
        seen_domains.add(domain)

        manifest = _read_manifest(components_dir / domain / "manifest.json")
        requirements.extend(manifest.get("requirements", []))
        for dependency in manifest.get("dependencies", []):
            visit(dependency)

    for domain in domains:
        visit(domain)

    return requirements


def _installed_components_dir() -> Path:
    spec = find_spec("homeassistant")
    if spec is None or spec.submodule_search_locations is None:
        raise RuntimeError("Home Assistant is not installed")
    return Path(next(iter(spec.submodule_search_locations))) / "components"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    manifest = _read_manifest(args.manifest)
    requirements = collect_requirements(
        _installed_components_dir(),
        manifest.get("dependencies", []),
    )
    print("\n".join(requirements))


if __name__ == "__main__":
    main()
