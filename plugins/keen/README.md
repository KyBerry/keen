# Keen plugin and local tool

This is the main source for Keen. It contains the coding-agent workflows and
the local tool Keen uses to check real pages in a browser.

Keen has four focused ways to help:

- **Explore** — find a look and feel that fits the product.
- **Establish** — make the chosen direction clear and easy to continue.
- **Refine** — improve a real page until it feels polished and finished.
- **Guard** — catch changes that no longer fit the product.

The command-line tool gathers screenshots and measurements. The coding agent
uses that evidence, along with the product's goals and constraints, to make the
design decisions.

## Requirements

- Python 3.10 or newer
- `uv`
- Chromium installed for Playwright capture
- Claude Code or Codex for the model workflow

Plugin installation and the Python runtime are deliberately separate. Install
both, then start a new host session so skill discovery refreshes.

## Claude Code install

From the repository root:

```bash
claude plugin marketplace add "$PWD"
claude plugin install keen@keen
uv tool install --force --reinstall --editable "$PWD/plugins/keen"
uv tool run --from "$PWD/plugins/keen" playwright install chromium
keen doctor
```

## Codex install

```bash
codex plugin marketplace add "$PWD"
codex plugin add keen@keen
uv tool install --force --reinstall --editable "$PWD/plugins/keen"
uv tool run --from "$PWD/plugins/keen" playwright install chromium
keen doctor
```

The Codex marketplace points at `codex-plugins/keen/`, an allowlisted bundle
generated from this directory. During development:

```bash
python3 scripts/sync_codex_plugin_bundle.py
```

## Local direction workshop

When a choice is easier to make by seeing it, Keen can open a small local page
with two or three options made for the product. The user can choose one, mix
ideas, or reject the set. The workshop accepts a limited data format, runs only
on the local computer, and loads no remote images.

```bash
keen workshop schema spec > /tmp/keen-workshop-schema.json
keen workshop validate /tmp/direction-round.json
keen workshop serve /tmp/direction-round.json --response /tmp/direction-response.json
keen workshop summarize /tmp/direction-round.json /tmp/direction-response.json
```

Keen then makes the smallest useful prototype and checks it in the browser.
Only a decision that still works after that check is saved:

```bash
keen workshop schema promotion
keen workshop promote /path/to/project /tmp/reviewed-promotion.json
keen context validate /path/to/project
```

Workshop files belong in an operating-system temporary directory. Lasting
project guidance stays in `.keen/design-context.json` and `.keen/direction.md`.

## Saved design direction

Create the two files Keen uses to remember the project's design direction:

```bash
keen context init /path/to/project --name "Project" --stage explore
keen context validate /path/to/project
keen context show /path/to/project
keen context render /path/to/project
```

The JSON file gives coding agents structured context. The Markdown file gives
people a readable summary. Commit both when the team wants every developer and
agent to follow the same direction. Temporary captures can remain ignored.

## CLI primitives

```bash
keen doctor
keen review "https://app.example.com/dashboard"
keen capture "http://localhost:3000" --viewports mobile,desktop
keen audit ".keen/review/<run>"
keen compare "https://app.example.com" --against apple-hig
keen tokens ".keen/review/<run>"
keen slop ".keen/review/<run>"
keen taste ".keen/review/<run>"
keen intent ".keen/review/<run>"
keen workshop --help
keen systemize ".keen/review/<run>" --name project
keen diff ".keen/review/<old>" ".keen/review/<new>"
```

Run `keen <subcommand> --help` for authoritative options. Exact comparison
identifiers are `apple-hig`, `fluent-2`, `material-3`, `polaris`, `carbon`, and
`atlassian`.

## Review output

```text
agent-brief.json                   bounded model-first context and evidence
report.html                        organized human evidence review
summary.md                         compact human fallback
report.json                        complete structured evidence
capture-manifest.json              requested and successful capture matrix
screens/*                          captured and annotated rendered states
dom/*.json                         DOM and accessibility measurements
components/*.json / *.png          element evidence and selected crops
analysis/*.json                    deterministic candidate findings
tokens/extracted.json              observed tokens
slop.json / taste.json             optional characterization signals
```

`agent-brief.json` includes the nearest project design context, capture target
and state, named element identity, measured and expected values, coverage, and
an explicit decision contract. It labels automated scores as candidate-signal
density. Agents should read this artifact first and query the full report only
for selected evidence.

## Authentication and local targets

Prefer declarative `--auth-steps <json>`. `--auth-script` executes arbitrary
Python and requires explicit unsafe flags plus user authorization for the exact
reviewed file. Internal-network and `file:` targets require `--allow-internal`
or `--allow-file`.

See [declarative authentication](docs/auth-steps.md) for the closed action DSL.

## Known capture limits

- Canvas and WebGL pixels can be captured but not decomposed into DOM elements.
- Cross-origin iframes may not expose internal nodes.
- Very tall pages may produce provisional viewport screenshots when device
  pixel limits prevent a reliable full-page image.
- Authentication, delayed data, virtualized content, and unusual interaction
  states require explicit capture setup.

Coverage gaps are not findings and automated contrast or system-drift signals
still require contextual model judgment.

## Development

```bash
uv sync --all-extras --locked
uv run playwright install chromium
uv run pytest -q
uv run ruff check harness tests scripts
uv run mypy harness
uv run python scripts/validate_prompts.py
uv run python scripts/evaluate_skill_contracts.py
uv run python scripts/evaluate_direction_workshop.py --platform static
uv run python scripts/smoke_wheel.py
python3 scripts/sync_codex_plugin_bundle.py --check
```

The plugin runtime directory is not a workspace for generated systems or
reviews. Keep all user artifacts under the user's project `.keen/` directory.

## License

[MIT](LICENSE)
