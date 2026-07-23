---
name: refine
description: Design, review, implement, or polish a rendered page or component against the product's direction. Use when developers are actively building, when something feels cheap or generic, or when a nearly finished surface needs a rigorous detail pass.
---

# Refine

Read `../keen/SKILL.md`, then `../keen/references/refine.md`.

Load project design context when present. For a URL or local route, run `keen
doctor`, then capture or review the real rendered surface with the smallest
necessary viewport/state matrix. For an existing run, audit without
recapturing. For a loose image, make visual claims only and do not invent DOM
evidence.

Read `agent-brief.json` first. Inspect selected screenshots, crops, and report
entries only as needed. Use intent, taste, token, named-system, and AI-default
signals as contextual lenses—not independent verdicts or scores.

Judge mechanical finish, authorship, and product fit. Explain why an issue
matters for this product and propose a correction tied to its recorded
direction. Return no more than three high-leverage decisions unless the user
asks for exhaustive findings.

A critique request is read-only. If the user explicitly asks to build or fix,
implement the smallest coherent change, preserve existing product constraints,
then recapture meaningful mobile and desktop states. Verify the rendered
result, update project decisions only when the change should guide future work,
and report remaining coverage gaps.
