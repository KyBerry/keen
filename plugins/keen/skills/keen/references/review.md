# Review guide

Use this guide after `keen review`, `audit`, or `compare`.

## Evidence order

Start with the score, severity counts, coverage, and top findings in
`summary.md`. Use the annotated overview to locate the highest-impact issue.
Read only the corresponding entries in `report.json` and component crop paths.

For each surfaced issue, include:

- severity and stable predicate ID;
- affected component ID or crop path when available;
- measured value versus threshold;
- user impact and a concrete correction;
- WCAG success criterion or named-system rule only when the report or bundled
  reference supports it.

Do not turn an absent state into a failure. Label it `not captured` or `verify`.

## Severity

- **P0:** completion is blocked, data/safety is at risk, or the UI is materially
  inaccessible.
- **P1:** a hard product/system rule is violated or users face substantial
  friction, but the task remains possible.
- **P2:** polish, consistency, or lower-impact usability debt.

Preserve the harness severity unless visual evidence proves the impact differs;
if you change it, explain why.

## Component judgment after predicates

- **Actions:** visual weight should match consequence and frequency. Check
  labels, grouping, loading, disabled, and destructive states.
- **Forms:** check label placement, required/optional convention, help timing,
  recoverable errors, expected-width cues, and tab order.
- **Navigation:** current location, hierarchy, and icon comprehension must be
  clear without relying on a subtle color difference alone.
- **Tables/lists/cards:** check alignment, scannability, truncation, empty
  states, and whether repeated card treatments communicate real hierarchy.
- **Dialogs:** verify focus entry/trap/return, escape behavior, destructive
  confirmation, and a clear action hierarchy.
- **Imagery:** distinguish informative from decorative images and check crop,
  quality, and aspect-ratio consistency.

Only surface these judgment findings when the rendered artifact supports them.

## Named systems and heuristics

When `--against` is set, frame results as conformance to that named system.
Use its exact canonical name from `keen compare --help` (for example,
`apple-hig` and `fluent-2`, not informal aliases).

Without a pinned system, do not pretend the product adopted one. Use WCAG for
accessibility, Nielsen-style heuristics for behavior/recovery, and visual
principles for hierarchy/rhythm. If a deeper system-specific citation is
needed in Claude, read only the relevant section under
`${CLAUDE_PLUGIN_ROOT}/references/`; otherwise rely on the report's measured
rule and avoid invented details.

## Compact output

1. Verdict: grade, strongest aspect, highest-impact problem.
2. Up to three systemic findings.
3. Selected component findings that add information, not duplicates.
4. Coverage gaps.
5. Three next actions.
