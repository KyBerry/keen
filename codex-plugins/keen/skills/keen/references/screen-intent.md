# Screen-intent guide

Intent weights findings; it does not block measurement.

If the user stated the screen's job, audience, or desired action, use it. If
not, infer the most likely intent from the route and rendered content, label the
assumption, and proceed. Ask only when competing interpretations would change
the recommendation materially.

## Walk

1. Name the screen's job in one sentence.
2. Name the likely user and moment: new/returning, novice/expert, calm/urgent,
   desktop/mobile.
3. Identify the action or decision the hierarchy emphasizes. Some screens are
   informational or legitimately support multiple peer actions; do not impose a
   universal single-primary-action rule.
4. List the minimum information needed to act and note what is missing, buried,
   or stranded from the action it supports.
5. Identify visual or copy noise that competes with the job.
6. Trace reading order and compare it with action priority.
7. Reconcile the observed screen with the stated or inferred intent.
8. Predict up to three falsifiable trip points.

## Evidence by input type

- **Run directory:** cite stable component IDs, crop paths, or bounding boxes
  only for the few elements needed to substantiate a finding.
- **Single screenshot:** there is no `report.json`. Refer to visible regions and
  labels; do not invent component IDs, predicate IDs, or bounding boxes.

Mark predicate-backed claims **measured** and the intent interpretation
**judgment**. Deduplicate across viewports and describe only meaningful changes.

## Output

Lead with the intent assumption and a one-sentence verdict. Then give the three
highest-impact changes, followed by only the walk highlights that produced a
useful finding. End with any trip point the user can test.
