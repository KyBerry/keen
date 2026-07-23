# Design-direction guide

`.keen/design-context.json` is compact project memory, not a generated brand
bible. Keep it current enough that a new developer or agent can make a
consistent decision without rereading old conversations.

Record:

- `project`: name, lifecycle stage, summary, audiences, jobs, primary action;
- `direction`: desired and undesired qualities, principles, typography pairing
  and rationale, color rationale, density, shape, motion, imagery, and icons;
- `references`: source plus explicit `take` and `avoid` lists;
- `constraints`: technical, brand, content, accessibility, or delivery limits;
- `quality_bar`: falsifiable descriptions of finished work;
- `avoid`: project-specific cheap or generic defaults;
- `decisions`: a short summary, rationale, status, and optional stable ID;
- `baselines`: accepted run paths or screenshot references.

Prefer useful sentences over adjectives. “Calm” alone is weak; “quiet surfaces,
one high-contrast action, and editorial type only for decision-setting copy” is
actionable.

Update context when a decision should influence future work. Do not turn every
small implementation choice into permanent doctrine. After editing, run
`keen context validate <project>` and `keen context render <project>` so the
machine and human views agree.
