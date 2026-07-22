---
name: ui-tokens
description: Extract design tokens from a screenshot or existing Keen run. Use when the user wants observed colors, spacing, typography, scale coherence, token fragmentation, or optional drift against a named system.
---

# UI Tokens

Read `../keen/SKILL.md` first for input-safety and artifact-order rules.

Require one local screenshot or run directory; URLs are not accepted. Accept only options from `keen tokens --help` and safely quote every dynamic value.

Summarize scale coherence, fragmentation, and, when `--against` is present, the highest-frequency and largest-magnitude drift. State that loose-screenshot mode provides color evidence only; spacing and typography require captured DOM artifacts.
