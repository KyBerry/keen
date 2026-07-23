# Guard guide

Guard protects established intent rather than freezing the interface.

Use an accepted Keen run or recorded screenshots as the baseline. Capture the
changed surface with matching routes, viewports, states, authentication, and
content when possible. Run `keen diff <baseline> <current>` and inspect the
rendered delta, not just score movement.

Classify change as:

- intended evolution that should update project memory;
- acceptable variation within the direction;
- mechanical regression;
- authorship drift that makes the product less coherent or more generic;
- unverified because state, content, or coverage differs.

Prioritize regressions that affect task completion, accessibility, hierarchy,
or a repeated component. Treat raw token drift and automated score movement as
leads. A different value is not automatically a defect.

When the current result becomes the new accepted direction, add the baseline
reference and consequential rationale to `.keen/design-context.json`, validate
it, and refresh `direction.md`. Do not silently rewrite the direction to excuse
a regression.
