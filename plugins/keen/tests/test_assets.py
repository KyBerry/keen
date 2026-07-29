"""Runtime asset resolution tests for source and wheel layouts."""

from __future__ import annotations

import json
import re
from pathlib import Path

from harness import __version__, _assets


def test_source_checkout_uses_plugin_assets() -> None:
    root = _assets.asset_root()
    assert (root / ".claude-plugin" / "plugin.json").is_file()
    assert _assets.asset_path("config", "rubric.yaml").is_file()


def test_release_versions_stay_in_sync() -> None:
    root = _assets.asset_root()
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    project_text = (root / "pyproject.toml").read_text()
    project_match = re.search(r'^version = "([^"]+)"$', project_text, re.MULTILINE)
    marketplace = json.loads(
        (root.parent.parent / ".claude-plugin" / "marketplace.json").read_text()
    )
    listing = next(item for item in marketplace["plugins"] if item["name"] == "keen")

    assert project_match is not None
    codex_plugin = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
    codex_base_version = codex_plugin["version"].split("+", 1)[0]

    assert plugin["name"] == "keen"
    assert codex_plugin["name"] == "keen"
    assert codex_plugin["interface"]["composerIcon"] == "./assets/keen-logo.png"
    assert (root / "assets" / "keen-logo.png").is_file()

    assert {
        __version__,
        plugin["version"],
        codex_base_version,
        project_match.group(1),
        listing["version"],
    } == {"0.8.1"}


def test_codex_marketplace_uses_clean_generated_bundle() -> None:
    root = _assets.asset_root()
    marketplace = json.loads(
        (root.parent.parent / ".agents" / "plugins" / "marketplace.json").read_text()
    )
    listing = next(item for item in marketplace["plugins"] if item["name"] == "keen")
    assert listing["source"]["path"] == "./codex-plugins/keen"

    bundle = root.parent.parent / "codex-plugins" / "keen"
    assert (bundle / ".codex-plugin" / "plugin.json").is_file()
    assert (bundle / "assets" / "keen-logo.png").is_file()
    assert (bundle / "skills" / "refine" / "SKILL.md").is_file()
    assert not (bundle / ".venv").exists()
    assert not (bundle / "tests").exists()


def test_packaged_assets_are_complete(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(_assets, "_SOURCE_MANIFEST", Path("/definitely/missing/plugin.json"))
    root = _assets.asset_root()
    assert root == _assets.packaged_asset_root()
    assert (root / "config" / "rubric.yaml").is_file()
    assert (root / "config" / "states.json").is_file()
    assert (root / "config" / "viewports.json").is_file()
    assert (root / "workshop" / "index.html").is_file()
    assert (root / "workshop" / "workshop.css").is_file()
    assert (root / "workshop" / "workshop.js").is_file()
    assert (root / "workshop" / "spec.schema.json").is_file()
    systems = sorted(path.stem for path in (root / "references" / "design-systems").glob("*.md"))
    assert systems == [
        "apple-hig",
        "atlassian",
        "carbon",
        "fluent-2",
        "material-3",
        "polaris",
    ]
