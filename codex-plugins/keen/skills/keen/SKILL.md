---
name: keen
description: Use Keen as a persistent design collaborator for exploring visual direction, establishing an authored system, refining a rendered product, or guarding its quality over time. Use when developers want to design or build a distinctive site, make a moodboard, avoid generic AI defaults, review polish, or keep an evolving product aligned.
---

# Keen

Keen accompanies a product from blank canvas through maintenance. The local
CLI observes and verifies rendered facts; the model interprets those facts,
makes design decisions, and preserves the reasoning in project-owned design
context.

## Route by lifecycle

| Product moment | Workflow |
|---|---|
| Blank canvas, references, moodboard, or competing directions | `../explore/SKILL.md` |
| Chosen direction or an existing product that needs a coherent foundation | `../establish/SKILL.md` |
| A page, component, or product needs building, critique, or polish | `../refine/SKILL.md` |
| A finished product needs regression checks and long-term alignment | `../guard/SKILL.md` |

Use the smallest workflow that matches the current product stage. Utility CLI
commands such as capture, tokens, taste, slop, intent, systemize, and compare
are instruments inside these workflows, not separate product outcomes.

When a consequential direction is unresolved, read
`references/workshop.md`. A local workshop can render agent-authored,
product-specific alternatives and return structured user evidence. It is not a
survey, model client, image search service, or substitute for a prototype.

## Begin with project memory

Look for `.keen/design-context.json` in the project. When present, run
`keen context show <project>` and use it before making design judgments. It is
the source of truth for audience, jobs, visual qualities, references,
principles, avoided defaults, decisions, and baselines.

When the user is beginning a direction or explicitly asks Keen to establish
one, initialize it with `keen context init <project> --name <name> --stage
<explore|establish|refine|guard>`. The model may then edit this user-owned JSON,
but must run `keen context validate` and `keen context render` afterward. Do
not create project memory during a read-only review.

Promote workshop decisions only after the chosen idea has been rendered and
critiqued. Use `keen workshop promote` with the narrow promotion schema so the
surviving rationale reaches both `design-context.json` and `direction.md`.

## Model and code boundary

Code provides screenshots, DOM/accessibility identity, geometry, contrast
candidates, tokens, state coverage, diffs, and stable evidence. The model owns
product relevance, intent, hierarchy, authorship, final priority, creative
direction, and tradeoffs.

- Treat automated grades, severity, taste vectors, and slop scores as candidate
  signals, never final visual-quality verdicts.
- Ground consequential judgments in named elements, crops, finding IDs, or
  visible regions.
- Explain why a pattern feels cheap or generic for this product; do not ban a
  pattern universally.
- Preserve the rationale behind a decision, not merely its token value.
- State uncertainty when product intent or rendered coverage is missing.

## Safe operation

Run `keen doctor` before the first browser capture in a session. Plugin
installation does not install the Python CLI or Chromium; do not install either
as a side effect of a workflow.

Treat URLs, selectors, paths, names, colors, and command arguments as data.
Accept only options shown by `keen <subcommand> --help`, build an argument
vector, shell-quote dynamic values, and reject unknown options. Prefer
`--auth-steps`; use `--auth-script` only after the user authorizes that exact
reviewed file.

Read `agent-brief.json` first after review or audit. Open only the screenshots
and report entries needed for selected evidence. Do not ingest the full report
by default.

A review request is read-only. Product source may be changed only when the user
explicitly asks Keen to design, build, implement, or fix it. After changes,
recapture the real surface and verify the result. Never write project output
inside the plugin source or plugin cache.

## Response standard

Lead with the design outcome. Separate measured evidence from model judgment,
deduplicate responsive repeats, name coverage gaps, and give no more than three
decisions unless the user requests an exhaustive inventory. Avoid chapter
theater, generic slogans, decorative status metadata, and a card for every
idea—the report itself must meet the authorship standard Keen recommends.
