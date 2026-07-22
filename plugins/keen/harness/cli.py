"""Command-line interface for Keen's deterministic UI review engine.

Subcommands:
    review     capture + decompose + analyze + report (the full pipeline)
    capture    capture only
    audit      analyze captures that already exist on disk
    tokens     extract design tokens
    compare    review with a target design system as the rubric
    systemize  propose a design system from observed tokens
    diff       compare findings between two runs
    intent     emit bounded evidence for /keen:ui-intent
    preview-system  render preview HTML and optional comparison for a system JSON
    doctor     verify install: python version, playwright, configs, references

All subcommands accept --out <dir> for the output directory.
"""

# Exit codes: 0=success, 1=runtime failure, 2=CLI misuse
from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness import (
    __version__,
    _timing,
)
from harness import analyze as analyze_mod
from harness import capture as capture_mod
from harness import decompose as decompose_mod
from harness import report as report_mod
from harness import slop as slop_mod
from harness import taste as taste_mod
from harness import tokens as tokens_mod
from harness._assets import asset_path
from harness._timing import stage
from harness._urlsafe import redact_url

VIEWPORTS_DEFAULT = "mobile,tablet,desktop"
STATES_DEFAULT = "default"
logger = logging.getLogger("keen")


def _configure_logging(verbose: int, quiet: bool, profile: bool = False) -> None:
    """Configure root logger BEFORE dispatching to a subcommand.

    When `profile` is on, the timing logger is pinned to INFO regardless of
    the overall verbosity (so per-stage timings are visible even with -q).
    """
    if quiet:
        level = logging.ERROR
    elif verbose >= 2 or verbose == 1:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    if profile:
        # Force the timing logger above any -q suppression so stage timings
        # remain visible during a profile run.
        logging.getLogger("keen.timing").setLevel(logging.INFO)


def _default_outdir(prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    return Path(".keen") / prefix / stamp


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _normalize_capture_target(target: str) -> str:
    """Turn an existing local file path into the file URL Playwright expects."""
    if urlparse(target).scheme:
        return target
    candidate = Path(target).expanduser()
    if candidate.is_file():
        return candidate.resolve().as_uri()
    return target


# -- Auth gating ----------------------------------------------------------


def _resolve_auth_steps(args: argparse.Namespace) -> Path | None:
    """Resolve `--auth-steps` to a Path.

    Unlike --auth-script, this does not execute Python; the file is parsed as
    JSON and dispatched through a closed action whitelist. No --unsafe gate is
    required.
    """
    raw = getattr(args, "auth_steps", None)
    if not raw:
        return None
    try:
        resolved = Path(raw).resolve(strict=True)
    except (FileNotFoundError, OSError) as e:
        logger.error("--auth-steps: cannot resolve path %r: %s", raw, e)
        raise SystemExit(2) from e
    logger.info("using declarative auth steps from %s", resolved)
    return resolved


def _resolve_auth_script(args: argparse.Namespace) -> Path | None:
    """Vet `--auth-script` before letting capture.py exec it.

    The script will execute *arbitrary Python* with the same privileges as the
    harness, so the gate must be explicit:

    1. `--auth-script` alone is insufficient — caller must also pass
       `--unsafe-auth-script` to acknowledge the risk.
    2. The path must resolve (strict=True) and, by default, live inside cwd.
       Pass `--unsafe-auth-script-anywhere` to relax the cwd restriction.
    """
    raw = getattr(args, "auth_script", None)
    if not raw:
        return None

    if not getattr(args, "unsafe_auth_script", False):
        logger.error(
            "--auth-script executes arbitrary Python; refusing without --unsafe-auth-script."
        )
        raise SystemExit(2)

    try:
        resolved = Path(raw).resolve(strict=True)
    except (FileNotFoundError, OSError) as e:
        logger.error("--auth-script: cannot resolve path %r: %s", raw, e)
        raise SystemExit(2) from e

    if not getattr(args, "unsafe_auth_script_anywhere", False):
        cwd = Path.cwd().resolve()
        try:
            resolved.relative_to(cwd)
        except ValueError as e:
            logger.error(
                "--auth-script: %s is outside cwd %s; pass --unsafe-auth-script-anywhere to override.",
                resolved,
                cwd,
            )
            raise SystemExit(2) from e

    logger.warning(
        "EXECUTING UNSAFE AUTH SCRIPT %s — this runs arbitrary Python in-process.",
        resolved,
    )
    return resolved


# -- System resolution ----------------------------------------------------


def list_available_systems() -> list[str]:
    """Return all system names with a markdown ref under references/design-systems/."""
    refs = asset_path("references", "design-systems")
    if not refs.exists():
        return []
    return sorted(p.stem for p in refs.glob("*.md"))


def validate_system(name: str | None) -> str | None:
    """Return name unchanged if a ref file exists, or raise SystemExit otherwise."""
    if name is None:
        return None
    available = list_available_systems()
    if name not in available:
        logger.error(
            "unknown system '%s'. Available: %s. Add a new system by writing "
            "references/design-systems/%s.md.",
            name,
            ", ".join(available),
            name,
        )
        raise SystemExit(2)
    return name


def load_custom_system_tokens(name: str) -> dict[str, Any] | None:
    """Look for a JSON token bundle next to the markdown ref. Used when tokens.compare_to_system
    is asked about a system not in SYSTEM_TOKENS."""
    candidate = asset_path("references", "design-systems", f"{name}.json")
    if not candidate.exists():
        return None
    try:
        data = json.loads(candidate.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    spacing = data.get("spacing", {})
    type_ = data.get("type", {})
    radii = data.get("radii", {})
    grid = spacing.get("grid_px", 4)
    sizes = [s["size_px"] for s in type_.get("sizes", []) if "size_px" in s]
    radii_px = [
        r["px"] for r in radii.get("scale", []) if isinstance(r.get("px"), int) and r["px"] < 1000
    ]
    return {
        "spacing_grid_px": grid,
        "font_sizes_px": sizes or [12, 14, 16, 20, 24, 32],
        "border_radii_px": radii_px or [0, 4, 8],
    }


# -- Capture args ---------------------------------------------------------


def _add_capture_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", help="URL or local file path to capture")
    p.add_argument(
        "--viewports",
        default=VIEWPORTS_DEFAULT,
        help=f"Comma-separated viewport names. Default: {VIEWPORTS_DEFAULT}",
    )
    p.add_argument(
        "--states",
        default=STATES_DEFAULT,
        help=(
            "Comma-separated component states. Defined in config/states.json: "
            "default, focus-first-input, hover-primary, form-error, dark, "
            "high-contrast, reduced-motion, zoom-200, loading, empty. "
            f"Default: {STATES_DEFAULT}"
        ),
    )
    p.add_argument(
        "--full-page",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture the full scrollable page (default; use --no-full-page for viewport only).",
    )
    p.add_argument(
        "--wait-selector",
        default=None,
        help="CSS selector to wait for before capturing (for SPAs).",
    )
    # --auth-steps and --auth-script are mutually exclusive: pick one mechanism.
    # --auth-steps is the preferred path (declarative JSON DSL, no code exec).
    auth_group = p.add_mutually_exclusive_group()
    auth_group.add_argument(
        "--auth-steps",
        default=None,
        help=("path to a JSON file with declarative login steps (safer than --auth-script)"),
    )
    auth_group.add_argument(
        "--auth-script",
        default=None,
        help=(
            "Path to a Python file exposing async login(page). "
            "WARNING: executes arbitrary Python; requires --unsafe-auth-script. "
            "Review the file before passing."
        ),
    )
    p.add_argument(
        "--unsafe-auth-script",
        action="store_true",
        default=False,
        help="Acknowledge that --auth-script executes arbitrary code.",
    )
    p.add_argument(
        "--unsafe-auth-script-anywhere",
        action="store_true",
        default=False,
        help="Permit --auth-script files outside cwd (implies --unsafe-auth-script).",
    )
    p.add_argument(
        "--viewport-config",
        default=None,
        help="Path to a custom viewports.json (overrides config/viewports.json).",
    )
    p.add_argument(
        "--states-config",
        default=None,
        help="Path to a custom states.json (overrides config/states.json).",
    )
    p.add_argument(
        "--allow-internal",
        action="store_true",
        default=False,
        help=(
            "Permit targets that resolve to private/loopback/link-local IPs. "
            "Default false: such targets are rejected as SSRF risk."
        ),
    )
    p.add_argument(
        "--allow-file",
        action="store_true",
        default=False,
        help="Permit file:// targets. Default false.",
    )
    p.add_argument(
        "--goto-timeout",
        type=float,
        default=30.0,
        help="Per-attempt page.goto timeout in seconds. Default 30.",
    )
    p.add_argument(
        "--settle-ms",
        type=int,
        default=0,
        help="Extra bounded wait after hydration, 0-30000 ms (default 0).",
    )
    p.add_argument(
        "--dismiss-banners",
        action="store_true",
        default=False,
        help="Try a closed list of common consent-banner accept buttons before capture.",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Replace Keen-generated artifacts already present under --out.",
    )


_GENERATED_CAPTURE_ENTRIES = (
    "screens",
    "dom",
    "components",
    "analysis",
    "tokens",
    "system",
    "report.json",
    "report.full.json",
    "agent-brief.json",
    "summary.md",
    "report.html",
    "slop.json",
    "slop.md",
    "taste.json",
    "taste.md",
    "capture-manifest.json",
)


def _prepare_capture_outdir(outdir: Path, *, overwrite: bool) -> None:
    """Prevent stale artifacts from leaking into a new capture run."""
    if outdir.is_symlink():
        raise SystemExit(f"refusing symlink output directory: {outdir}")
    existing = [outdir / name for name in _GENERATED_CAPTURE_ENTRIES if (outdir / name).exists()]
    if existing and not overwrite:
        names = ", ".join(path.name for path in existing[:5])
        logger.error(
            "output directory contains Keen artifacts (%s); use a fresh --out or --overwrite",
            names,
        )
        raise SystemExit(2)
    if overwrite:
        for path in existing:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
    outdir.mkdir(parents=True, exist_ok=True)


def _do_capture(args: argparse.Namespace, outdir: Path) -> Path:
    _prepare_capture_outdir(outdir, overwrite=bool(getattr(args, "overwrite", False)))
    auth_steps_path = _resolve_auth_steps(args)
    auth_path = None if auth_steps_path is not None else _resolve_auth_script(args)
    settle_ms = int(getattr(args, "settle_ms", 0))
    goto_timeout = float(getattr(args, "goto_timeout", 30.0))
    if not 0 <= settle_ms <= 30_000:
        logger.error("--settle-ms must be between 0 and 30000")
        raise SystemExit(2)
    if goto_timeout <= 0:
        logger.error("--goto-timeout must be greater than zero")
        raise SystemExit(2)
    cfg = capture_mod.CaptureConfig(
        target=_normalize_capture_target(args.target),
        viewports=_csv(args.viewports),
        states=_csv(args.states),
        full_page=args.full_page,
        wait_selector=args.wait_selector,
        auth_steps_path=auth_steps_path,
        auth_script=auth_path,
        outdir=outdir,
        viewport_config=Path(args.viewport_config) if args.viewport_config else None,
        states_config=Path(args.states_config) if args.states_config else None,
        allow_internal=getattr(args, "allow_internal", False),
        allow_file=getattr(args, "allow_file", False),
        goto_timeout_ms=int(goto_timeout * 1000),
        settle_ms=settle_ms,
        dismiss_banners=bool(getattr(args, "dismiss_banners", False)),
    )
    try:
        capture_mod.run(cfg)
    except (ValueError, capture_mod.CaptureFailure) as e:
        # Surface SSRF / config validation errors as clean CLI failures (exit 1)
        # rather than letting an asyncio traceback bubble out.
        logger.error("%s", e)
        raise SystemExit(1) from None
    return outdir


def _focus_coverage_for(captures_dir: Path, dom_filename: str) -> dict[str, int]:
    dom_path = captures_dir / "dom" / dom_filename
    if not dom_path.exists():
        return {}
    try:
        data = json.loads(dom_path.read_text())
        return data.get("focus_coverage", {}) or {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("could not read focus_coverage from %s: %s", dom_path, e)
        return {}


def _do_decompose(captures_dir: Path) -> list[Path]:
    """Decompose every dom/*.json under captures_dir; write to components/."""
    written: list[Path] = []
    components_dir = captures_dir / "components"
    components_dir.mkdir(parents=True, exist_ok=True)
    dom_dir = captures_dir / "dom"
    if not dom_dir.exists():
        return written
    for dom_path in sorted(dom_dir.glob("*.json")):
        components_path = components_dir / dom_path.name
        components = decompose_mod.decompose(dom_path)
        components_path.write_text(json.dumps([c.to_dict() for c in components], indent=2))
        written.append(components_path)
    return written


def _do_analyze(captures_dir: Path, target_system: str | None) -> list[Path]:
    """Analyze every components/*.json; write to analysis/."""
    written: list[Path] = []
    analysis_dir = captures_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    components_dir = captures_dir / "components"
    if not components_dir.exists():
        return written
    for components_path in sorted(components_dir.glob("*.json")):
        analysis_path = analysis_dir / components_path.name
        focus_cov = _focus_coverage_for(captures_dir, components_path.name)
        analysis = analyze_mod.analyze_file(
            components_path,
            target_system=target_system,
            captures_dir=captures_dir,
            focus_coverage=focus_cov,
        )
        analysis_path.write_text(json.dumps(analysis, indent=2))
        written.append(analysis_path)
    return written


def _write_slop(captures_dir: Path) -> Path:
    """Compute the slop report for a run dir and write slop.json + slop.md."""
    sr = slop_mod.analyze_slop(captures_dir)
    out_json = captures_dir / "slop.json"
    out_json.write_text(json.dumps(sr.to_dict(), indent=2) + "\n")
    (captures_dir / "slop.md").write_text(slop_mod.render_markdown(sr))
    logger.info("slop: score=%s (%s) → %s", sr.score, sr.band, out_json.relative_to(captures_dir))
    return out_json


def _write_taste(captures_dir: Path) -> Path:
    """Compute the taste DNA vector for a run dir and write taste.json + taste.md."""
    vec = taste_mod.extract_taste(captures_dir)
    out_json = captures_dir / "taste.json"
    out_json.write_text(json.dumps(vec.to_dict(), indent=2) + "\n")
    (captures_dir / "taste.md").write_text(taste_mod.render_taste_card(vec))
    logger.info(
        "taste: archetype=%s temperature=%s distinctiveness=%s",
        vec.archetype_hint,
        vec.color_temperature,
        vec.scores.get("distinctiveness", 0),
    )
    return out_json


def _do_tokens(captures_dir: Path, target_system: str | None) -> Path:
    tokens_dir = captures_dir / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    detected = tokens_mod.extract_from_captures(captures_dir)
    extracted_path = tokens_dir / "extracted.json"
    extracted_path.write_text(json.dumps(detected, indent=2))
    if target_system:
        try:
            drift = tokens_mod.compare_to_system(detected, target_system)
            (tokens_dir / "comparison.json").write_text(json.dumps(drift, indent=2))
            (tokens_dir / "drift.md").write_text(tokens_mod.render_drift(drift))
        except ValueError as e:
            logger.error("tokens: could not compare to system: %s", e)
    tokens_mod.write_palette(detected, tokens_dir / "palette.png")
    return extracted_path


# -- Commands -------------------------------------------------------------


def cmd_review(args: argparse.Namespace) -> int:
    target_system = validate_system(args.against)
    outdir = Path(args.out) if args.out else _default_outdir("review")
    outdir.mkdir(parents=True, exist_ok=True)

    logger.info("capture target=%s -> %s", redact_url(args.target), outdir)
    with stage("capture"):
        _do_capture(args, outdir)

    logger.info("decompose %s", outdir)
    with stage("decompose"):
        component_files = _do_decompose(outdir)
    logger.info("decompose: wrote %d component files", len(component_files))

    logger.info("tokens: extracting")
    with stage("tokens"):
        _do_tokens(outdir, target_system)

    logger.info("analyze: target_system=%s", target_system)
    with stage("analyze"):
        analysis_files = _do_analyze(outdir, target_system=target_system)
    logger.info("analyze: wrote %d analysis files", len(analysis_files))

    logger.info("report: composing")
    with stage("report"):
        rep = report_mod.compose(outdir, target_system=target_system)
        (outdir / "report.json").write_text(json.dumps(rep, indent=2))
        (outdir / "summary.md").write_text(report_mod.render_summary(rep))

    logger.info("slop: scoring")
    with stage("slop"):
        _write_slop(outdir)

    logger.info("taste: extracting DNA")
    with stage("taste"):
        _write_taste(outdir)

    logger.info("done. output: %s", outdir)
    logger.info(
        "  report.json    grade=%s, damage=%s",
        rep["score"].get("grade"),
        rep["score"].get("score"),
    )
    logger.info("  summary.md")
    logger.info("  screens/       full screenshots + annotated overviews")
    logger.info("  components/    cropped per-component PNGs")
    logger.info(
        "  tokens/        extracted.json%s",
        ", drift.md" if target_system else "",
    )
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    outdir = Path(args.out) if args.out else _default_outdir("captures")
    with stage("capture"):
        _do_capture(args, outdir)
    logger.info("captures written to %s", outdir)
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    target_system = validate_system(args.against)
    target = Path(args.target)
    if not target.exists():
        logger.error("%s does not exist", target)
        raise SystemExit(2)

    captures_dir = target if target.is_dir() else target.parent
    with stage("decompose"):
        _do_decompose(captures_dir)
    with stage("tokens"):
        _do_tokens(captures_dir, target_system)
    with stage("analyze"):
        _do_analyze(captures_dir, target_system=target_system)
    with stage("report"):
        rep = report_mod.compose(captures_dir, target_system=target_system)
        (captures_dir / "report.json").write_text(json.dumps(rep, indent=2))
        (captures_dir / "summary.md").write_text(report_mod.render_summary(rep))
    with stage("slop"):
        _write_slop(captures_dir)
    with stage("taste"):
        _write_taste(captures_dir)
    logger.info("audit written to %s", captures_dir)
    logger.info(
        "  grade=%s, damage=%s",
        rep["score"].get("grade"),
        rep["score"].get("score"),
    )
    return 0


def cmd_tokens(args: argparse.Namespace) -> int:
    target_system = validate_system(args.against)
    outdir = Path(args.out) if args.out else _default_outdir("tokens")
    outdir.mkdir(parents=True, exist_ok=True)
    src = Path(args.target)

    with stage("tokens"):
        if src.is_file():
            detected = tokens_mod.extract_from_image(src)
        else:
            detected = tokens_mod.extract_from_captures(src)

        tokens_dir = outdir / "tokens"
        tokens_dir.mkdir(parents=True, exist_ok=True)
        (tokens_dir / "extracted.json").write_text(json.dumps(detected, indent=2))
        if target_system:
            try:
                drift = tokens_mod.compare_to_system(detected, target_system)
                (tokens_dir / "comparison.json").write_text(json.dumps(drift, indent=2))
                (tokens_dir / "drift.md").write_text(tokens_mod.render_drift(drift))
            except ValueError as e:
                logger.error("tokens: %s", e)
        tokens_mod.write_palette(detected, tokens_dir / "palette.png")
    logger.info("tokens written to %s", tokens_dir)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    if not args.against:
        logger.error("--against <system> is required for compare")
        raise SystemExit(2)
    return cmd_review(args)


def cmd_systemize(args: argparse.Namespace) -> int:
    from . import systemize as systemize_module

    run_dir = Path(args.target)
    if not run_dir.exists():
        logger.error("run directory does not exist: %s", run_dir)
        raise SystemExit(2)
    with stage("systemize"):
        result = systemize_module.systemize_run(run_dir, name=args.name)
    logger.info("proposed system '%s' written to:", args.name)
    for kind, path in result["artifacts"].items():
        logger.info("  %-13s %s", kind, path)

    p = result["proposal"]
    logger.info("summary:")
    logger.info("  spacing grid:   %spx", p["spacing"]["grid_px"])
    logger.info(
        "  type sizes:     %d steps (ratio %s, anchor %spx)",
        len(p["type"].get("sizes", [])),
        p["type"].get("ratio"),
        p["type"].get("body_px"),
    )
    logger.info("  radii:          %d steps", len(p["radii"].get("scale", [])))
    if p["colors"].get("suggested_roles"):
        logger.info(
            "  color roles:    %d suggested (agent should refine names + add semantic colors)",
            len(p["colors"]["suggested_roles"]),
        )
    if p.get("notes"):
        logger.info("cleanup targets:")
        for n in p["notes"]:
            logger.info("  - %s", n)
    logger.info(
        "open the preview HTML and review the JSON/Markdown as a provisional proposal. "
        "custom systems remain project-owned and are not registered as --against targets; "
        "refine the JSON, then run `keen validate-system` and `keen preview-system`."
    )
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    from . import diff as diff_module

    a, b = Path(args.run_a), Path(args.run_b)
    for label, path in [("run_a", a), ("run_b", b)]:
        if not (path / "report.json").exists():
            logger.error("%s has no report.json: %s", label, path)
            raise SystemExit(2)
    out_path = Path(args.out) if args.out else (b / "diff.md")
    result = diff_module.diff_runs(a, b)
    out_path.write_text(diff_module.render_markdown(result))
    logger.info("diff written to %s", out_path)
    logger.info(
        "  +%d added, -%d removed, =%d unchanged",
        len(result["added"]),
        len(result["removed"]),
        len(result["unchanged"]),
    )
    return 0


def cmd_intent(args: argparse.Namespace) -> int:
    """Emit bounded, descriptive evidence for an intent walk.

    The CLI does not emit model instructions or attempt a critique. The
    canonical skill owns interpretation and can use these stable fields as
    pre-walk context.

    NOTE: this command intentionally uses `print(...)` so the JSON payload lands
    on stdout for downstream consumers, while all other output goes via logger
    to stderr.
    """
    target = Path(args.target)
    if target.is_file() and target.suffix == ".png":
        # Single-image intent walk
        out = {
            "mode": "single-image",
            "image": str(target),
            "evidence_scope": "visual-only",
            "stable_ids": False,
            "limitations": [
                "No DOM measurements or stable component IDs are available.",
                "Product intent must be stated by the user or labeled as an inference.",
            ],
        }
        print(json.dumps(out, indent=2))
        return 0

    if not (target / "report.json").exists():
        logger.error("no report.json under %s", target)
        raise SystemExit(2)
    report = json.loads((target / "report.json").read_text())
    captures = report.get("captures", [])
    annotated = report.get("annotated_overviews", {})
    top = report.get("top_findings", [])
    out = {
        "mode": "run-dir",
        "run_dir": str(target),
        "captures": captures,
        "annotated_overviews": annotated,
        "top_findings": top[:10],
        "grade": report.get("score", {}).get("grade"),
        "evidence_contract": {
            "finding_key": "predicate_id",
            "component_key": "component_id",
            "image_key": "crop_path",
            "scope_keys": ["viewport", "state"],
        },
        "limitations": [
            "Automated findings measure rendered output; they do not establish product intent.",
            "Missing capture states must be reported as coverage gaps, not inferred as passing.",
        ],
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_taste(args: argparse.Namespace) -> int:
    """Extract the taste DNA vector for a run directory.

    Writes taste.json + taste.md next to report.json. Prints the markdown
    card (default) or the JSON (when --json is passed) to stdout.
    """
    captures_dir = Path(args.target)
    if not captures_dir.exists():
        logger.error("%s does not exist", captures_dir)
        raise SystemExit(2)
    if not (captures_dir / "components").exists():
        logger.error(
            "%s has no components/ subdirectory; run `keen review` or "
            "`keen audit` against it first",
            captures_dir,
        )
        raise SystemExit(2)
    vec = taste_mod.extract_taste(captures_dir)
    card = taste_mod.render_taste_card(vec)
    (captures_dir / "taste.json").write_text(json.dumps(vec.to_dict(), indent=2) + "\n")
    (captures_dir / "taste.md").write_text(card)
    logger.info(
        "taste: archetype=%s temperature=%s distinctiveness=%s",
        vec.archetype_hint,
        vec.color_temperature,
        vec.scores.get("distinctiveness", 0),
    )
    if args.json:
        print(json.dumps(vec.to_dict(), indent=2))
    else:
        print(card)
    return 0


def cmd_slop(args: argparse.Namespace) -> int:
    """Compute the slop score for an existing run directory.

    Writes slop.json and slop.md next to report.json. Exits 0 unless the
    run directory is malformed (missing components/ + tokens/).
    """
    captures_dir = Path(args.target)
    if not captures_dir.exists():
        logger.error("%s does not exist", captures_dir)
        raise SystemExit(2)
    if not (captures_dir / "components").exists():
        logger.error(
            "%s has no components/ subdirectory; run `keen review` or "
            "`keen audit` against it first",
            captures_dir,
        )
        raise SystemExit(2)
    out = _write_slop(captures_dir)
    if args.print_markdown:
        print((captures_dir / "slop.md").read_text())
    else:
        print(json.dumps(json.loads(out.read_text()), indent=2))
    return 0


def cmd_derive_palette(args: argparse.Namespace) -> int:
    """Run the derive-palette subcommand."""
    from harness.derive_palette import derive, palette_with_contrast

    out_dir = Path(args.out) if args.out else Path(".keen") / "palettes" / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        p = derive(seed=args.seed, strategy=args.strategy, steps=args.steps)
    except ValueError as e:
        logger.error("derive-palette failed: %s", e)
        return 2  # CLI misuse

    table = palette_with_contrast(p)
    (out_dir / "palette.json").write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    logger.info("derive-palette: wrote %s", out_dir / "palette.json")
    return 0


def cmd_validate_system(args: argparse.Namespace) -> int:
    """Run the validate-system subcommand.

    Exit codes:
        0 = system valid (no P0 findings)
        1 = system has P0 findings
        2 = CLI misuse (file not found, malformed JSON, etc.)
    """
    from harness.validate_system import emit_contrast_matrix
    from harness.validate_system import validate_system as run_validation

    try:
        system = json.loads(Path(args.system_json).read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error("validate-system: cannot read %s: %s", args.system_json, e)
        return 2

    findings = run_validation(system, archetype=args.archetype, strict=args.strict)
    matrix = emit_contrast_matrix(system)

    # Stdout: one line per finding, plus a final tally.
    for f in findings:
        print(f"[{f['severity']}] {f['predicate_id']}: {f['message']}")
    has_p0 = any(f["severity"] == "P0" for f in findings)
    p0_count = sum(1 for f in findings if f["severity"] == "P0")
    print(f"\nvalidate-system: {len(findings)} findings ({p0_count} P0)")

    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        validation_path = out / "validation.json"
        matrix_path = out / "contrast-matrix.json"
        validation_path.write_text(json.dumps({"findings": findings}, indent=2) + "\n")
        logger.info("validate-system: wrote %s", validation_path)
        matrix_path.write_text(json.dumps(matrix, indent=2) + "\n")
        logger.info("validate-system: wrote %s", matrix_path)

    return 1 if has_p0 else 0


def cmd_preview_system(args: argparse.Namespace) -> int:
    """Render preview.html (+ optional comparison.html + system.md) for a system JSON.

    Exit codes:
        0 = success
        2 = CLI misuse (file not found, malformed JSON)
    """
    from harness.preview_system import (
        builtin_reference_system,
        render_comparison,
        render_dashboard_mockup,
        render_form_mockup,
        render_landing_mockup,
        render_preview,
        render_system_md,
    )

    try:
        system = json.loads(Path(args.system_json).read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error("preview-system: cannot read %s: %s", args.system_json, e)
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    (out / "preview.html").write_text(render_preview(system))
    logger.info("preview-system: wrote %s", out / "preview.html")

    (out / "system.md").write_text(render_system_md(system))
    logger.info("preview-system: wrote %s", out / "system.md")

    # Mockups: real UI archetypes rendered with the system's tokens. Disabled
    # by default to keep `preview-system` cheap; opt in via --mockups.
    if getattr(args, "mockups", False):
        mock_dir = out / "mockups"
        mock_dir.mkdir(parents=True, exist_ok=True)
        (mock_dir / "landing.html").write_text(render_landing_mockup(system))
        (mock_dir / "dashboard.html").write_text(render_dashboard_mockup(system))
        (mock_dir / "form.html").write_text(render_form_mockup(system))
        logger.info(
            "preview-system: wrote 3 mockups → %s",
            mock_dir.relative_to(out.parent) if out.parent != Path(".") else mock_dir,
        )

    if args.compare_with:
        ref_names = [n.strip() for n in args.compare_with.split(",") if n.strip()]
        refs: list[dict[str, Any]] = []
        labels: list[str] = ["proposal"]
        for name in ref_names:
            ref_json = asset_path("references", "design-systems", f"{name}.json")
            if not ref_json.exists():
                builtin = builtin_reference_system(name)
                if builtin is None:
                    logger.warning(
                        "preview-system: skipping reference %r (no JSON at %s)",
                        name,
                        ref_json,
                    )
                    continue
                refs.append(builtin)
                labels.append(name)
                continue
            try:
                refs.append(json.loads(ref_json.read_text()))
                labels.append(name)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("preview-system: cannot read %s: %s; skipping", ref_json, e)
        if refs:
            (out / "comparison.html").write_text(render_comparison(system, refs, labels))
            logger.info("preview-system: wrote %s", out / "comparison.html")
        else:
            logger.warning(
                "preview-system: no valid references to compare with; comparison.html not written"
            )

    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Verify the harness is wired up correctly.

    Prints PASS/FAIL per check with an actionable fix. Exits 0 on all-clear, 1
    if any check fails.
    """
    results: list[tuple[str, bool, str]] = []

    # 1. Python version
    py_ok = sys.version_info >= (3, 10)
    results.append(
        (
            "python>=3.10",
            py_ok,
            ("ok ({}.{}.{})".format(*sys.version_info[:3]))
            if py_ok
            else "fix: upgrade Python to >= 3.10",
        )
    )

    # 2. Playwright import
    try:
        import playwright  # noqa: F401

        pw_ok = True
        pw_msg = "ok"
    except ImportError as e:
        pw_ok = False
        pw_msg = f"fix: pip install playwright ({e})"
    results.append(("import playwright", pw_ok, pw_msg))

    # 3. Playwright browser driver
    drv_ok = False
    drv_msg = "fix: playwright install chromium"
    if pw_ok:
        try:
            from playwright._impl._driver import compute_driver_executable

            exe = compute_driver_executable()
            # compute_driver_executable returns a tuple of (driver, args)
            # depending on version. We only care that it returns something.
            drv_ok = exe is not None
            drv_msg = "ok" if drv_ok else "fix: playwright install chromium"
        except Exception as e:
            drv_msg = f"fix: playwright install chromium ({e})"
    results.append(("playwright driver", drv_ok, drv_msg))

    # 4. Config files parse successfully
    config_files = {
        "config/rubric.yaml": ("yaml",),
        "config/states.json": ("json",),
        "config/viewports.json": ("json",),
    }
    for rel, (kind,) in config_files.items():
        path = asset_path(*rel.split("/"))
        ok = False
        msg = f"fix: create {rel}"
        if not path.exists():
            msg = f"fix: create {rel} (missing at {path})"
        else:
            try:
                txt = path.read_text()
                if kind == "json":
                    json.loads(txt)
                else:
                    import yaml

                    yaml.safe_load(txt)
                ok = True
                msg = "ok"
            except ImportError:
                msg = "fix: pip install pyyaml"
            except (OSError, json.JSONDecodeError) as e:
                msg = f"fix: {rel} failed to parse ({e})"
            except Exception as e:  # yaml.YAMLError lives under yaml
                msg = f"fix: {rel} failed to parse ({e})"
        results.append((rel, ok, msg))
    # 5. design-systems references non-empty
    refs_dir = asset_path("references", "design-systems")
    if refs_dir.exists():
        mds = list(refs_dir.glob("*.md"))
        refs_ok = len(mds) > 0
        refs_msg = (
            f"ok ({len(mds)} systems)"
            if refs_ok
            else "fix: add at least one .md under references/design-systems/"
        )
    else:
        refs_ok = False
        refs_msg = f"fix: missing directory {refs_dir}"
    results.append(("references/design-systems", refs_ok, refs_msg))

    # 6. Default outdir writable
    outdir = Path(".keen")
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        probe = outdir / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        out_ok = True
        out_msg = f"ok ({outdir.resolve()})"
    except OSError as e:
        out_ok = False
        out_msg = f"fix: cannot write to {outdir} ({e})"
    results.append((".keen/ writable", out_ok, out_msg))

    # 7. derive_palette module imports cleanly
    try:
        import harness.derive_palette

        dp_ok = harness.derive_palette is not None
        dp_msg = "ok"
    except ImportError as e:
        dp_ok = False
        dp_msg = f"fix: ensure harness/derive_palette.py is present ({e})"
    results.append(("import derive_palette", dp_ok, dp_msg))

    # 8. validate_system module imports cleanly
    try:
        import harness.validate_system

        vs_ok = harness.validate_system is not None
        vs_msg = "ok"
    except ImportError as e:
        vs_ok = False
        vs_msg = f"fix: ensure harness/validate_system.py is present ({e})"
    results.append(("import validate_system", vs_ok, vs_msg))

    # 9. preview_system module imports cleanly
    try:
        import harness.preview_system

        ps_ok = harness.preview_system is not None
        ps_msg = "ok"
    except ImportError as e:
        ps_ok = False
        ps_msg = f"fix: ensure harness/preview_system.py is present ({e})"
    results.append(("import preview_system", ps_ok, ps_msg))

    # Render
    all_ok = all(ok for _, ok, _ in results)
    for name, ok, msg in results:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {msg}")

    if all_ok:
        print("\nAll checks passed.")
        return 0
    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(failed)} check(s) failed: {', '.join(failed)}")
    return 1


# -- Parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    available = list_available_systems()
    systems_help = f"Available: {', '.join(available)}" if available else ""

    p = argparse.ArgumentParser(
        prog="keen",
        description="Keen deterministic UI/UX review engine",
    )
    p.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v for debug).",
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress info-level output (errors still shown).",
    )
    p.add_argument(
        "--profile",
        action="store_true",
        default=False,
        help=(
            "Time each pipeline stage and print a summary table at the end. "
            "Forces per-stage timing logs to INFO regardless of -q/-v."
        ),
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("review", help="full pipeline: capture → decompose → analyze → report")
    _add_capture_args(pr)
    pr.add_argument("--out", default=None)
    pr.add_argument("--against", default=None, help=f"Audit against this system. {systems_help}")
    pr.set_defaults(func=cmd_review)

    pc = sub.add_parser("capture", help="capture only")
    _add_capture_args(pc)
    pc.add_argument("--out", default=None)
    pc.set_defaults(func=cmd_capture)

    pa = sub.add_parser("audit", help="analyze captures that already exist")
    pa.add_argument("target", help="path to a captures directory")
    pa.add_argument("--against", default=None, help=f"Audit against this system. {systems_help}")
    pa.add_argument(
        "--allow-internal",
        action="store_true",
        default=False,
        help="Accepted for parity with capture; ignored by audit.",
    )
    pa.add_argument(
        "--allow-file",
        action="store_true",
        default=False,
        help="Accepted for parity with capture; ignored by audit.",
    )
    pa.set_defaults(func=cmd_audit)

    pt = sub.add_parser("tokens", help="extract design tokens")
    pt.add_argument("target", help="screenshot file or captures directory")
    pt.add_argument("--against", default=None, help=f"Compare against this system. {systems_help}")
    pt.add_argument("--out", default=None)
    pt.set_defaults(func=cmd_tokens)

    pcomp = sub.add_parser("compare", help="review against a specific design system")
    _add_capture_args(pcomp)
    pcomp.add_argument("--out", default=None)
    pcomp.add_argument("--against", required=True, help=f"Required. {systems_help}")
    pcomp.set_defaults(func=cmd_compare)

    psys = sub.add_parser(
        "systemize",
        help="propose a coherent design system from observed tokens in a run directory",
    )
    psys.add_argument("target", help="path to a run directory (output of capture/review)")
    psys.add_argument(
        "--name", default="custom", help="short name for the proposed system (used in filenames)"
    )
    psys.set_defaults(func=cmd_systemize)

    pd = sub.add_parser("diff", help="diff findings between two runs")
    pd.add_argument("run_a", help="baseline run directory")
    pd.add_argument("run_b", help="newer run directory")
    pd.add_argument("--out", default=None, help="output markdown path (default: <run_b>/diff.md)")
    pd.set_defaults(func=cmd_diff)

    pi = sub.add_parser("intent", help="emit bounded evidence for an intent walk")
    pi.add_argument("target", help="run directory or single screenshot path")
    pi.set_defaults(func=cmd_intent)

    psl = sub.add_parser(
        "slop",
        help="score a run directory for AI-design slop fingerprints (0–100)",
    )
    psl.add_argument("target", help="path to a run directory (output of capture/review/audit)")
    psl.add_argument(
        "--print-markdown",
        action="store_true",
        default=False,
        help="print the markdown report instead of slop.json",
    )
    psl.set_defaults(func=cmd_slop)

    ptaste = sub.add_parser(
        "taste",
        help="extract the taste DNA fingerprint (color/type/shape/depth) for a run",
    )
    ptaste.add_argument("target", help="path to a run directory (output of capture/review/audit)")
    ptaste.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="print taste.json instead of the markdown card",
    )
    ptaste.set_defaults(func=cmd_taste)

    pdp = sub.add_parser(
        "derive-palette",
        help="derive an OKLCH-uniform palette from a brand seed (deterministic)",
    )
    pdp.add_argument("--seed", required=True, help="brand seed in #RRGGBB form")
    pdp.add_argument(
        "--strategy",
        default="monochromatic",
        choices=sorted(
            [
                "monochromatic",
                "complementary",
                "triadic",
                "split-complementary",
                "analogous",
                "tetradic",
            ]
        ),
        help="palette-derivation strategy (default: monochromatic)",
    )
    pdp.add_argument(
        "--steps",
        type=int,
        default=11,
        help="number of lightness steps in the primary ramp (default: 11)",
    )
    pdp.add_argument(
        "--name",
        default="palette",
        help="palette name for output filenames",
    )
    pdp.add_argument(
        "--out",
        default=None,
        help="output dir (default: .keen/palettes/<name>/)",
    )
    pdp.set_defaults(func=cmd_derive_palette)

    pvs = sub.add_parser(
        "validate-system",
        help="run system-level predicates against a proposed system JSON",
    )
    pvs.add_argument("system_json", help="path to a system JSON to validate")
    pvs.add_argument(
        "--archetype",
        default=None,
        help="archetype name (enables archetype-specific predicates)",
    )
    pvs.add_argument(
        "--strict",
        action="store_true",
        help="treat P1 findings as P0 (fail-closed for refine loop)",
    )
    pvs.add_argument(
        "--out",
        default=None,
        help="optional dir for validation.json + contrast-matrix.json",
    )
    pvs.set_defaults(func=cmd_validate_system)

    pps = sub.add_parser(
        "preview-system",
        help=("render preview HTML (+ optional side-by-side comparison) for a system JSON"),
    )
    pps.add_argument("system_json", help="path to a system JSON")
    pps.add_argument(
        "--compare-with",
        default=None,
        help="comma-separated reference system names to compare against",
    )
    pps.add_argument(
        "--out",
        required=True,
        help=(
            "output dir (will contain preview.html, system.md, and "
            "comparison.html when --compare-with is given)"
        ),
    )
    pps.add_argument(
        "--mockups",
        action="store_true",
        default=False,
        help=(
            "also render landing.html / dashboard.html / form.html mockups "
            "under <out>/mockups/, using the system's tokens applied to "
            "fixed UI archetypes"
        ),
    )
    pps.set_defaults(func=cmd_preview_system)

    pdoc = sub.add_parser("doctor", help="verify install: python, playwright, configs")
    pdoc.set_defaults(func=cmd_doctor)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = getattr(args, "profile", False)
    _configure_logging(getattr(args, "verbose", 0), getattr(args, "quiet", False), profile=profile)
    if profile:
        _timing.enable_profiling()
    t_start = time.monotonic()
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        logger.error("interrupted")
        return 1
    except Exception as e:
        logger.exception("unhandled error: %s", e)
        return 1
    finally:
        if profile:
            total_ms = int((time.monotonic() - t_start) * 1000)
            timings = _timing.drain_summary()
            # Print to stderr so it doesn't interfere with stdout consumers
            # (e.g. `keen intent` which writes JSON to stdout).
            sys.stderr.write(_timing.format_summary(timings, total_ms=total_ms))


if __name__ == "__main__":
    raise SystemExit(main())
