# Keen

Keen is a sharp-eyed local UI/UX analysis plugin and Python CLI for Claude Code,
Codex, and scripts. It captures rendered pages with Playwright, measures
accessibility and design-system drift, detects recognizable AI-default surface
patterns, extracts a compact taste vector, and writes reviewable artifacts.

Python measures; the agent judges. That keeps contrast, geometry, DOM state,
and token math deterministic while reserving intent, hierarchy, and
prioritization for the model.

The public repository is [KyBerry/keen](https://github.com/KyBerry/keen). Clone
it locally before following the marketplace and CLI installation steps below.

## Requirements

- Python 3.10 or newer
- `uv` recommended (pip editable installs also work for development)
- Chromium installed for Playwright captures
- roughly 300 MB for the browser binary
- Claude Code 2.1.216 or newer for current namespaced skill behavior
- a Codex build with `codex plugin` support for the Codex plugin install

The agent plugin and the local measurement runtime are deliberately separate.
Claude and Codex plugin installers load Markdown workflows and presentation
metadata; they do not run Python package or browser installers. Install the CLI
and Chromium explicitly so package execution is never a hidden side effect of a
review request.

## Local Claude Code install

Let `<marketplace-path>` be the absolute path to this
marketplace directory and `<plugin-path>` be
`<marketplace-path>/plugins/keen`.

```bash
claude plugin marketplace add "<marketplace-path>"
claude plugin install keen@keen
uv tool install --force --reinstall --editable "<plugin-path>"
uv tool run --from "<plugin-path>" playwright install chromium
keen doctor
```

Restart Claude Code after installing or updating the plugin.

To refresh an existing local install after source changes:

```bash
claude plugin marketplace update keen
claude plugin update keen@keen
uv tool install --force --reinstall --editable "<plugin-path>"
keen doctor
```

If Claude still reports the old version, uninstall and reinstall through the
plugin commands instead of editing or deleting cache files manually.

## Local Codex plugin install

The repository includes a Codex marketplace at
`<marketplace-path>/.agents/plugins/marketplace.json`. It installs an
allowlisted bundle from `<marketplace-path>/codex-plugins/keen`, built
from the canonical skills, assets, license, and native manifest under
`<plugin-path>`. The clean bundle keeps Python environments, test caches, and
development artifacts out of Codex's plugin cache.

```bash
codex plugin marketplace add "<marketplace-path>"
codex plugin add keen@keen
uv tool install --force --reinstall --editable "<plugin-path>"
uv tool run --from "<plugin-path>" playwright install chromium
keen doctor
```

Start a new Codex task after installing or updating so skill discovery reloads.
To refresh a configured local plugin after source changes, use the maintainer
sequence below. The cachebuster is build metadata on the Codex manifest; the
shared release remains 0.7.0.

```bash
python3 /Users/kyleberry/.codex/skills/.system/plugin-creator/scripts/update_plugin_cachebuster.py "<plugin-path>"
python3 "<plugin-path>/scripts/sync_codex_plugin_bundle.py"
codex plugin add keen@keen
```

If plugin installation is unavailable, the compatibility fallback is to copy
one or more skill directories from `<plugin-path>/skills/` to
`$HOME/.agents/skills/`. The native plugin install is preferred because it
preserves namespacing, plugin UI metadata, starter prompts, and the complete
workflow set.

## CLI quickstart

```bash
keen doctor
keen review "https://app.example.com/dashboard" --against material-3
keen capture "http://localhost:3000" --viewports mobile,desktop
keen audit ".keen/review/<run>"
keen compare "https://app.example.com" --against apple-hig
keen tokens ".keen/review/<run>"
keen slop ".keen/review/<run>" --print-markdown
keen taste ".keen/review/<run>"
keen diff ".keen/review/<old>" ".keen/review/<new>"
keen intent ".keen/review/<run>"
```

Run `keen <subcommand> --help` for the authoritative options and exact
system names. `hig` and `fluent` are not aliases; use `apple-hig` and
`fluent-2`. Keen is the only supported executable name.

## Agent workflows

- `ui-review` — full measurement plus intent-aware critique
- `ui-capture` — capture only
- `ui-audit` — reanalyze an existing run
- `ui-compare` — conformance to one named system
- `ui-tokens` — tokens from a screenshot or existing run, not a URL
- `ui-deslop` — AI-default surface fingerprints and escape moves
- `ui-taste` — measurable visual signature
- `ui-intent` — intent walk for a run or loose PNG
- `ui-systemize` — reduce observed tokens into a system proposal
- `ui-create` — create from a seed, site run, or vibe
- `ui-remix` — create from measured inspiration without overstating distance

In Claude Code, invoke a workflow as `/keen:ui-review`. In Codex, invoke
it as `$keen:ui-review`. A general request can use the umbrella
`keen` skill, which routes to the smallest matching workflow. The
specialist Codex skills do not activate implicitly; this prevents a casual UI
question from launching a browser capture or creation flow.

Skills treat arguments as data, allowlist CLI options, and shell-quote dynamic
values. Review workflows do not install dependencies or mutate product code.
Creation workflows write only to the user-owned output location they report.

## Output

A full review writes a timestamped directory under `.keen/review/`.
That path remains stable so existing runs and automation continue to work:

```text
summary.md                         compact human report
agent-brief.json                   bounded agent-first evidence index
report.json                        complete structured report
report.html                        organized visual evidence review
capture-manifest.json              requested/succeeded capture matrix
screens/*-annotated.png            issue locations by severity
dom/*.json                         rendered DOM measurements
components/*.json                  decomposed components
components/*.png                   selected component crops
analysis/*.json                    predicate findings
tokens/extracted.json              observed tokens
tokens/comparison.json             named-system drift, when requested
tokens/drift.md                    compact drift table, when requested
slop.json / slop.md                AI-default pattern score
taste.json / taste.md              taste vector and compact card
```

Focused commands write only the artifacts relevant to their job. Add
`.keen/` to the product repository's `.gitignore`.

For agent work, read `agent-brief.json` as the sole first artifact. Use
`summary.md` as a human-facing fallback when the brief is missing, not as a
second copy of the same evidence. Query `report.json` only for the few
finding/component IDs needed to support the response.

## Design-system creation

The deterministic stages are:

```bash
keen derive-palette --seed "#0A4D8C" --strategy complementary
keen validate-system "system.json" --archetype clarity-first --strict
keen preview-system "system.json" --out ".keen/created/example" --mockups
keen systemize ".keen/review/<run>" --name example
```

`systemize` preserves supported display/body font pairings from the capture,
records the evidence and rationale in JSON and Markdown, and renders the roles
in the preview. When only one family is observed, it keeps a restrained
single-family system instead of inventing an unsupported pairing.

Generated systems are project artifacts. Keep them under
`.keen/created/<slug>/` or another user-owned location. Do not copy them
into the plugin source or Claude cache. The current CLI does not expose a
persistent custom-system registry, so a generated name is not automatically
available to `--against`.

## Authentication and local targets

Prefer declarative `--auth-steps <json>`. `--auth-script` executes arbitrary
Python and requires explicit unsafe flags; review the exact file before use.

Private/loopback targets require `--allow-internal`, and `file://` targets
require `--allow-file`. These are deliberate SSRF boundaries, not errors to
bypass automatically.

## Known capture limits

- Cross-origin iframe contents are not introspected.
- Canvas/WebGL content can be captured but not decomposed into DOM components.
- Cross-origin stylesheets can limit focus-style inspection.
- Loading and empty states usually need product-specific setup.
- A loose PNG provides visual/color evidence but not DOM spacing, type, or
  accessibility semantics.

## Privacy

The harness runs locally and has no telemetry or model SDK. Chromium loads only
the target supplied by the user. The surrounding agent determines what artifact
content is sent to its model.

## Development

`AGENTS.md` is the contributor guide. `skills/keen/SKILL.md` is the
cross-platform umbrella and safety contract. Each focused workflow is a sibling
skill under `skills/ui-*/`; optional guides remain under
`skills/keen/references/`.

Run contract validation before tests:

```bash
python3 scripts/validate_prompts.py
python3 scripts/evaluate_skill_contracts.py
python3 scripts/sync_codex_plugin_bundle.py --check
python3 -m pytest -q
python3 /Users/kyleberry/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
claude plugin validate ../..
claude plugin validate .
```

`evals/activation-cases.json` is the shared deterministic activation corpus.
The contract evaluator validates routing, mutation
boundaries, references, and Codex invocation policy without calling a model.
Its `--platform claude` and `--platform codex` modes exercise fresh client
processes when those clients are authenticated.

## License

MIT. See `LICENSE`.
