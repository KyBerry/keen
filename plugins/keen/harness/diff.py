"""Diff two Keen runs and surface what changed.

The point: when iterating on fixes, the user shouldn't re-read the same 14
findings every cycle. They should see "you fixed X, Y, Z; A regressed; B is
still broken." That makes iteration tight.

A finding is keyed by its predicate and production component identity. The
capture path scopes an index when it is available; older reports fall back to
viewport/state scope and the legacy ``component_index`` field. We deliberately
do *not* hash the message text — that would split "ratio 4.49" and "ratio 4.55"
into removed+added when really the issue is the same finding with a different
measurement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from harness import _artifacts as artifacts_mod


def _capture_scope(component: dict[str, Any]) -> str:
    """Return the narrowest stable capture scope present in a report."""
    capture_path = component.get("capture_path")
    if capture_path:
        return str(capture_path)
    return f"{component.get('viewport', '')}|{component.get('state', '')}"


def _component_index(component: dict[str, Any]) -> Any:
    """Read production ``index`` while remaining compatible with old runs."""
    if "index" in component:
        return component["index"]
    return component.get("component_index", "")


def _load_findings(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in report.get("components", []):
        capture_scope = _capture_scope(c)
        component_index = _component_index(c)
        for f in c.get("findings", []):
            key = "|".join(
                [
                    f.get("predicate_id", ""),
                    c.get("component_kind", ""),
                    capture_scope,
                    c.get("viewport", ""),
                    c.get("state", ""),
                    str(component_index),
                ]
            )
            out.setdefault(
                key,
                {
                    "predicate_id": f.get("predicate_id"),
                    "severity": f.get("severity"),
                    "component_kind": c.get("component_kind"),
                    "viewport": c.get("viewport"),
                    "state": c.get("state"),
                    "capture_scope": capture_scope,
                    "component_index": component_index,
                    "message": f.get("message"),
                    "crop_path": c.get("crop_path"),
                },
            )
    return out


def diff_runs(run_a: Path, run_b: Path) -> dict[str, Any]:
    rep_a = artifacts_mod.read_report_document(run_a / "report.json")
    rep_b = artifacts_mod.read_report_document(run_b / "report.json")
    a = _load_findings(rep_a)
    b = _load_findings(rep_b)
    a_keys, b_keys = set(a), set(b)

    added = [b[k] for k in sorted(b_keys - a_keys)]
    removed = [a[k] for k in sorted(a_keys - b_keys)]
    unchanged = [b[k] for k in sorted(b_keys & a_keys)]

    return {
        "run_a": str(run_a),
        "run_b": str(run_b),
        "score_a": rep_a.get("score", {}),
        "score_b": rep_b.get("score", {}),
        "added": added,
        "removed": removed,
        "unchanged": unchanged,
    }


def render_markdown(result: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Keen diff")
    lines.append("")
    sa, sb = result["score_a"], result["score_b"]
    delta = sb.get("score", 0) - sa.get("score", 0)
    arrow = "↘" if delta < 0 else ("↗" if delta > 0 else "→")
    lines.append(
        f"**Damage:** {sa.get('score', 0)} {arrow} {sb.get('score', 0)} "
        f"({delta:+.1f})  •  Grade {sa.get('grade', '?')} → {sb.get('grade', '?')}"
    )
    lines.append("")
    lines.append(f"**A:** `{result['run_a']}`")
    lines.append(f"**B:** `{result['run_b']}`")
    lines.append("")

    if result["removed"]:
        lines.append(f"## Fixed in B ({len(result['removed'])})")
        lines.append("")
        for f in result["removed"][:50]:
            lines.append(
                f"- ✅ **[{f['severity']}]** `{f['predicate_id']}` "
                f"({f['component_kind']}, {f['viewport']}/{f['state']}) — {f['message']}"
            )
        if len(result["removed"]) > 50:
            lines.append(f"- _… and {len(result['removed']) - 50} more_")
        lines.append("")

    if result["added"]:
        lines.append(f"## New in B ({len(result['added'])})")
        lines.append("")
        for f in result["added"][:50]:
            crop = f" `{f['crop_path']}`" if f.get("crop_path") else ""
            lines.append(
                f"- 🔴 **[{f['severity']}]** `{f['predicate_id']}` "
                f"({f['component_kind']}, {f['viewport']}/{f['state']}){crop} — {f['message']}"
            )
        if len(result["added"]) > 50:
            lines.append(f"- _… and {len(result['added']) - 50} more_")
        lines.append("")

    lines.append(f"## Still present ({len(result['unchanged'])})")
    lines.append("")
    if not result["unchanged"]:
        lines.append("_(none — clean run)_")
    else:
        for f in result["unchanged"][:25]:
            lines.append(
                f"- ⏸ **[{f['severity']}]** `{f['predicate_id']}` "
                f"({f['component_kind']}, {f['viewport']}/{f['state']})"
            )
        if len(result["unchanged"]) > 25:
            lines.append(f"- _… and {len(result['unchanged']) - 25} more_")

    return "\n".join(lines) + "\n"
