# Real-world site coverage

Last tested: 2026-05-23. Playwright 1.60.0, Chromium for Testing
148.0.7778.96. Python 3.12.8. Default rubric (`config/rubric.yaml`).

## Methodology

For each site, ran:

```bash
.venv/bin/python -m harness review <url> \
    --out /tmp/d-rw/<slug> \
    --viewports desktop \
    --against material-3 \
    --goto-timeout 60
```

Default settings — no banner dismissal, no settle wait, no auth. The goal was
to surface where the pipeline breaks on real content, not to make every site
look good.

## Per-site results

| Site | Slug | Status | Elements (DOM) | Components | Truncated? | Screenshot | Signal band* | Candidate findings |
|---|---|---|---:|---:|---|---:|:---:|---:|
| m3.material.io | `m3` | ✓ captures | 327 | 97 | no | 144 KB | F | 15 |
| developer.apple.com/design | `apple` | ✓ captures | 593 | 279 | no | 392 KB | F | 15 |
| stripe.com | `stripe` | ✓ captures | 1 723 | 397 | no | 3.6 MB | F | 15 |
| vercel.com | `vercel` | ✓ captures | 1 603 | 377 | no | 1.1 MB | F | 15 |
| linear.app | `linear` | ✓ captures | 2 392 | 279 | no | 1.2 MB | F | 15 |
| github.com | `github` | ✓ captures | 913 | 237 | no | 2.1 MB | F | 15 |
| news.ycombinator.com | `hn` | ✓ captures | 773 | 493 | no | 279 KB | F | 15 |
| figma.com | `figma` | ✓ captures | 833 | 419 | no | 1.8 MB | F | 15 |
| notion.so | `notion` | ✓ captures | 804 | 257 | no | 1.8 MB | F | 15 |
| nytimes.com | `nyt` | △ truncated | **5 000** | 329 | **yes** | 2.7 MB | F | 15 |

\* "Grade" is the rubric grade derived from `top_findings` density. F is the
worst grade; every real-world site received F under the default rubric. This
is a known false-positive issue — see "Known limitations" below.

## Top issues found

### 1. Rubric over-fires on rich landing pages
**Observed on:** all 10 sites. Every site graded F.

The default rubric assumes a "design surface" — a single screen, a few dozen
components. Real marketing landing pages have 200–500 components, and even a
small per-component P1 rate guarantees the damage score blows past the F
threshold (75). On stripe.com — a site with one of the strongest design
systems in the industry — the harness flags hundreds of P1s and Fs out.

This is the most important real-world finding. Fix is rubric-level (out of
scope for this pass), but the directional ideas:
- Density-normalized scoring (damage per component) instead of absolute damage.
- Per-component-kind weighting (a P1 on a footer link is not the same as a P1
  on a primary CTA).
- Sampling: when components > 200, evaluate predicates on a stratified sample.

### 2. DOM element-count truncation on long pages
**Observed on:** nytimes.com (5 000 cap hit).

The `INSTRUMENT_JS` walk caps at 5 000 visible nodes. NYT's homepage —
articles + ads + skyboxes + footer — exceeds this with the lazy-loaded
content placeholders included. Truncated runs miss the lower half of the page.

**Status:** the cap stays at 5 000 to bound memory and DOM-payload size. The
new `dom.truncated` flag is already exposed; the report should surface it.
For NYT-class pages, recommend running with `--viewports mobile` to capture
the more focused above-the-fold content, OR adding a future
`--max-elements N` CLI flag (intentionally not added this pass to avoid CLI
churn).

### 3. Weighted candidate index missing from `report.json`
**Observed on:** all 10 sites (and example.com).

`report.json` has `score.grade` but `score.damage` is `None`. The grade is
computed from the damage value, so the damage *exists* during scoring but is
not being persisted into the output. This is a real bug, surfaced this pass
but not yet fixed (out of scope — `report.py` is owned by a concurrent agent
this round).

### 4. SPA hydration races (skeleton-only captures)
**Observed on:** nytimes.com (lazy skeletons dominate below the fold);
suspected on figma.com (heavy WebAssembly canvas) and notion.so (auth-walled
content area shows marketing surface).

Without an extra wait after `domcontentloaded`, JS-rendered placeholders
are captured instead of real content. Fix shipped this pass:
`CaptureConfig.settle_ms` (default 0).

### 5. Cookie-banner overlays
**Observed on:** nytimes.com (consent banner covered top 200 px), and
implicated on github.com / linear.app for EU-region runs.

Banner overlays steal screen real estate and capture as a component cluster.
Fix shipped this pass: `CaptureConfig.dismiss_banners` (default False) plus
an `_dismiss_common_banners` helper with a closed selector whitelist.

### 6. Shadow DOM elements invisible
**Observed on:** figma.com (heavily uses web components), suspected on
several SaaS sites that use Stencil/Lit-based design systems.

`document.body.querySelectorAll('*')` does not traverse shadow roots, so any
component defined inside a `<custom-element>` is missing. Fix shipped this
pass: `collectAll()` in `INSTRUMENT_JS` walks shadow trees recursively
(iterative BFS, bounded by 2 × `MAX_ELEMENTS` for traversal safety).

## Fixes shipped this pass

| Fix | File:line | What it does |
|---|---|---|
| `CaptureConfig.settle_ms: int = 0` | `harness/capture.py:159–165` | New dataclass field. Extra ms wait after `domcontentloaded`+`wait_selector` to let JS hydrate. |
| `CaptureConfig.dismiss_banners: bool = False` | `harness/capture.py:166–169` | New dataclass field. Enables banner-accept clicking before screenshot. |
| `COMMON_BANNER_DISMISS` closed selector list | `harness/capture.py:~1190` | Conservative whitelist of common cookie/GDPR accept-button selectors. |
| `_dismiss_common_banners(page)` async helper | `harness/capture.py:~1208` | Tries each selector with 500 ms per-selector timeout; clicks first match; logs which. |
| Shadow-DOM walk in `INSTRUMENT_JS` | `harness/capture.py:~272 (JS)` | Replaces `document.body.querySelectorAll('*')` with iterative BFS through shadow roots. |
| `settle_ms` / `dismiss_banners` wired into `_capture_one` | `harness/capture.py:~1278–1289` | Both run after font-ready and before the state-setup steps. |

## Known limitations (not fixed this pass)

- **CLI wiring**: `--settle-ms` and `--dismiss-banners` flags are not yet
  exposed on `cli.py`. The dataclass fields exist; a future commit (or an
  agent owning `cli.py` next round) can wire them.
- **Rubric over-fires on real-world pages** — every site graded F. This is
  the most-impactful issue surfaced this pass. Needs rubric redesign.
- **`score.damage` is `None`** in `report.json`. Real bug, separate fix.
- **Iframe content** is not walked. Cross-origin iframes can't be reached
  without policy relaxation. Same-origin iframes could be supported in a
  future pass.
- **Strict CSP sites** (nytimes.com) may eventually block the instrument
  evaluation; not observed in this pass but worth monitoring. Mitigation
  if needed: switch to `addInitScript` to evaluate at page-init time
  before CSP enforces.
- **Lazy-loaded images via IntersectionObserver** still appear as skeletons
  in some captures. `settle_ms` helps but doesn't trigger viewport scroll.
  Future option: a `--scroll-page` flag that scrolls top-to-bottom before
  capture.

## Recommended follow-ups

1. **Rubric redesign** — density-normalize damage; per-component-kind
   weighting; sample-based scoring for component counts > 200.
2. **CLI wiring** — expose `--settle-ms` and `--dismiss-banners` in
   `harness/cli.py`'s `_add_capture_args`.
3. **Fix `score.damage`** — populated in `harness/report.py` `compose()`.
4. **Surface `dom.truncated`** in `summary.md` so the reviewer knows the
   analysis is incomplete.
5. **`--scroll-page` flag** — scroll the page top-to-bottom to trigger
   IntersectionObserver-based lazy loaders before screenshot.
6. **Add a `--max-elements N` override** for NYT-class long pages.
