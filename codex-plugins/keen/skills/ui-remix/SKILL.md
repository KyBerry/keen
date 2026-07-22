---
name: ui-remix
description: Create a distinct design system from measured inspiration. Use when the user wants to preserve selected qualities from a URL or Keen run while deliberately changing color plus type, shape, depth, or density.
---

# UI Remix

Read `../keen/SKILL.md` first, then `../keen/references/system-design.md` and `../keen/references/characterize.md`.

Parse one required `--inspiration`, optional numeric `--shift-hue`, and optional kebab-case `--name`; reject unknown options and safely quote values. For an HTTP(S) inspiration, run `keen review` to a new run. For an existing run, use it directly. Run `keen taste` and read its compact card before creating the proposal.

Use the inspiration as priors, not a copy target: retain only requested high-level qualities and deliberately change color family plus at least one of type, shape, depth, or density. Follow the create workflow for palette derivation, system assembly, strict validation, responsive preview, and rendered inspection.

Strict validation proves only the predicates it emits. If tooling does not provide a normalized inspiration-distance metric, report measurable hue/type/shape deltas and state the limitation; never claim guaranteed distance. If the inspiration lacks useful chromatic signal, use a user-provided seed or switch to vibe creation.
