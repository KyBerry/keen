#!/usr/bin/env python3
"""Synchronize wheel runtime assets with the Claude plugin source assets."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PACKAGED_ROOT = PLUGIN_ROOT / "harness" / "resources"
SOURCE_DIRS = ("config", "references", "workshop")


def _files_below(root: Path, directory: str) -> dict[Path, Path]:
    source = root / directory
    return {
        path.relative_to(root): path
        for path in source.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }


def _source_files() -> dict[Path, Path]:
    files: dict[Path, Path] = {}
    for directory in SOURCE_DIRS:
        files.update(_files_below(PLUGIN_ROOT, directory))
    return files


def _packaged_files() -> dict[Path, Path]:
    files: dict[Path, Path] = {}
    for directory in SOURCE_DIRS:
        if (PACKAGED_ROOT / directory).exists():
            files.update(_files_below(PACKAGED_ROOT, directory))
    return files


def check() -> list[str]:
    """Return human-readable differences without changing the filesystem."""
    source = _source_files()
    packaged = _packaged_files()
    differences: list[str] = []
    for relative in sorted(source.keys() - packaged.keys()):
        differences.append(f"missing packaged asset: {relative}")
    for relative in sorted(packaged.keys() - source.keys()):
        differences.append(f"stale packaged asset: {relative}")
    for relative in sorted(source.keys() & packaged.keys()):
        if source[relative].read_bytes() != packaged[relative].read_bytes():
            differences.append(f"content differs: {relative}")
    return differences


def sync() -> None:
    """Copy source assets into the package and remove stale package copies."""
    source = _source_files()
    packaged = _packaged_files()
    for relative, source_path in source.items():
        destination = PACKAGED_ROOT / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination)
    for relative in packaged.keys() - source.keys():
        (PACKAGED_ROOT / relative).unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if packaged assets do not exactly match their source copies",
    )
    args = parser.parse_args(argv)
    if args.check:
        differences = check()
        if differences:
            for difference in differences:
                print(difference, file=sys.stderr)
            print(
                "Run `python3 scripts/sync_runtime_assets.py` to synchronize assets.",
                file=sys.stderr,
            )
            return 1
        print("Packaged runtime assets match source assets.")
        return 0

    sync()
    differences = check()
    if differences:
        for difference in differences:
            print(difference, file=sys.stderr)
        return 1
    print("Synchronized runtime assets under harness/resources/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
