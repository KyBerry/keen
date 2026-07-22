---
name: ui-audit
description: Reanalyze an existing Keen run without recapturing. Use when the user already has a run directory and wants updated findings, a compact critique, or an optional named-system comparison from the stored evidence.
---

# UI Audit

Read `../keen/SKILL.md` first, then `../keen/references/review.md`.

Require one run directory. Accept only options from `keen audit --help` and safely quote every dynamic value. Reuse existing screenshots and DOM artifacts; do not recapture. Read `agent-brief.json` first and use `summary.md` only when the brief is missing. If the user supplied `--against`, use the exact canonical system name accepted by the CLI.

For a later iteration, prefer `keen diff <old-run> <new-run>` and report only meaningful changes.
