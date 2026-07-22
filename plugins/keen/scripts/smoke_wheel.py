#!/usr/bin/env python3
"""Build and validate Keen from a clean, isolated wheel install."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
CANONICAL_SYSTEMS = {
    "apple-hig",
    "atlassian",
    "carbon",
    "fluent-2",
    "material-3",
    "polaris",
}


def _run(argv: list[str], *, cwd: Path, capture: bool = False) -> str:
    print("+", " ".join(argv))
    completed = subprocess.run(
        argv,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=capture,
    )
    if capture:
        return completed.stdout.strip()
    return ""


def _project_version() -> str:
    text = (PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        raise RuntimeError("project version is missing from pyproject.toml")
    return match.group(1)


def _assert_wheel_contents(wheel: Path) -> None:
    expected = {
        "harness/templates/report.html.tmpl",
        *{
            f"harness/resources/{path.relative_to(PLUGIN_ROOT).as_posix()}"
            for directory in ("config", "references")
            for path in (PLUGIN_ROOT / directory).rglob("*")
            if path.is_file()
        },
    }
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
    missing = sorted(expected - members)
    if missing:
        raise RuntimeError(f"wheel is missing runtime assets: {missing}")
    print(f"Wheel contains all {len(expected)} required runtime assets.")


def _venv_executables(env_dir: Path) -> tuple[Path, Path, Path]:
    bin_dir = env_dir / ("Scripts" if os.name == "nt" else "bin")
    suffix = ".exe" if os.name == "nt" else ""
    return (
        bin_dir / f"python{suffix}",
        bin_dir / f"pip{suffix}",
        bin_dir / f"keen{suffix}",
    )


def _create_venv(env_dir: Path) -> None:
    """Create a pip-enabled venv, with uv as a fallback for uv-managed Python."""
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        return
    except subprocess.CalledProcessError:
        uv = shutil.which("uv")
        if uv is None:
            raise
        print("stdlib ensurepip failed; retrying isolated environment creation with uv")
        _run(
            [uv, "venv", "--clear", "--seed", "--python", sys.executable, str(env_dir)],
            cwd=PLUGIN_ROOT,
        )


def _build_wheel(dist_dir: Path) -> None:
    """Build with pip when available, otherwise use the repository's uv tool."""
    if importlib.util.find_spec("pip") is not None:
        _run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                ".",
                "--no-deps",
                "--wheel-dir",
                str(dist_dir),
            ],
            cwd=PLUGIN_ROOT,
        )
        return

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("wheel smoke test requires either pip or uv")
    _run([uv, "build", "--wheel", "--out-dir", str(dist_dir), "."], cwd=PLUGIN_ROOT)


def main() -> int:
    version = _project_version()
    with tempfile.TemporaryDirectory(prefix="keen-wheel-smoke-") as raw_tmp:
        temp_root = Path(raw_tmp)
        dist_dir = temp_root / "dist"
        dist_dir.mkdir()

        _run(
            [sys.executable, "scripts/sync_runtime_assets.py", "--check"],
            cwd=PLUGIN_ROOT,
        )
        _build_wheel(dist_dir)
        wheels = list(dist_dir.glob("keen_harness-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one built wheel, found: {wheels}")
        wheel = wheels[0]
        _assert_wheel_contents(wheel)

        env_dir = temp_root / "venv"
        _create_venv(env_dir)
        python, pip, keen = _venv_executables(env_dir)
        _run(
            [str(pip), "install", "--disable-pip-version-check", str(wheel)],
            cwd=temp_root,
        )

        version_output = _run([str(keen), "--version"], cwd=temp_root, capture=True)
        if version not in version_output:
            raise RuntimeError(
                f"installed CLI version mismatch: expected {version!r}, got {version_output!r}"
            )

        _run([str(keen), "doctor"], cwd=temp_root)

        systems_output = _run(
            [
                str(python),
                "-I",
                "-c",
                "import json; from harness.cli import list_available_systems; "
                "print(json.dumps(list_available_systems()))",
            ],
            cwd=temp_root,
            capture=True,
        )
        systems = set(json.loads(systems_output))
        if not CANONICAL_SYSTEMS.issubset(systems):
            raise RuntimeError(
                f"installed wheel did not discover canonical systems: {sorted(systems)}"
            )

        report_dir = temp_root / "report"
        report_code = """
from pathlib import Path
import sys
from harness._html_report import write_report

out = Path(sys.argv[1])
out.mkdir()
report = {
    "captures": [],
    "components": [],
    "top_findings": [],
    "score": {"grade": "A", "score": 0},
}
path = write_report(report, out, "# Wheel smoke report\\n\\nInstalled assets work.")
assert path.is_file(), path
text = path.read_text(encoding="utf-8")
assert "<!doctype html>" in text.lower()
print(path)
"""
        _run(
            [str(python), "-I", "-c", report_code, str(report_dir)],
            cwd=temp_root,
        )

    print(f"Clean wheel smoke passed for Keen {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
