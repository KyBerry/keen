---
name: ui-capture
description: Capture a URL or local route across selected viewports and states without critiquing it. Use when the user wants screenshots, DOM measurements, state coverage, or a reusable Keen run for later analysis.
---

# UI Capture

Read `../keen/SKILL.md` first for the prerequisite and input-safety contract.

Require exactly one target. Accept only options from `keen capture --help`, construct a safely quoted argument vector, and preserve the CLI defaults unless the user requested particular viewports or states. Prefer declarative `--auth-steps`; do not use unsafe auth-script flags without explicit authorization for that exact reviewed file.

After capture, report the output directory, requested versus successful viewport/state matrix, and warnings. Do not analyze or critique the images unless asked.
