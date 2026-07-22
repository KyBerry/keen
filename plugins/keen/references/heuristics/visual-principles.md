---
source: "internal + cited below"
last_verified: 2026-05-23
---

# Visual Design Principles for UI Critique

These are perceptual and compositional principles. They precede any design
system. A product can be technically compliant with Material 3 and still
fail every one of these.

## 1. Hierarchy

The eye should land on what matters first, second, and third without effort.

**How hierarchy is built (in order of strength):**
1. **Position** — top-left in LTR languages reads first.
2. **Size** — big things read before small.
3. **Weight** — bold reads before regular.
4. **Color contrast** — high-contrast reads before low.
5. **Whitespace** — isolated elements read before dense ones.
6. **Color hue** — warm reads before cool, saturated before muted.

**Failure modes:**
- Three elements all competing for "first read" (e.g., a hero headline, a
  banner CTA, and a notification badge all at maximum visual weight).
- The actual primary action is visually subordinate to a secondary one
  (often happens when "Cancel" is styled as a primary button by mistake).
- Page title, section title, and card title all rendered at the same size.

**In a critique, name:**
- What you think the user is *supposed* to look at first.
- What you actually look at first.
- The mechanic causing the mismatch.

## 2. Type scale

Type at 3 well-chosen sizes beats type at 8 arbitrary ones.

**Healthy scales:**
- Modular ratios: 1.125 (minor second), 1.2 (minor third), 1.25 (major
  third), 1.333 (perfect fourth), 1.414 (augmented fourth, common in
  display-heavy UIs), 1.5 (perfect fifth — large jumps, used at the top of
  a scale).
- Most product UIs need **5–7 sizes total**: caption, body, body-lg, h3, h2,
  h1, display.

**Body text:**
- **14–16px** is the de-facto web standard for product UI. (author judgment;
  matches Material 3 `Body Medium` 14 / `Body Large` 16 and Apple HIG
  Body 17)
- **Below 12px**, comprehension drops sharply for non-tabular content.
  (author judgment)
- **Above 18px** for body, the screen feels like a kid's book.
  (author judgment)

**Line height:**
- Body: **1.4–1.6x** font size. (author judgment; aligns with Material 3's
  Body line heights of 20/24 against 14/16 sizes)
- Headings: **1.1–1.3x** font size. (author judgment)
- Code / monospace: **1.5x**. (author judgment)
- Below 1.3 for body, lines feel cramped. Above 1.7, lines feel disconnected.
  (author judgment)

**Line length (measure):**
- **45–75 characters** for prose. ~65 is ideal.
- Tables and forms can be wider — those aren't continuous reading.

## 3. Alignment

Elements relate to each other through alignment more than through proximity.

**Rules:**
- Never align two things "almost" the same. Either the same axis or
  obviously different axes.
- Strong vertical edges anchor a layout. A page where left edges of
  headings, body text, and form fields are within 4px of each other but not
  identical reads as broken.
- Center alignment for prose is for ceremonial moments (covers, hero
  headlines). Not for body content. Not for forms. Not for paragraphs.

**Failure tells:**
- Form labels left-aligned, inputs centered, help text right-aligned.
- A title that's 8px off from the column it should anchor to.
- Bullet points where the bullets align but the text doesn't (or vice versa).

## 4. Rhythm and grid

Whitespace between elements should be a small set of values, repeated
predictably.

- A 4px or 8px base grid gives you 4/8/12/16/24/32/48/64.
- A spacing scale of **5–7 values** is enough for a product. More than 10
  and there's no rhythm — it's just freehand.
- Vertical rhythm between sections should be **larger** than between
  components within a section, which should be larger than the gap between
  related elements (label → input).

**Failure tells:**
- Spacing values in the wild: 13px, 18px, 23px (off-grid by 1).
- Padding inside a card different from the gap *between* cards by an
  arbitrary amount.
- A "tight" component placed adjacent to a "loose" one, with no apparent
  reason for the difference.

## 5. Color

Color in a product is a system, not an aesthetic.

**Functional roles a color system needs:**
- Surface (background)
- Text on surface (and text-subtle, text-disabled)
- Border / divider
- Primary brand (and a state for hover, active, disabled)
- Semantic: success, warning, danger, info

**Common failures:**
- A "blue" used for links, primary buttons, focus rings, AND informational
  callouts. The user can't tell what's interactive.
- Success, warning, and danger that don't have AA-compliant text colors
  baked into the system. Designers reach for the brand red, find it hits
  3.8:1, and ship it anyway.
- Two greys that are 3% apart in lightness used as "different" colors.
  Either commit to a difference or use one.

**Saturation:**
- High saturation reads as urgent. Use sparingly.
- A whole UI in 90%+ saturation feels like a slot machine.
- Greyed-down accents (HSL with 30–60% saturation) are the workhorse for
  product UI.

## 6. Contrast (perceptual, not just WCAG)

WCAG contrast ratios are a floor, not a target.

- Body text at exactly 4.5:1 is technically compliant and uncomfortable to
  read for long sessions. **7:1+** is the comfort zone. (4.5:1 from WCAG
  2.2 SC 1.4.3; 7:1 is WCAG SC 1.4.6 AAA threshold)
- Disabled text should be **clearly** disabled — at 1.5–2.5:1 against
  surface — not just "a bit lighter" (which reads as low-quality body text,
  not disabled). (author judgment)
- An icon that conveys meaning (a warning triangle, a status dot) needs
  more contrast than its decorative neighbors. (author judgment, aligned
  with WCAG SC 1.4.11 Non-text Contrast 3:1)

## 7. Density

Density is a deliberate choice, not a default.

- **Dense** (Bloomberg, Linear, Jira): more information per screen, less
  whitespace, smaller targets, smaller text. The user is an expert,
  rewarded for time invested in learning the layout.
- **Spacious** (Apple Notes, Notion, Stripe marketing): less per screen,
  more breathing room, larger targets. The user is a novice or visiting,
  rewarded for clarity.
- **Mixed density** is almost always wrong. A dense table inside a spacious
  shell, or a spacious form inside a dense dashboard, reads as inconsistent.

## 8. Motion

Motion exists to explain causality, not decorate.

- **150–250ms** for state changes (hover, focus). (author judgment)
- **200–400ms** for component changes (modal open, drawer slide). (author
  judgment; aligns with Material 3's 200–300ms component-level durations)
- **400–600ms** for major transitions (page navigation). (author judgment)
- Above 600ms, motion is *the experience*, not a transition — only
  appropriate for marketing or onboarding moments. (author judgment)
- Easing should be `ease-out` (decelerating) for most UI — things settle.
  `ease-in` (accelerating) is for things leaving. (author judgment)
- Motion that doesn't respect `prefers-reduced-motion` is a violation.
  (WCAG SC 2.3.3 Animation from Interactions AAA; respecting reduced
  motion is also explicit in Apple HIG and Material 3.)

## 9. Affordance

The visual design tells the user what's interactive without them having to
hover-test.

- Buttons should look pressable: contrast, perhaps a subtle shadow or
  border, padding that suggests "I am a target."
- Links in body text need a non-color signal: underline, weight change, or
  position (in a list of nav items).
- Disabled states should *look* disabled — desaturated, lower contrast,
  often with a `not-allowed` cursor.
- Inputs should look fillable: a visible boundary, a visible inside, not
  borderless-on-white-on-white.

## How to write a visual critique

For each element flagged:
1. Name the principle (hierarchy, alignment, rhythm, etc.).
2. Describe the *perceptual* problem in one sentence ("the section title
   reads as smaller than the body intro because it has the same size and
   less weight").
3. Give the fix with a specific value.

A critique that says "improve hierarchy" is not actionable.
A critique that says "raise the section title to 20px / 600 weight, and
drop the intro to 14px / 400, so the title is the visually dominant
element of the section" is actionable.

## Sources
- Robert Bringhurst, *The Elements of Typographic Style* — line-length
  guidance of 45–75 characters (~66 ideal) for continuous reading.
- Apple Human Interface Guidelines, Dynamic Type:
  https://developer.apple.com/design/human-interface-guidelines/typography
- WCAG 2.2 SC 1.4.3 Contrast (Minimum) — 4.5:1 normal / 3:1 large text:
  https://www.w3.org/TR/WCAG22/#contrast-minimum
- WCAG 2.2 SC 1.4.6 Contrast (Enhanced) — 7:1 AAA target:
  https://www.w3.org/TR/WCAG22/#contrast-enhanced
- Material Design 3 type scale:
  https://m3.material.io/styles/typography/type-scale-tokens

Items marked `(author judgment)` above are opinionated calibrations
drawn from working practice; they're presented as priors, not as cited
rules.
