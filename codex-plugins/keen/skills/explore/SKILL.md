---
name: explore
description: Explore an authored visual direction with research, references, moodboards, and meaningfully different concepts. Use when a developer is starting from nothing, wants thoughtful inspiration, needs a moodboard, or has not chosen how the product should feel.
---

# Explore

Read `../keen/SKILL.md`, then `../keen/references/explore.md`,
`../keen/references/design-direction.md`, and `../keen/references/workshop.md`.

Establish the product job, audience, desired feeling, constraints, and source
material. Inspect user-provided images or URLs first. Research current examples
only when requested or needed to answer the prompt, keep source links, and use
references as evidence rather than templates to copy.

Present two or three directions that differ in composition, typography,
density, imagery, interaction, and content behavior—not merely color or radius.
For each, say what it borrows, what it rejects, why it fits the product, and
where it could fail.

When seeing the options will help the user decide, author a temporary local
workshop with product content and rendered specimens. Ask only the unresolved,
high-leverage question; do not turn known facts into a questionnaire. Interpret
the response, make the smallest useful prototype, capture relevant viewports
and states, and critique the render before asking a follow-up or promoting a
decision.

When the user asks to retain the work, initialize or update the project-owned
`.keen/design-context.json`. Record reference `take` and `avoid` fields, the
chosen or unresolved direction, and important constraints. Run `keen context
validate` and `keen context render` after editing.

Finish with a clear choice or smallest useful prototype. Do not build a full
system before a direction has been selected, and do not preserve temporary
workshop files as project memory.
