#!/usr/bin/env python3
"""Build or verify the allowlisted Codex distribution bundle."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
MARKETPLACE = SOURCE.parent.parent
DESTINATION = MARKETPLACE / "codex-plugins" / "keen"
INCLUDES = (
    Path(".codex-plugin"),
    Path("assets"),
    Path("skills"),
    Path("LICENSE"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify without writing.")
    return parser.parse_args()


def file_hashes(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in INCLUDES:
        path = root / relative
        if path.is_file():
            hashes[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            continue
        if path.is_dir():
            for child in sorted(item for item in path.rglob("*") if item.is_file()):
                child_relative = child.relative_to(root).as_posix()
                hashes[child_relative] = hashlib.sha256(child.read_bytes()).hexdigest()
            continue
        raise FileNotFoundError(f"required bundle input is missing: {path}")
    return hashes


def verify() -> list[str]:
    expected = file_hashes(SOURCE)
    if not DESTINATION.is_dir():
        return [f"bundle is missing: {DESTINATION}"]
    actual = {
        child.relative_to(DESTINATION).as_posix(): hashlib.sha256(child.read_bytes()).hexdigest()
        for child in sorted(item for item in DESTINATION.rglob("*") if item.is_file())
    }
    errors: list[str] = []
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(
        relative
        for relative in set(expected) & set(actual)
        if expected[relative] != actual[relative]
    )
    if missing:
        errors.append(f"missing files: {missing}")
    if extra:
        errors.append(f"unexpected files: {extra}")
    if changed:
        errors.append(f"changed files: {changed}")
    return errors


def sync() -> None:
    expected_destination = MARKETPLACE.resolve() / "codex-plugins" / "keen"
    if DESTINATION.resolve() != expected_destination:
        raise RuntimeError(f"refusing unexpected bundle destination: {DESTINATION}")
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="keen-codex-bundle-", dir=DESTINATION.parent
    ) as temporary_raw:
        staged = Path(temporary_raw) / "keen"
        staged.mkdir()
        for relative in INCLUDES:
            source = SOURCE / relative
            destination = staged / relative
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

        backup = Path(temporary_raw) / "previous"
        if DESTINATION.exists():
            os.replace(DESTINATION, backup)
        try:
            os.replace(staged, DESTINATION)
        except OSError:
            if backup.exists() and not DESTINATION.exists():
                os.replace(backup, DESTINATION)
            raise
        if backup.exists():
            shutil.rmtree(backup)


def main() -> int:
    args = parse_args()
    if not args.check:
        sync()
    errors = verify()
    if errors:
        print("Codex plugin bundle validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Codex plugin bundle is current: {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
