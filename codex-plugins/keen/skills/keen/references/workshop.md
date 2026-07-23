# Direction workshop guide

Use the workshop when a consequential visual or interaction choice remains
unresolved and rendered alternatives will produce better evidence than prose.
Do not use it to ask facts already available in the product, code, content, or
design context.

## One adaptive round

1. Inspect the real product job, audience, primary action, content, code seams,
   constraints, and existing direction.
2. Choose one high-leverage decision. Offer two or three meaningfully different
   alternatives, or a focused text question when options would fabricate
   certainty.
3. Create the spec and response in an operating-system temporary directory,
   not the project or plugin. Read the authoring contract with `keen workshop
   schema spec`, then run `keen workshop validate <spec>`.
4. Run `keen workshop serve <spec> --response <temporary-response>`. Keep the
   command alive while the user answers. The private URL contains a random
   token and the server binds only to loopback.
5. Run `keen workshop summarize <spec> <response>`. Treat the result as user
   evidence, not an automatic decision.
6. Implement the smallest useful prototype. Use real product copy. Capture the
   relevant mobile and desktop widths plus consequential states. Critique
   hierarchy, task clarity, authorship, accessibility, responsiveness, and
   product fit.
7. If the render exposes one new consequential question, run a smaller
   critique round. Otherwise state the decision and rejected alternative.

Each option must say what it changes, why it fits, where it could fail, and
what it costs. Its specimen should demonstrate the actual disagreement through
composition, type roles, density, color behavior, action hierarchy, or
interaction—not decorate identical cards. Always allow the user to reject the
set or describe a hybrid.

Use stages deliberately:

- `product-truth`: correct an ambiguous job, audience, content hierarchy, or
  primary action;
- `direction`: compare authored visual or interaction theses;
- `critique`: compare revisions after inspecting a real render.

## Durable promotion

Workshop specs, responses, and handoffs are transient. A selected option is
not durable merely because the user clicked it. After a prototype survives
rendered critique, read `keen workshop schema promotion`, write a narrow
promotion in the temporary directory, and run:

```text
keen workshop promote <project> <promotion.json>
keen context validate <project>
```

The promotion must include a concise decision, rationale, status, and source
session ID. Add only product facts, direction rules, constraints, quality bars,
references, avoided defaults, or baselines that should guide a fresh agent.
Do not promote untested preferences, session progress, rejected options, or a
generated brand narrative.

## Model boundary

The server renders a closed JSON vocabulary and collects answers. It does not
call a model, browse for inspiration, infer the next question, modify product
source, or decide what survives. The agent owns adaptation, prototype work,
critique, and the proposed promotion; the user owns the consequential choice.
