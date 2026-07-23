---
name: guard
description: Guard an established product against visual regressions, generic drift, and lost design rationale. Use when developers are maintaining a finished product, reviewing a redesign or pull request, comparing baselines, or checking that new work still belongs.
---

# Guard

Read `../keen/SKILL.md`, then `../keen/references/guard.md`.

Load `.keen/design-context.json` and identify the accepted baseline. Capture the
current surface with matching route, viewport, state, authentication, and
content whenever possible. Run `keen diff <baseline> <current>` and use scores
only to locate change; inspect the actual screenshots and named elements before
judging impact.

Separate intended evolution, acceptable variation, mechanical regression,
authorship drift, and unverified differences. Prioritize repeated components,
task completion, accessibility, hierarchy, and departures from explicit
direction. Do not treat every token change as a defect or rewrite the direction
to rationalize weaker work.

Review-only requests do not authorize source changes. When the user asks for
repairs, implement them and recapture the affected states. When an evolution is
accepted, record only the consequential decision and new baseline, then run
`keen context validate` and `keen context render`.

Return the release or maintenance decision, the evidence behind it, and no
more than three actions. State mismatched capture conditions as coverage gaps.
