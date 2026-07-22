<p align="center">
  <img src="plugins/keen/assets/keen-logo.png" width="148" alt="Keen logo">
</p>

<h1 align="center">Keen</h1>

<p align="center"><strong>A keen eye for UI systems.</strong></p>

<p align="center">
  <a href="https://github.com/KyBerry/keen/actions/workflows/ci.yml"><img src="https://github.com/KyBerry/keen/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-3.10%2B-172033" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-035CFE" alt="MIT license"></a>
</p>

Keen is a local-first UI/UX review toolkit for Claude Code, Codex, and the
command line. It captures the rendered interface, measures what can be measured,
and gives the model structured evidence for the work that still requires
judgment.

The split is deliberate: Python handles geometry, contrast, DOM state, token
math, and repeatable predicates. The agent handles intent, hierarchy,
prioritization, and design-system decisions.

## What Keen does

| Need | Keen workflow |
| --- | --- |
| Review a rendered product | Capture responsive states, grade measurable issues, and prioritize corrections |
| Find AI-default UI patterns | Measure repeated visual tells and propose specific escape moves |
| Compare a named system | Check against Material 3, Apple HIG, Fluent 2, Polaris, Carbon, or Atlassian |
| Understand the visual language | Extract tokens and characterize density, type, color, shape, and depth |
| Build a system from evidence | Turn a captured product, seed color, or measured inspiration into a validated proposal |

Every run writes inspectable artifacts under `.keen/`. Screenshots, extracted
DOM evidence, JSON, Markdown, and HTML stay available for review instead of
being hidden inside a model response.

<details>
  <summary><strong>See an example system review</strong></summary>
  <br>
  <img src="plugins/keen/assets/review-preview.png" width="900" alt="Example Keen design-system review">
</details>

## Install

Keen has two parts: a host plugin with model workflows and a Python CLI with the
measurement runtime. Install both.

```bash
git clone https://github.com/KyBerry/keen.git
cd keen
```

### Claude Code

```bash
claude plugin marketplace add "$PWD"
claude plugin install keen@keen
uv tool install --force --reinstall --editable "$PWD/plugins/keen"
uv tool run --from "$PWD/plugins/keen" playwright install chromium
keen doctor
```

Start a new Claude Code session after installation so skill discovery reloads.

### Codex

```bash
codex plugin marketplace add "$PWD"
codex plugin add keen@keen
uv tool install --force --reinstall --editable "$PWD/plugins/keen"
uv tool run --from "$PWD/plugins/keen" playwright install chromium
keen doctor
```

Start a new Codex task after installation so the new plugin is available to the
model.

## Use it

Ask naturally:

> Review this rendered dashboard and prioritize the three highest-leverage fixes.

Or invoke a focused workflow such as `/keen:ui-deslop` in Claude Code or
`$keen:ui-deslop` in Codex.

The CLI is useful in scripts and for inspecting intermediate artifacts:

```bash
keen review "https://app.example.com/dashboard" --against material-3
keen capture "http://localhost:3000" --viewports mobile,desktop
keen audit ".keen/review/<run>"
keen tokens ".keen/review/<run>"
keen slop ".keen/review/<run>" --print-markdown
keen taste ".keen/review/<run>"
keen diff ".keen/review/<old>" ".keen/review/<new>"
```

Run `keen <command> --help` for the authoritative options.

## Workflows

- `ui-review` — complete evidence-based review
- `ui-capture` — screenshots and DOM evidence without critique
- `ui-audit` — reanalyze an existing run
- `ui-compare` — compare with one named design system
- `ui-tokens` — extract observed design tokens
- `ui-deslop` — isolate recognizable AI-default patterns
- `ui-taste` — describe the measurable visual signature
- `ui-intent` — reconcile the screen with the user's likely job
- `ui-systemize` — reduce observed evidence into a system proposal
- `ui-create` — create and validate a new system
- `ui-remix` — transform measured inspiration into a distinct direction

## Safety model

Keen is conservative around rendered products and credentials:

- localhost and remote targets are treated as untrusted input;
- declarative `--auth-steps` is preferred over executable auth scripts;
- internal-network and `file:` access require explicit flags;
- plugins do not install Python packages or browsers as a side effect;
- review workflows write evidence but do not silently edit product source;
- only creation workflows may produce design-system proposals.

See [declarative authentication](plugins/keen/docs/auth-steps.md) for the full
credential-handling contract.

## Repository layout

```text
.agents/plugins/marketplace.json   Codex marketplace
.claude-plugin/marketplace.json    Claude Code marketplace
codex-plugins/keen/                generated, allowlisted Codex bundle
plugins/keen/                      canonical plugin and Python package
```

Do not edit `codex-plugins/keen/` directly. Build it from the canonical source:

```bash
python plugins/keen/scripts/sync_codex_plugin_bundle.py
```

## Develop

```bash
cd plugins/keen
uv sync --all-extras --locked
uv run playwright install chromium
uv run pytest -q
uv run ruff check harness tests scripts
uv run mypy harness
uv run python scripts/validate_prompts.py
uv run python scripts/smoke_wheel.py
```

Keen currently has 974 passing tests, deterministic activation-contract checks,
strict plugin validation, and isolated wheel-install coverage.

## License

[MIT](LICENSE) © Kyle Berry
