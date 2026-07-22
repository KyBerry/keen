"""Resolve runtime data in both plugin checkouts and installed wheels.

The Claude plugin keeps its editable source assets at the repository root so
agents can read them directly.  Python wheels cannot reliably install files
next to a top-level package, so the build also ships a synchronized copy under
``harness/resources``.  Runtime callers use the source copy only when the
package is demonstrably inside a Keen plugin checkout; normal installs
always use the package-local copy.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent
_PACKAGED_ASSET_ROOT = _PACKAGE_ROOT / "resources"
_SOURCE_ROOT = _PACKAGE_ROOT.parent
_SOURCE_MANIFEST = _SOURCE_ROOT / ".claude-plugin" / "plugin.json"


def asset_root() -> Path:
    """Return the active, filesystem-backed runtime asset directory."""
    if _SOURCE_MANIFEST.is_file():
        return _SOURCE_ROOT
    return _PACKAGED_ASSET_ROOT


def asset_path(*parts: str) -> Path:
    """Return a path below the active runtime asset directory."""
    return asset_root().joinpath(*parts)


def packaged_asset_root() -> Path:
    """Return the package-local resource directory (primarily for validation)."""
    return _PACKAGED_ASSET_ROOT
