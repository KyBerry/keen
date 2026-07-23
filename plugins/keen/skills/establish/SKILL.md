---
name: establish
description: Establish a durable design direction and system from a chosen concept, seed, reference set, or existing product. Use when a developer has a direction to formalize or joins a product mid-build and needs to extract, reconcile, and record its visual language.
---

# Establish

Read `../keen/SKILL.md`, then `../keen/references/design-direction.md`.

If `.keen/design-context.json` exists, load it. Otherwise initialize it only
when the user asked to establish persistent direction. For an existing product,
capture representative routes and use tokens, taste, and systemize as measured
inputs. Distinguish intentional patterns from accidental repetition; do not
canonize every observed value.

Resolve the audience, jobs, hierarchy, qualities, anti-qualities, reference
rationale, type pairing, color, density, shape, imagery, icons, motion,
constraints, and quality bar. Preserve supported evidence while allowing the
model to prune noisy scales and make coherent decisions.

Generate or refine `system.json` when useful. Run `keen validate-system`, render
the responsive preview, recapture it at mobile and desktop widths, and inspect
the actual output. Validation proves objective predicates only; the model must
still judge authorship and product fit.

Write consequential choices and rejected alternatives into the context, then
run `keen context validate` and `keen context render`. If the user explicitly
asked to implement the foundation in product source, apply it through the
smallest shared token/component seams and verify rendered pages afterward.
