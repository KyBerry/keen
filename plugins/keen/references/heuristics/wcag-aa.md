---
version: "WCAG 2.2 AA (updated 2024-12-12)"
source: https://www.w3.org/TR/WCAG22/
last_verified: 2026-05-23
---

# WCAG 2.2 Level AA — Reference for the Harness

This is **not** a complete WCAG reference. It's the subset of success criteria
that the harness can plausibly check from a screenshot + DOM dump, plus the
ones the LLM agent should manually look for in component review.

## Predicate-checked criteria (automated)

These are checked by `harness/analyze.py`. When citing them in a report, use
the SC number alongside the predicate id.

### SC 1.4.3 Contrast (Minimum) — AA
- **Text < 18px or < 14px bold**: contrast ratio against background ≥ **4.5:1**.
- **Text ≥ 18px or ≥ 14px bold ("large text")**: ≥ **3:1**.
- **Predicate**: `contrast_text_aa`.

### SC 1.4.11 Non-text Contrast — AA
- UI components (button borders, form input borders, focus indicators, icons
  carrying meaning) and graphical objects need ≥ **3:1** against adjacent
  colors.
- **Predicate**: `contrast_ui_aa`.
- Out of scope of the harness today for graphical objects, but flagged for
  agent review.

### SC 2.4.7 Focus Visible — AA
- Any keyboard-focusable element must have a visible focus indicator.
- **Predicate**: `focus_visible` (checks for non-`none` outline OR a
  declared focus style; flags `outline: none` without a replacement).

### SC 2.5.5 / 2.5.8 Target Size
- **AA (2.5.8, new in 2.2)**: ≥ **24×24 CSS px**.
- **AAA (2.5.5)**: ≥ **44×44 CSS px**.
- Generic reviews check the 24px AA floor with `target.size-aa` and preserve
  WCAG's inline/spacing caveats for manual confirmation.
- When a named system is selected, `hit-target.size` separately checks that
  system's preferred minimum (Material 48, Apple HIG 44, ADS 40, etc.). A
  system preference is not reported as generic WCAG conformance.

### SC 4.1.2 Name, Role, Value — A
- All interactive controls expose a name programmatically. A bare icon
  button with no aria-label, no text content, no `aria-labelledby`, and no
  associated `<label>` fails.
- **Predicate**: `accessible_name`.

### SC 1.3.1 Info and Relationships — A
- Form inputs have a programmatically associated label (`<label for>`,
  wrapping `<label>`, `aria-label`, or `aria-labelledby`).
- **Predicate**: `form_label`.

### SC 2.4.6 Headings and Labels — AA
- Heading levels don't skip (`h1` then `h3` with no `h2`). Each page has
  exactly one `h1`.
- **Predicate**: `heading_hierarchy`.

### SC 1.4.1 Use of Color — A
- Color isn't the *only* means of conveying information. The harness flags
  link styling: a link in body text with no underline AND a color that
  doesn't reach 3:1 contrast against the surrounding body text fails this.
- **Predicate**: `link_distinguishable`.

## Manual-review criteria (agent prompts)

These can't be reliably automated from the harness signal, but the agent
should look for them when reviewing component crops.

### SC 1.1.1 Non-text Content — A
- Every non-decorative image has alt text. Decorative images use `alt=""`.
- The harness can list `<img>` elements missing an `alt` attribute, but
  can't tell if a present `alt` is *meaningful* (e.g., `alt="image"` is
  technically present, semantically useless).

### SC 1.4.4 Resize Text — AA
- Text can be resized to 200% without loss of content or functionality.
- Reviewable by re-running the harness at 200% browser zoom and diffing
  the layouts.

### SC 1.4.10 Reflow — AA
- Content reflows at **320 CSS px wide** without horizontal scrolling
  (except for things that genuinely require 2D, like tables and maps).
- Reviewable by capturing a 320px viewport.

### SC 1.4.12 Text Spacing — AA
- The user can override line-height to 1.5x font size, paragraph spacing
  to 2x font size, letter-spacing to 0.12x, word-spacing to 0.16x, with no
  loss of content.
- Reviewable by injecting an override stylesheet and re-capturing.

### SC 2.1.1 Keyboard — A
- All functionality is operable from a keyboard.
- The harness can flag elements with `onclick` handlers but no `tabindex`
  and no role; the agent should manually walk the tab order on capture.

### SC 2.1.2 No Keyboard Trap — A
- Focus can leave any element via standard navigation.
- Manual review only — flag any custom widget (combobox, datepicker, modal)
  for explicit keyboard-trap testing.

### SC 2.4.3 Focus Order — A
- Tab order matches the visual / reading order.
- The harness can dump tab order as a list of `tabindex` ≥ 0 elements with
  their bounding boxes; the agent flags inversions.

### SC 2.4.11 Focus Not Obscured (Minimum) — AA (new in 2.2)
- The focused element isn't entirely hidden behind sticky headers, cookie
  banners, or other overlays.
- Manual review: capture with focus on each major navigation target, scroll
  position varied.

### SC 2.5.7 Dragging Movements — AA (new in 2.2)
- Any drag-based interaction has a non-drag alternative (click-to-reorder,
  arrow-key reorder, etc.).
- Manual: identify drag affordances and look for the alternative.

### SC 3.3.7 Redundant Entry — A (new in 2.2)
- Information already entered isn't asked for again in the same session.
- Manual: walk multi-step flows.

### SC 3.3.8 Accessible Authentication (Minimum) — AA (new in 2.2)
- Authentication doesn't rely on a cognitive function test (transcribing
  characters, solving a puzzle) without an alternative.
- Manual: review login, MFA, password reset.

## How to cite WCAG in a report

Bad: "Fails WCAG."

Better: "Fails WCAG 2.2 SC 1.4.3 (Contrast Minimum, AA): the body text in
the secondary card hits 3.1:1 against its surface — 4.5:1 required for
14px regular text. Either darken the text to `#3a3a3a` (5.2:1) or lighten
the surface to `#f8f8f8` (4.6:1)."

Always include:
1. SC number and short title.
2. Conformance level (A / AA / AAA).
3. The measured value vs the required value.
4. A concrete fix with a number, not a direction.

## Sources
- WCAG 2.2 (W3C Recommendation): https://www.w3.org/TR/WCAG22/
- W3C WAI overview of WCAG: https://www.w3.org/WAI/standards-guidelines/wcag/
