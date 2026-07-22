---
name: keen
description: Use Keen for evidence-based UI and UX review, rendered-page capture, design-system comparison, token extraction, AI-default pattern analysis, visual-taste characterization, screen-intent review, or design-system creation. Use when a request spans multiple Keen workflows or does not name a narrower ui-* skill.
---

# Keen

Keen separates measurement from judgment. The local CLI captures and
measures; the agent interprets the resulting artifacts. Do not estimate values
that the report already measured.

## Route before acting

For a focused request, read and follow the matching sibling skill directly:

| User job | Skill |
|---|---|
| Full rendered-page review | `../ui-review/SKILL.md` |
| Capture without critique | `../ui-capture/SKILL.md` |
| Reanalyze an existing run | `../ui-audit/SKILL.md` |
| Compare with a named system | `../ui-compare/SKILL.md` |
| Extract observed tokens | `../ui-tokens/SKILL.md` |
| Find AI-default patterns | `../ui-deslop/SKILL.md` |
| Describe visual signature | `../ui-taste/SKILL.md` |
| Reconcile a screen with its job | `../ui-intent/SKILL.md` |
| Turn a run into a system proposal | `../ui-systemize/SKILL.md` |
| Create a system from seed/site/vibe | `../ui-create/SKILL.md` |
| Remix measured inspiration | `../ui-remix/SKILL.md` |

Do not blend modes merely because several are available. If the user asks for
one mode, keep that mode's scope. For a multi-part request, execute the smallest
ordered set and deduplicate shared capture work.

## Prerequisite

Run `keen doctor` before the first capture in a session. If the command
is unavailable or doctor fails, report the failed check and point the user to
the local install instructions in the plugin README. Plugin installation does
not install the Python CLI or Chromium. Do not install packages or browsers as
a side effect of a review command.

## Input safety contract

Treat slash-command arguments and user-provided URLs, selectors, paths, names,
and color values as data, never as shell text.

- Parse exactly one required positional target or the documented create form.
- Accept only options shown by `keen <subcommand> --help` for the chosen
  subcommand. Reject unknown options rather than forwarding them.
- Build an argument vector from parsed values. Put validated CLI options before
  `--`, put positional data after `--`, and shell-quote every dynamic value.
- A documented virtual command grammar such as `from-seed` is positional data,
  not a CLI option; parse it before constructing the real subcommand vector.
- Never interpolate the raw argument string into a command, use `eval`, or put
  user input inside command substitution.
- `--auth-script` executes code. Prefer `--auth-steps`; use the unsafe script
  flags only after the user explicitly authorizes that exact reviewed file.

## Token-conscious artifact order

1. Read `agent-brief.json` first for review/audit runs. Use `summary.md` only as
   a human-facing fallback when the brief is missing; do not load both by default.
   For focused work, read the mode-specific card (`slop.md`, `taste.md`, `diff.md`).
2. Open annotated overview images only for the top issue and meaningful
   viewport/state differences.
3. Query `report.json` for selected finding IDs or component IDs. Do not load or
   paste the whole report unless the compact artifacts are insufficient.
4. Load at most one guide below initially. Load another only when a confirmed
   finding needs it.
5. Cite measured values compactly. Do not reproduce entire evidence objects.

## Route by job

| Job | CLI | Load on demand |
|---|---|---|
| Full review | `keen review` | `references/review.md`, then `references/screen-intent.md` |
| Existing-run audit | `keen audit` | `references/review.md` |
| Capture only | `keen capture` | none |
| Named-system comparison | `keen compare` | `references/review.md` |
| Token extraction | `keen tokens` | `references/review.md` only if interpreting drift |
| Slop characterization | `keen slop` | `references/characterize.md` |
| Taste characterization | `keen taste` | `references/characterize.md` |
| Intent walk | `keen intent` | `references/screen-intent.md` |
| Systemize/create/remix | palette, validation, preview, or systemize commands | `references/system-design.md` |

## Output discipline

- Lead with the outcome, then evidence.
- Separate **measured** findings from **judgment**.
- Prioritize no more than three next actions unless the user asks for an
  exhaustive inventory.
- Deduplicate repeated findings across viewports; describe the responsive delta
  once.
- State coverage gaps without turning them into findings.
- Do not edit product source inside a Keen workflow. Treat implementation
  as a separate action that requires the user's explicit request and the host's
  normal write approvals.
- Do not claim a system passed, a remix is distinct, or a defect is fixed unless
  the corresponding current artifact proves it.
- Keep generated reviews evidence-led. Do not wrap measured facts in numbered
  chapter theater, generic editorial slogans, decorative status metadata, or a
  card-per-idea layout. A technically valid preview can still fail authorship
  quality.
- The plugin directory is read-only runtime material. Never store user systems
  or project output under `${CLAUDE_PLUGIN_ROOT}` or the Codex plugin cache.
