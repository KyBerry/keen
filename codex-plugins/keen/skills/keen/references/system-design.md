# System-design guide

Use for `systemize`, seed/vibe/site creation, and remix work.

## Principles

- Pick an archetype before pruning scales.
- Reduce choices: named roles and rules matter more than token count.
- Preserve semantic separation between brand actions and status colors.
- Validate every color against its intended use; disabled UI is exempt from
  WCAG contrast requirements, not required to have poor contrast.
- Treat bundled systems as inspiration and comparison targets, not copy sources.
- Assign explicit display, body/interface, and mono roles. Use a contrasting
  display/body pair only when the source or brief supports it; otherwise keep
  one family and create hierarchy through scale, weight, and rhythm.
- Prefer captured font evidence over trend-driven substitutions. A pairing must
  explain what each face does and remain legible when only local fallbacks load.
- Treat the preview as a working review sheet, not a portfolio piece. Report
  chrome should use the restrained reading face; display fonts belong inside
  the product-role specimens that justify them.
- Keep review chrome on a fixed accessible neutral surface. Candidate color
  roles belong in labeled swatches and bounded specimens; an invalid proposal
  must not make its own decision document unreadable.
- Use captured product strings for specimens when they are available. Label
  synthetic stress copy explicitly; never pass invented slogans off as product
  evidence.
- Treat font-family counts as captured elements, not authored uses. Infer a
  display role only when heading-scale text supports it; exclude code and icon
  fonts from reading-role selection.
- Review AI-looking patterns as clusters. Do not ban a font, color, card, or
  radius in isolation, and do not escape one template by adding another
  fashionable device.

## Minimum system shape

- color roles for text, surfaces, borders/focus, primary action, success,
  warning, danger, and information;
- a coherent type scale of roughly five to seven roles;
- declared `fonts.display`, `fonts.body`, and `fonts.mono` stacks plus a short
  pairing rationale (`fonts.primary` remains a compatibility alias for body);
- a declared 4px or 8px spacing grid with a short practical scale;
- three to five purposeful radii plus optional `none`/`full`;
- component minimums for actions, inputs, rows, and focus targets;
- falsifiable usage rules, not just a token vocabulary.

Use the schema accepted by `keen validate-system`; do not rely on an
internal planning document as a runtime specification.

## Workflow

1. For an observed product, run `keen systemize <run-dir> --name
   <slug>`. For a seed or remix, derive the palette first.
2. Assemble the system JSON with a short rationale for judgment calls,
   including why display and body faces belong together.
3. Run `keen validate-system <system.json> --archetype <name> --strict`.
4. Make targeted corrections, with a maximum of three validation iterations.
5. Run `keen preview-system <system.json> --out <dir> --mockups`.
6. Recapture the rendered specimen with an isolated browser context:
   `keen capture "<absolute-preview.html>" --allow-file --viewports
   mobile,desktop --states default --out "<out>/verification" --overwrite`.
   Read both PNGs. Check overflow, clipped content, actual font resolution,
   focus visibility, and whether every visible control is real or explicitly
   labeled static. Never leave a user-visible browser in an emulated viewport.
7. Run `keen slop "<out>/verification" --print-markdown`. Treat a
   triggered cluster as an authorship-quality audit prompt: compare it with the
   render, then fix template composition or document it as an evidence-backed
   product choice. Do not add decorative variety merely to lower the score.
8. Inspect the applied landing, dashboard, and form surfaces when available.
   Confirm headings and body copy resolve to the intended stacks in the browser,
   not only in JSON.
9. Present the artifact paths, validation result, authorship-quality result,
   remaining risks, and up to
   three adjustment choices.

## Creation assumptions

Infer a sensible slug, archetype, palette strategy, and seed when the user gave
enough direction. State those assumptions and continue. Ask only when a missing
choice would materially change brand direction.

For a site seed, use observed non-neutral color evidence. For a vibe, propose a
primary choice and alternatives in the result rather than forcing a preliminary
round trip unless the user requested approval first.

## Persistence

Keep generated systems in the user's project output, normally
`.keen/created/<slug>/`. Never copy them into the plugin source or cache.
The current CLI does not provide a persistent custom-system registry; do not
claim that `--against <slug>` will work until a supported registry/install
command exists and its readback succeeds.
