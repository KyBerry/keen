---
name: ui-create
description: Create a validated design system from a color seed, captured site, or stated vibe. Use when the user asks Keen to generate a system proposal and responsive review sheet rather than merely analyze an existing UI.
---

# UI Create

Read `../keen/SKILL.md` first, then `../keen/references/system-design.md`.

Parse exactly one entry form: `from-seed`, `from-site`, or `from-vibe`. Treat all values as data and reject unknown options. Infer a short slug, archetype, seed, and palette strategy when intent is sufficient, state the assumptions, and continue. Ask only when a missing choice would materially change brand direction.

Use the deterministic stages:

1. Run `keen derive-palette` with a valid quoted seed and allowlisted strategy.
2. Assemble `system.json` under `.keen/created/<slug>/` using the schema accepted by `keen validate-system`.
3. Run strict validation and make targeted corrections, at most three passes.
4. Run `keen preview-system --mockups`; recapture the absolute preview HTML at mobile and desktop widths with `--allow-file` and inspect both PNGs.
5. Run `keen slop` on the verification run and correct template composition rather than adding ornament.

Keep display faces inside role specimens, label synthetic copy, and reject numbered editorial chapters, generic slogans, decorative status metadata, fake product evidence, and card-per-idea layouts unless the brief gives a concrete product reason. Present paths, validation evidence, remaining limits, and up to three adjustment choices. Never write output into the plugin source or cache, and do not claim persistent `--against <slug>` support.
