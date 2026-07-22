---
name: ui-review
description: Review a rendered UI with measured, intent-aware evidence. Use when the user asks for a full UI or UX review, visual audit, accessibility-oriented review, responsive critique, or prioritized improvements for a URL or local route.
---

# UI Review

Read `../keen/SKILL.md` first for the prerequisite, input-safety, artifact-order, and output contracts. Then read `../keen/references/review.md` and `../keen/references/screen-intent.md`.

Require exactly one target. Accept only options listed by `keen review --help`; reject unknown options. Invoke `keen review` with an argument vector whose dynamic values are shell-quoted. Let the CLI choose its timestamped output directory unless the user supplied `--out`.

After the run:

1. Read `agent-brief.json`; use `summary.md` only when the brief is missing.
2. Inspect only the selected report entries and annotated images required for the top findings.
3. Use stated intent or label a reasonable inference. Ask only if ambiguity would change the verdict.
4. Separate measured facts from judgment, deduplicate responsive repeats, and end with no more than three next actions unless the user asks for an exhaustive list.

If `keen doctor` fails or capture coverage is incomplete, report it directly. Do not install dependencies, fabricate evidence, or edit product source as part of this review workflow.
