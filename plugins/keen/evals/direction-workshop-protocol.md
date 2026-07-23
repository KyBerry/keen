# Direction workshop outcome evaluation

The workshop succeeds only if it helps a later agent build a more coherent,
product-specific interface. Completion rate, option clicks, and visual
preference are diagnostics—not the outcome.

Compare four arms with the same product brief and task:

1. brief only;
2. a static adjective questionnaire;
3. an adaptive workshop result;
4. the workshop result plus a real render, critique, and revision.

Run each case and arm at least three times with fresh sessions and randomized
arm labels. The direction-producing agent receives only that arm's evidence.
A second fresh agent receives only the produced direction artifact and the
implementation task. It must not see the source arm.

## Score the handoff, not the prose

Use automated structure and term checks as a smoke test. A blinded reviewer
then scores the fresh handoff and, when implementation is available, the final
render from 1–5 on:

- product-job and primary-action accuracy;
- preservation of real code, content, accessibility, and viewport constraints;
- meaningful hierarchy and type-role decisions;
- product-specific composition and interaction rather than fashionable tokens;
- recognition of failure modes and implementation cost;
- responsive and state coverage;
- consistency between stated direction and rendered result;
- absence of fabricated evidence, generic AI defaults, and unearned certainty.

The decisive comparison is fresh-agent task performance. The rendered-loop arm
should produce fewer clarification questions, fewer contradictory design
choices, and higher blinded render scores than brief-only and static-questionnaire
arms. If it merely produces longer artifacts, the workshop has failed.

## Guardrails

- Use the same model/version, task, code snapshot, and token budget across arms.
- Keep model judges separate from direction generators and retain human review
  for authorship and visual fit.
- Do not score source screenshots or option labels as final output quality.
- Record invalid runs, missing viewport/state evidence, and judge disagreement.
- Retain raw eval artifacts outside shipped plugin and project design context.
- Change the workshop only when a repeated outcome gap is attributable to its
  questions, specimens, handoff contract, or promotion behavior.
