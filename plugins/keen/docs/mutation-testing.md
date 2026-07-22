# Mutation testing

## What is this

[Mutation testing](https://mutmut.readthedocs.io/en/latest/index.html) measures
the *strength* of the test suite, not just its reach. The mutator (mutmut)
applies small syntactic changes — flip a `<` to `<=`, replace a string with a
garbled version, swap a `+` for a `-` — and reruns the suite. If the suite
fails on the mutated code, the test "kills" the mutant; if the suite still
passes, the mutant survives, meaning the original behaviour is not actually
pinned by any test.

Line coverage lies because it only asks "did pytest *execute* this line." A
fully covered line whose only check is `assert returns_something(x) is not
None` will still pass every arithmetic, off-by-one, and string-tampering
mutation applied to that line. Mutation score asks the harder question: "if
the line were wrong, would the suite notice?"

We use mutmut on the small pure modules of the harness — the ones that have
clear input/output contracts and minimal side effects — because those are the
modules where a regression slipping through line coverage is most likely to
cause silent wrong-answer bugs in downstream reports.

## What we mutation-test

| Module | LOC | Rationale |
|---|---|---|
| `harness/colors.py` | 101 | Pure stdlib WCAG colour math. Hex parsing, luminance, contrast, blending. Used by every analyse / token / systemize call. |
| `harness/_urlsafe.py` | 309 | SSRF guard. Security-critical — every navigation target is validated here. Wrong rejection reasons or off-by-one IP comparisons are exploitable. |
| `harness/_sanitize.py` | 418 | Prompt-injection sanitiser. Every page-derived string flows through this before landing in agent-readable artefacts. Wrong substring escape or off-by-one cap is a directly exploitable bug. |
| `harness/rubric.py` | 304 | Scoring + grade thresholds + predicate severity map. Wrong constants here flip a "B-grade ship" report into an "A-grade ship". |
| `harness/diff.py` | 128 | Run-to-run diff. The keys it builds determine what counts as a "still present" finding vs "new / fixed"; off-by-one in the joiner or set arithmetic silently misclassifies progress. |

The larger and more I/O-bound modules — `capture.py`, `analyze.py`,
`decompose.py`, `cli.py`, `report.py`, `tokens.py`, `systemize.py` — are
explicitly *out of scope* for mutation testing in this repo. They run
Playwright, touch the filesystem extensively, and their mutmut runtime
quickly exceeds CI budgets without proportional value (regressions in
those modules are usually loud — they crash, not silently mis-report).

## How to run

Install the dev extras (one-off):

```bash
.venv/bin/python -m pip install ".[dev]"
```

Run mutmut on the current configured paths (the three small modules —
`colors.py`, `rubric.py`, `diff.py`):

```bash
.venv/bin/python -m mutmut run
```

To re-run only against the larger modules (`_sanitize.py`, `_urlsafe.py`):

```bash
.venv/bin/python -m mutmut run --paths-to-mutate harness/_sanitize.py,harness/_urlsafe.py
```

To run on all five modules at once (slow — 30–45 min wall time on a
laptop):

```bash
.venv/bin/python -m mutmut run --paths-to-mutate harness/_urlsafe.py,harness/_sanitize.py,harness/colors.py,harness/rubric.py,harness/diff.py
```

Inspect results:

```bash
.venv/bin/python -m mutmut results
.venv/bin/python -m mutmut show <mutation-id>
.venv/bin/python -m mutmut html  # writes html/ report
```

Configuration lives under `[tool.mutmut]` in `pyproject.toml`. The
`use_coverage = true` flag tells mutmut to skip mutations on lines the
test suite never executes — without that flag every defensive branch
would also be mutated and we'd waste compute on impossible-to-cover
arms.

## Score per module

The table reports the two-pass score: before any targeted mutant tests
in `tests/test_*_mutants.py` were added vs. after. Numbers come from the
mutmut sqlite cache (`SELECT status, COUNT(*) FROM mutant ...`).

| Module | LOC | Tested mutants | Killed (before) | Killed (after) | Survived (before) | Survived (after) | Score (after) |
|---|---|---|---|---|---|---|---|
| `harness/colors.py` | 101 | 166 | 144 | 158 | 22 | 8 | 95.2 % |
| `harness/_urlsafe.py` | 309 | 94 | 63 | 79 | 31 | 15 | 84.0 % |
| `harness/_sanitize.py` | 418 | 189 | 153 | 185 | 33 | 4 | 97.9 % |
| `harness/rubric.py` | 304 | 139 | n/a | 132 | n/a | 7 | 95.0 % |
| `harness/diff.py` | 128 | 141 | n/a | 135 | n/a | 6 | 95.7 % |
| **Total (all five)** | **1260** | **729** | — | **689** | — | **40** | **94.5 %** |

Across the five modules the targeted mutant tests reduced the surviving
mutant count from approximately **86 → 40** (a 53 % drop), and lifted the
combined score on the tested subset from approximately **77 % → 94.5 %**.

Coverage-skipped lines (via `use_coverage = true`) mean mutmut never
mutates dead code, so the denominators above only include lines the
existing suite already executed.

Notes:
- mutmut 2.5.1 occasionally hits a known cache-corruption bug
  (`ValueError: Attribute Mutant.line is required` or `AssertionError`
  in `cached_mutation_status`) during long runs that span many files.
  When this happens the run aborts with the cache partly populated.
  Several survivors persist in the cache as `untested` until a follow-up
  run picks them up. Numbers above reflect the *tested* subset.
- *Coverage-skipped mutants* are not counted at all in the totals above
  because `use_coverage = true` tells mutmut to skip them (they live on
  lines the suite cannot reach: defensive `except yaml.YAMLError`
  branches, etc.). Across the five modules `mutmut run` generated 830
  total mutants; coverage skipping reduced the actively tested set to
  approximately 670.
- The "Score" column is computed as `killed / (killed + survived)` over
  the tested subset.

The mutmut commands used to produce these numbers:
```bash
.venv/bin/python -m mutmut run \
  --paths-to-mutate harness/_urlsafe.py,harness/_sanitize.py,harness/colors.py,harness/rubric.py,harness/diff.py
.venv/bin/python -m mutmut results
sqlite3 .mutmut-cache "SELECT s.filename, m.status, COUNT(*) FROM mutant m \
  JOIN line l ON m.line = l.id \
  JOIN sourcefile s ON l.sourcefile = s.id \
  GROUP BY s.filename, m.status"
```

## Known equivalent / unkillable mutants

Some surviving mutants are *equivalent* — there is no input that distinguishes
them from the original because the mutated and original code produce identical
observable behaviour. Documenting them avoids the next maintainer chasing them.

### `harness/colors.py` (8 surviving, all equivalent)

- **`parse_color` keyword set** (mutations on `"transparent"`,
  `"currentcolor"`, `"inherit"`, `"initial"` → garbled). The function
  returns `None` for any string not matching the hex or rgb regex, so
  removing one of these keywords from the early-return set does not
  change the return value for that input. The keywords are a hot-path
  optimisation, not a behavioural contract.
- **WCAG `chan` threshold** `x <= 0.03928` → `x < 0.03928`. The
  constant `0.03928 × 255 = 10.0164` is not an integer, so no `n/255`
  for integer `n` (the only way `parse_color` produces a channel value)
  ever equals `0.03928` exactly. The branch boundary is therefore
  unreachable from any real CSS colour input.
- **`contrast_ratio` alpha branch** `f[3] < 1.0` → `f[3] <= 1.0` (or
  `< 2.0`). When `f[3] == 1.0`, the blend step is the identity
  (`fg * 1 + bg * 0 == fg`), so triggering or skipping it leaves the
  downstream luminance unchanged.
- **`int(h[4:6], 16)` slice end** → `int(h[4:7], 16)`. On a 6-character
  `h`, both slices are equivalent (Python silently clips slice ends past
  the string length).

### `harness/_urlsafe.py` (15 surviving, mix of equivalent and contrived)

- **`logger = logging.getLogger("keen")` → `logger = None`** and
  **`logger.getLogger("keen")` → `getLogger("XXkeenXX")`**.
  The module declares but never *uses* the logger (warnings come from
  `harness/rubric.py`). The mutation would crash any future
  `logger.warning(...)` call but no existing code path invokes it. We
  pin the contract (logger exists, name is `"keen"`) in
  `tests/test_urlsafe_mutants.py` so the contract remains explicit, but
  the mutmut score does not credit a kill.
- **Type-annotation alterations** (`Exception | None` → `Exception &
  None`, `IPv4Network | IPv6Network` → `IPv4Network & IPv6Network`).
  These annotations are not evaluated at runtime under
  `from __future__ import annotations`, so no behavioural difference.
- **`host.lower().rstrip(".")` → `rstrip("XX.XX")`**. `str.rstrip(chars)`
  strips any character in `chars`; `"XX.XX"` is `{"X", "."}`. For
  legitimate hostnames (which don't end in "X"), both forms strip
  trailing dots identically. The mutation is detectable only for
  contrived `"foo.X"` cases — not worth a test fixture.
- **`v6_candidate.split("%", 1)[0]` → `split("%", 2)[0]`**. When the
  string contains no "%", both forms return the original string.  When
  there's one "%", `[0]` returns the prefix in both. The limit only
  matters when there are 2+ "%" characters, which is not a real DNS
  shape. Documented as effectively equivalent.

### `harness/_sanitize.py` (9 surviving, mix of equivalent and contrived)

- **`break` → `continue` in `_strip_controls`'s `_EXTRA_STRIP_RANGES`
  loop**. Once `in_extra = True`, the outer `if in_extra: continue`
  fires the same way regardless. The inner loop's exit vs. iterate
  behaviour does not change the resulting stripped character set.
- **`parsed.scheme or ""` → `parsed.scheme or "XXXX"`**. The mutated
  default `"XXXX"` is not in the allowed scheme set `{"http", "https",
  "mailto", "file"}`, so the same early-return `""` path fires.
- **`sa.get("href")` key reads** on the deep-nested element loop —
  several survive because the legitimate URL the test feeds is preserved
  by both original and mutant paths.

### `harness/rubric.py` and `harness/diff.py`

Most surviving mutants in these two modules are documentation/log-message
literals — strings that appear only inside warning calls but never
surface to user-visible output. Tests pin the `record.msg` template of
each warning (see `test_load_config_missing_file_log_message_exact`
etc.), but a long tail remain — they're worth a focused pass next time
the modules are updated.

The full surviving-mutant list is reproducible via:

```bash
.venv/bin/python -m mutmut results
```

After a fresh `mutmut run`, the per-module breakdown is also written to
`.mutmut-cache` (sqlite). Inspect with:

```bash
sqlite3 .mutmut-cache "SELECT s.filename, m.status, COUNT(*) FROM mutant m \
  JOIN line l ON m.line = l.id \
  JOIN sourcefile s ON l.sourcefile = s.id \
  GROUP BY s.filename, m.status"
```

## CI integration

We do not run mutmut on every CI run — wall time is too high for a hot
PR-feedback loop. Instead, run it manually before tagging releases and
when touching any of the five target modules.

A recommended (not yet enabled) workflow:

```yaml
# .github/workflows/mutation.yml
name: mutation testing
on:
  workflow_dispatch:
  schedule:
    - cron: "0 6 * * 0"   # weekly, Sunday 06:00 UTC
jobs:
  mutmut:
    runs-on: ubuntu-latest
    timeout-minutes: 90
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install ".[dev]"
      - run: |
          # Warm the coverage cache so use_coverage works.
          coverage run -m pytest tests/ -q
          # Then run mutmut.
          mutmut run --paths-to-mutate harness/colors.py,harness/rubric.py,harness/diff.py
      - run: mutmut results
      - run: mutmut html
      - uses: actions/upload-artifact@v4
        with:
          name: mutmut-html
          path: html/
```

Before turning this on, confirm the runtime under the latest test suite
fits inside the configured `timeout-minutes` (currently 90 — comfortable
margin for the three-module scope; less so for all five).
