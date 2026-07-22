"""Rubric: turn raw findings into a scored, ranked summary.

Every finding has a severity (P0/P1/P2); weights and grade thresholds come
from config/rubric.yaml (or sensible defaults if PyYAML/the file isn't
available). The score is a damage number — lower is better.

We also produce an "issue density" map: components-with-findings / total
components, broken down by component_kind. Often the most useful number in
the whole report.
"""

from __future__ import annotations

import logging
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from harness._assets import asset_path

logger = logging.getLogger("keen")


DEFAULT_WEIGHTS = {"P0": 10.0, "P1": 3.0, "P2": 1.0}

# Scoring v2 calibration. The legacy score summed every finding, so the same
# systemic defect repeated across a long page made damage grow without bound.
# A 50-component reference surface preserves the old arithmetic for small
# reviews. Above that size, damage is density-normalized. A predicate may
# contribute for at most 10% of assessed components (with a three-hit minimum),
# which keeps a repeated pattern important without letting it drown every
# other signal.
SCORE_REFERENCE_COMPONENTS = 50
SCORE_REPEAT_DENSITY_CAP = 0.10
SCORE_MIN_REPEAT_CAP = 3
SCORE_VERSION = "density-v2"

_UNSCOPED_CAPTURE = "unscoped"
_PAGE_CAPTURE = "page"


# Default severity per predicate id. Predicates themselves construct their
# `Finding` records with the severity baked in (so the analyze stage is
# self-contained); this map is the authoritative reference for documentation,
# audits, and any future override mechanism. Keep it in sync when adding a
# new predicate to harness/analyze.py.
#
# Severity rubric:
#   P0 = WCAG A/AA conformance failure that a user definitely hits
#   P1 = WCAG A/AA failure with a narrower impact OR system/standard drift
#   P2 = WCAG AAA, polish, or heuristic/uncertain finding
PREDICATE_SEVERITIES: dict[str, str] = {
    # Existing predicates (read off harness/analyze.py at the time of audit).
    "hit-target.size": "P1",  # named-system conformance above the WCAG AA floor
    "contrast.text": "P0",  # WCAG 2.2 SC 1.4.3 AA
    "name.icon-button": "P0",  # WCAG SC 4.1.2 A
    "name.link": "P0",  # WCAG SC 4.1.2 A
    "name.link.generic": "P1",  # WCAG SC 2.4.4 (partial)
    "name.image": "P0",  # WCAG SC 1.1.1 A
    "focus.visible": "P2",  # Authored-rule scan is incomplete; native focus may conform
    "focus.no-styles": "P2",  # Native focus may conform; requires rendered verification
    "label.association": "P0",  # WCAG SC 1.3.1 / 3.3.2 A
    "input.autocomplete": "P2",  # WCAG SC 1.3.5 AA, best-effort heuristic
    "input.numeric-mode": "P2",  # Mobile UX nicety, not a WCAG failure
    "heading.hierarchy": "P1",  # WCAG SC 1.3.1 / 2.4.6 partial
    "heading.empty": "P0",  # WCAG SC 2.4.6 / 4.1.2 A
    "heading.duplicate-h1": "P1",  # SEO + heading-structure drift
    "link.distinguishable": "P1",  # WCAG SC 1.4.1 A
    "spacing.grid": "P2",  # Design-system drift
    "layout.off-canvas": "P1",  # WCAG SC 1.4.10 AA
    "tap-target.overlap": "P1",  # WCAG SC 2.5.8 AA spacing exception
    "dialog.aria-modal": "P1",  # WAI ARIA APG
    "visual-dom.background-mismatch": "P1",  # Internal cross-check
    # --- New in this audit pass ---
    # WCAG 2.4.4 Link Purpose (In Context), Level A — but our broader
    # phrase list catches softer cases, so we rate it P1 (matches the
    # existing name.link.generic neighbour).
    "name.link.context": "P1",
    # WCAG 1.4.11 Non-text Contrast, Level AA — failures here block low-
    # vision users from locating controls; P1 because borderless inputs
    # by design are increasingly common and the rule has carve-outs.
    "contrast.non-text": "P1",
    # WCAG 2.5.3 Label in Name, Level A — speech-input failure. P1
    # because the SC explicitly tolerates case/punctuation differences,
    # making automated detection inherently best-effort.
    "label-in-name": "P1",
    # WCAG 2.4.3 Focus Order, Level A (Failure F44) — positive tabindex
    # is a well-known anti-pattern. P1 (not P0) because the *actual*
    # impact depends on whether the tab order ends up sensible.
    "focus.positive-tabindex": "P1",
    # WCAG 2.5.8 Target Size (Minimum), Level AA - explicit 24x24 floor.
    # P1 because the automated geometry check cannot establish every WCAG
    # spacing exception. Named-system conformance is separately P1.
    "target.size-aa": "P1",
    # WAI ARIA APG: missing focusable descendant in a dialog. P1 because
    # it's a strong signal that focus management is unimplemented.
    "dialog.focus-trap-affordance": "P1",
    # WAI ARIA APG: only focusable element is the close button. P2
    # because the heuristic has false positives (small dialogs with a
    # genuine single close-only action are valid).
    "dialog.initial-focus": "P2",
    # axe-core landmark-one-main / WCAG 1.3.1 — P1 because navigation
    # impact is significant but the rule is widely violated by valid
    # single-page apps that haven't been fragment-captured.
    "landmark.one-main": "P1",
    # axe-core landmark-no-duplicate-banner/contentinfo — P2 because
    # duplicates are a navigation nicety, not a barrier.
    "landmark.duplicate": "P2",
    # Heuristic for WCAG 3.3.2 Labels or Instructions, Level A —
    # required state visibility. P2 because the cue can live elsewhere
    # (e.g. above the form) which static analysis can't see.
    "form.required-indicator": "P2",
    # System-level predicates (harness/validate_system.py).
    "system.roles.required-present": "P0",
    "system.roles.text-default-aa": "P0",
    "system.roles.text-subtle-aa": "P0",
    "system.roles.text-subtlest-large-only": "P1",
    "system.roles.text-disabled-not-aa": "P1",
    "system.roles.text-inverse-aa": "P0",
    "system.roles.border-focused-aa": "P0",
    "system.roles.border-focused-inverse-aa": "P0",
    "system.type.scale-monotonic": "P0",
    "system.type.ratio-consistent": "P1",
    "system.type.line-height-prose": "P1",
    "system.spacing.single-grid": "P0",
    "system.spacing.scale-monotonic": "P0",
    "system.radii.scale-monotonic": "P0",
    "system.semantic.success-not-red": "P1",
    "system.semantic.error-not-green": "P1",
    "system.archetype.primary-required": "P0",
    "system.distinctiveness.too-close-to-reference": "P1",
}


DEFAULT_GRADE_THRESHOLDS: list[dict[str, Any]] = [
    {"max_damage": 5, "grade": "A", "summary": "Production-quality. Ship-blocking issues absent."},
    {"max_damage": 15, "grade": "B", "summary": "Solid. Address the listed P1s before ship."},
    {
        "max_damage": 35,
        "grade": "C",
        "summary": "Functional but rough. Several issues affect usability or accessibility.",
    },
    {
        "max_damage": 75,
        "grade": "D",
        "summary": "Significant accessibility or quality gaps. Don't ship without addressing P0s.",
    },
    {
        "max_damage": 9999,
        "grade": "F",
        "summary": "Not ready. Fundamental accessibility or quality failures.",
    },
]

DEFAULT_COMPONENT_FILTER: dict[str, Any] = {
    "include_kinds": None,
    "exclude_kinds": ["image", "label"],
}

DEFAULT_REPORT_OPTS: dict[str, Any] = {
    "group_by": "component",
    "show_passing": False,
    "include_screenshots": True,
    "max_findings_per_component": 10,
}


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load rubric.yaml. Returns defaults if PyYAML or the file is missing.

    On any parse / type error we log a warning naming the offending key and
    fall back to the built-in defaults for that key only. Silent fallback
    has historically masked typos in the YAML — loud is better here.
    """
    if path is None:
        path = asset_path("config", "rubric.yaml")
    cfg: dict[str, Any] = {
        "severity_weights": dict(DEFAULT_WEIGHTS),
        "grade_thresholds": list(DEFAULT_GRADE_THRESHOLDS),
        "component_filter": dict(DEFAULT_COMPONENT_FILTER),
        "report": dict(DEFAULT_REPORT_OPTS),
    }
    if not path.exists():
        logger.warning(
            "rubric.yaml: file not found at %s; falling back to defaults",
            path,
        )
        return cfg
    try:
        import yaml
    except ImportError:
        logger.warning("rubric.yaml: PyYAML is not installed; falling back to defaults")
        return cfg
    try:
        loaded = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError) as e:
        logger.warning(
            "rubric.yaml: %s; falling back to defaults for the whole file",
            e,
        )
        return cfg

    if not isinstance(loaded, dict):
        logger.warning(
            "rubric.yaml: top-level value is %s, expected a mapping; falling back to defaults",
            type(loaded).__name__,
        )
        return cfg

    if "severity_weights" in loaded:
        try:
            cfg["severity_weights"] = {k: float(v) for k, v in loaded["severity_weights"].items()}
        except (TypeError, ValueError, AttributeError) as e:
            logger.warning(
                "rubric.yaml: %s; falling back to defaults for severity_weights",
                e,
            )
    if "grade_thresholds" in loaded:
        gt = loaded["grade_thresholds"]
        if isinstance(gt, list):
            cfg["grade_thresholds"] = list(gt)
        else:
            logger.warning(
                "rubric.yaml: grade_thresholds is %s, expected list; falling back to defaults for grade_thresholds",
                type(gt).__name__,
            )
    if "component_filter" in loaded:
        cf = loaded["component_filter"]
        if isinstance(cf, dict):
            cfg["component_filter"].update(cf)
        else:
            logger.warning(
                "rubric.yaml: component_filter is %s, expected mapping; falling back to defaults for component_filter",
                type(cf).__name__,
            )
    if "report" in loaded:
        rep = loaded["report"]
        if isinstance(rep, dict):
            cfg["report"].update(rep)
        else:
            logger.warning(
                "rubric.yaml: report is %s, expected mapping; falling back to defaults for report",
                type(rep).__name__,
            )
    return cfg


def grade_for(damage: float, thresholds: list[dict[str, Any]]) -> dict[str, str]:
    """Map a damage score to a grade entry."""
    for row in thresholds:
        if damage <= float(row.get("max_damage", 0)):
            return {"grade": row.get("grade", "?"), "summary": row.get("summary", "")}
    last = thresholds[-1] if thresholds else {"grade": "F", "summary": ""}
    return {"grade": last.get("grade", "F"), "summary": last.get("summary", "")}


def _viewport_state_scope(item: dict[str, Any]) -> str | None:
    viewport = item.get("viewport")
    state = item.get("state")
    if viewport is None and state is None:
        return None
    return f"{viewport or ''}/{state or ''}"


def _capture_scope(item: dict[str, Any]) -> str | None:
    capture_path = item.get("capture_path")
    if capture_path:
        return str(capture_path)
    return _viewport_state_scope(item)


def _dedupe_unscoped_global_findings(
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse the same page predicate repeated by responsive captures.

    Analysis files historically omitted capture metadata from global findings.
    One page predicate is therefore indistinguishable from three identical
    viewport copies after report composition. Predicate + severity is the
    narrow stable identity available; legacy findings without a predicate id
    remain distinct rather than being silently collapsed.
    """
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        predicate_id = finding.get("predicate_id")
        if not predicate_id:
            deduped.append(finding)
            continue
        identity = (str(predicate_id), str(finding.get("severity", "")))
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(finding)
    return deduped


def _capture_groups(
    components: list[dict[str, Any]],
    global_findings: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]:
    """Build independently gradable capture groups.

    Component findings inherit their component's capture. Scoped global
    findings join that capture. Unscoped page findings are deduplicated and
    applied once to every capture so they still combine with component damage;
    this is conservative when old reports do not expose their source capture.
    """
    components_by_capture: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    scope_aliases: dict[str, str | None] = {}
    for component in components:
        component_scope = _capture_scope(component) or _UNSCOPED_CAPTURE
        components_by_capture[component_scope].append(component)
        viewport_state = _viewport_state_scope(component)
        if viewport_state:
            existing = scope_aliases.get(viewport_state)
            if existing is None and viewport_state not in scope_aliases:
                scope_aliases[viewport_state] = component_scope
            elif existing != component_scope:
                scope_aliases[viewport_state] = None

    globals_by_capture: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    unscoped_globals: list[dict[str, Any]] = []
    for finding in global_findings:
        global_scope = _capture_scope(finding)
        if global_scope is None:
            unscoped_globals.append(finding)
            continue
        viewport_state = _viewport_state_scope(finding)
        if not finding.get("capture_path") and viewport_state:
            global_scope = scope_aliases.get(viewport_state) or global_scope
        globals_by_capture[global_scope].append(finding)
        components_by_capture.setdefault(global_scope, [])

    if not components_by_capture:
        components_by_capture[_PAGE_CAPTURE] = []
    page_findings = _dedupe_unscoped_global_findings(unscoped_globals)

    groups: list[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]] = []
    for scope in sorted(components_by_capture):
        capture_components = components_by_capture[scope]
        capture_findings = [
            finding for component in capture_components for finding in component.get("findings", [])
        ]
        capture_findings.extend(globals_by_capture.get(scope, []))
        capture_findings.extend(page_findings)
        groups.append((scope, capture_components, capture_findings))
    return groups


def _capture_damage(
    components: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    weights: dict[str, float],
) -> dict[str, float | int]:
    """Calculate calibrated damage for one concrete capture."""
    component_count = len(components)
    repeat_cap = max(
        SCORE_MIN_REPEAT_CAP,
        math.ceil(max(component_count, 1) * SCORE_REPEAT_DENSITY_CAP),
    )
    predicate_weights: defaultdict[str, list[float]] = defaultdict(list)
    for ordinal, finding in enumerate(findings):
        predicate_id = str(finding.get("predicate_id") or f"__legacy_finding_{ordinal}")
        severity = str(finding.get("severity", ""))
        predicate_weights[predicate_id].append(float(weights.get(severity, 0.0)))

    capped_damage = sum(
        sum(sorted(finding_weights, reverse=True)[:repeat_cap])
        for finding_weights in predicate_weights.values()
    )
    normalization_factor = max(1.0, component_count / SCORE_REFERENCE_COMPONENTS)
    damage = capped_damage / normalization_factor

    # A confirmed blocker must never receive the "ship-blockers absent" A
    # summary merely because it occurred once on a very large page. Keep the
    # floor proportional to custom weights so callers that deliberately set a
    # smaller P0 weight retain that behavior.
    if any(finding.get("severity") == "P0" for finding in findings):
        damage = max(damage, min(6.0, float(weights.get("P0", 0.0))))

    return {
        "damage": damage,
        "component_count": component_count,
        "repeat_cap": repeat_cap,
        "normalization_factor": normalization_factor,
        "capped_damage": capped_damage,
    }


def score(
    analysis: dict[str, Any],
    weights: dict[str, float] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = config or load_config()
    weights = weights or cfg.get("severity_weights") or DEFAULT_WEIGHTS
    components = analysis["components"]
    global_findings = analysis.get("global_findings", []) or []

    counts: Counter = Counter()
    findings: list[dict[str, Any]] = []
    for c in components:
        for f in c.get("findings", []):
            counts[f["severity"]] += 1
            findings.append(f)
    for finding in global_findings:
        counts[finding["severity"]] += 1
        findings.append(finding)

    # Preserve the legacy absolute total as a diagnostic for existing users,
    # but grade against the calibrated score below.
    raw_damage = sum(counts[s] * weights.get(s, 0) for s in counts)

    capture_scores = [
        (scope, _capture_damage(capture_components, capture_findings, weights))
        for scope, capture_components, capture_findings in _capture_groups(
            components, global_findings
        )
    ]
    worst_scope, worst_capture = max(
        capture_scores,
        key=lambda item: float(item[1]["damage"]),
    )
    damage = float(worst_capture["damage"])

    by_kind: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "with_findings": 0, "P0": 0, "P1": 0, "P2": 0}
    )
    for c in components:
        kind = c["component_kind"]
        by_kind[kind]["total"] += 1
        if c.get("findings"):
            by_kind[kind]["with_findings"] += 1
            for f in c["findings"]:
                by_kind[kind][f["severity"]] += 1

    for _, row in by_kind.items():
        row["density_pct"] = (
            round(100 * row["with_findings"] / row["total"], 1) if row["total"] else 0.0
        )

    grade = grade_for(damage, cfg.get("grade_thresholds") or DEFAULT_GRADE_THRESHOLDS)

    return {
        "score": round(damage, 1),
        "raw_score": round(raw_damage, 1),
        "counts": dict(counts),
        "weights": weights,
        "by_component_kind": dict(by_kind),
        "global_findings": len(global_findings),
        "scoring": {
            "version": SCORE_VERSION,
            "reference_components": SCORE_REFERENCE_COMPONENTS,
            "component_count": len(components),
            "graded_component_count": worst_capture["component_count"],
            "normalization_factor": round(float(worst_capture["normalization_factor"]), 3),
            "repeat_cap_per_predicate": worst_capture["repeat_cap"],
            "capped_score_before_normalization": round(float(worst_capture["capped_damage"]), 1),
            "capture_count": len(capture_scores),
            "aggregation": "worst-capture",
            "worst_capture": worst_scope,
        },
        "grade": grade["grade"],
        "grade_summary": grade["summary"],
    }


def top_findings(
    analysis: dict[str, Any],
    limit: int = 15,
    per_predicate_cap: int = 3,
) -> list[dict[str, Any]]:
    """Return the top-N findings, diversified across predicates so a single
    noisy predicate doesn't drown the report."""
    severity_rank = {"P0": 0, "P1": 1, "P2": 2}
    flat: list[dict] = []
    for c in analysis["components"]:
        for f in c.get("findings", []):
            flat.append(
                {
                    **f,
                    "component_kind": c["component_kind"],
                    "viewport": c.get("viewport"),
                    "state": c.get("state"),
                    "component_index": c.get("index"),
                    "crop_path": c.get("crop_path"),
                }
            )
    for finding in analysis.get("global_findings", []) or []:
        flat.append(
            {
                **finding,
                "component_kind": "page",
                "viewport": None,
                "state": None,
            }
        )
    flat.sort(key=lambda x: (severity_rank.get(x["severity"], 9), x.get("predicate_id", "")))

    out: list[dict] = []
    if limit <= 0:
        return out
    counts_per: dict[str, int] = defaultdict(int)
    deferred: list[dict] = []
    for f in flat:
        pid = f.get("predicate_id", "")
        if counts_per[pid] >= per_predicate_cap:
            deferred.append(f)
            continue
        out.append(f)
        counts_per[pid] += 1
        if len(out) >= limit:
            break
    if len(out) < limit:
        out.extend(deferred[: limit - len(out)])
    return out
