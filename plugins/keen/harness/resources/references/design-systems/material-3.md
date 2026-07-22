---
version: "Material 3 baseline + Expressive (2025+)"
source: https://m3.material.io
last_verified: 2026-05-23
---

# Material Design 3 (Material You)

Condensed reference. When you cite this, name the section and the specific value.

## Tokens

### Spacing
- Base grid: **4dp**. Compose layouts in 4-multiples.
- Common increments: 4, 8, 12, 16, 20, 24, 32, 40, 48, 56, 64.

### Type scale (px values for `1.0` density)
| Role | Size | Weight | Line height |
|------|------|--------|-------------|
| Display Large | 57 | 400 | 64 |
| Display Medium | 45 | 400 | 52 |
| Display Small | 36 | 400 | 44 |
| Headline Large | 32 | 400 | 40 |
| Headline Medium | 28 | 400 | 36 |
| Headline Small | 24 | 400 | 32 |
| Title Large | 22 | 400 | 28 |
| Title Medium | 16 | 500 | 24 |
| Title Small | 14 | 500 | 20 |
| Body Large | 16 | 400 | 24 |
| Body Medium | 14 | 400 | 20 |
| Body Small | 12 | 400 | 16 |
| Label Large | 14 | 500 | 20 |
| Label Medium | 12 | 500 | 16 |
| Label Small | 11 | 500 | 16 |

Default font: **Roboto**. Brand fonts permitted at the same scale.

### Shape (corner radius)
- None: 0
- Extra small: 4
- Small: 8
- Medium: 12
- Large: 16
- Extra large: 28
- Full: 9999 (capsule / circle)

### Elevation (the dynamic-color variant)
Levels 0–5. Surface color shifts per level, with shadows used sparingly. Never
both heavy color shift and heavy shadow on the same surface.

## Component minimums

### Buttons
- **Filled, Tonal, Outlined, Text, Elevated** are the five canonical button kinds.
- Container height: **40dp** baseline. Padding: 24dp horizontal for text buttons,
  16dp + 8dp icon-then-text for icon+text.
- Hit target: **48×48dp** minimum (this is *larger* than the visible button —
  add an invisible padding layer for small visual buttons).
- Label: **Label Large** (14sp, 500 weight, all caps OR sentence case — pick one
  and be consistent across the product).

### Icon buttons
- Container: **40dp** standard, **32dp** compact, **48dp** large.
- Hit target: **48dp** minimum regardless of container.
- Required: tooltip OR aria-label (Material flags this explicitly).

### FABs
- Standard: 56dp container.
- Small: 40dp.
- Large: 96dp.

### Text fields
- Container height: **56dp** filled, **56dp** outlined.
- Inputs use a label that floats — placeholder-only is not a Material pattern.
- Helper / error text: 12sp, line height 16, 4dp margin top.

### App bars
- Top: **64dp** small, **112dp** medium, **152dp** large.
- Bottom: **80dp**.
- Title: Title Large (22sp).

### Navigation
- Bottom navigation: 80dp height, 3–5 destinations.
- Navigation rail: 80dp width.
- Navigation drawer: 360dp standard width on tablet+.

### Cards
- Corner radius: 12dp default.
- Elevation: level 1 baseline (0 for outlined cards).
- Padding: 16dp internal.

## Color system

Material 3 uses **dynamic color**: a seed color generates 5 tonal palettes
(primary, secondary, tertiary, neutral, neutral-variant), each with tones 0–100.

Token roles to know:
- `primary` / `on-primary`
- `primary-container` / `on-primary-container`
- `surface` / `on-surface`
- `surface-variant` / `on-surface-variant`
- `outline` / `outline-variant`
- `error` / `on-error` / `error-container` / `on-error-container`

Contrast: every `on-X` is required to meet **AA 4.5:1** against `X`. If a
custom color doesn't, the system isn't being followed.

## Motion

- Easing: **emphasized** (CubicBezier(0.2, 0, 0, 1)) for most UI motion.
- Duration: **200–300ms** for component-level changes; **400–500ms** for
  larger transitions. Anything over 500ms feels sluggish.

## What "in Material 3" means in a critique

A product that says it's on Material 3 should match on at least:
- 4dp spacing grid
- The five button kinds (or a justified subset)
- The shape scale (4 / 8 / 12 / 16 / 28)
- A coherent tonal color system, not arbitrary brand colors mapped to roles
- Type scale increments from the table above

Drift on any one of these is normal; drift on all of them means the product
isn't actually using Material 3, just citing it.

## Elevation tokens (levels 0–5)
The M3 elevation scale defines six levels by exact dp:

| Level | dp | Common components |
|-------|----|----|
| Level 0 | 0 | Outlined card, text button background |
| Level 1 | 1 | Elevated card, bottom sheet (collapsed) |
| Level 2 | 3 | Navigation bar (resting), search bar, menus |
| Level 3 | 6 | FAB (resting), modal bottom sheet, dialog |
| Level 4 | 8 | Hover/focus elevation for level-2 surfaces |
| Level 5 | 12 | Pressed/hover elevation for level-3 surfaces |

Source: https://m3.material.io/styles/elevation/tokens. M3 prefers a
**surface-color shift** at each level over heavy drop shadow — the
elevation tint is more important than the shadow on light themes, and
the shadow does most of the work on dark themes. Don't apply both a
heavy color shift *and* a heavy shadow to the same surface.

## Material 3 Expressive (2025+)

Google announced Material 3 Expressive at I/O 2025 as an extension of M3,
not a replacement. Expressive opts into a richer, more emotional surface
treatment while remaining compatible with the M3 token system. A product
on Expressive still satisfies M3 — but Expressive components have a
distinct shape, color, and motion vocabulary.

### Shape morphing & expanded shape library
Baseline M3 ships five named corner radii (`extra-small` 4, `small` 8,
`medium` 12, `large` 16, `extra-large` ~24–28dp — Compose Material 3
defaults the `extraLarge` shape to **24dp**; the m3.material.io spec
page also documents an `extra-large` variant at **28dp** for some
component contexts; treat 24–28 as the acceptable range for the
extra-large token).

Expressive adds two things on top:

1. **An expanded shape library** — "a new set of **35 shapes** to add
   decorative visual elements, with built-in shape morph motion."
   (Source: https://m3.material.io/blog/building-with-m3-expressive,
   "Expanded: Shape library".) These are decorative shape primitives
   (clovers, soft squares, lozenges, etc.), not a replacement for the
   five canonical corner-radius tokens.
2. **Shape morphing on interactive components** — "shapes now morph in
   response to interactions." Round button components like `IconButton`,
   `TextButton`, `IconToggleButton`, and `TextToggleButton` "support
   variations that animate when pressed or checked." `ButtonGroup`
   "implements an expressive group of buttons, in a row that
   shape-morphs when touched." (Source:
   https://m3.material.io/styles/shape/shape-morph and developer.android.com
   compose-material3 docs.)

Don't treat a non-baseline corner radius on an Expressive surface as
system drift without first checking whether the surface opted into
Expressive shape tokens. A button that morphs from a fully-rounded
circle to a rounded-square on press is a deliberate Expressive idiom,
not inconsistency.

### Expressive button heights
Material 3 Expressive introduces **five canonical button sizes** —
**Extra small, Small, Medium, Large, Extra large** — applied across
filled, tonal, outlined, and elevated styles. Source:
https://m3.material.io/components/split-button/overview ("Split buttons
are available in five sizes: Extra small, Small, Medium, Large, and
Extra large. They also come in four color styles: Elevated, Filled,
Tonal, and Outlined.") The same five-size vocabulary applies to the
main button family in Expressive.

Compose Material 3 exposes these via `ButtonDefaults`:
- `ExtraSmallContainerHeight`
- `SmallContainerHeight`
- `MediumContainerHeight`
- `LargeContainerHeight`
- `ExtraLargeContainerHeight`

The baseline 40dp button height in pre-Expressive M3 corresponds to
roughly the **Small** size in Expressive. The Extra-large size is the
new prominent action size.

`(author judgment)` Approximate Expressive container heights, inferred
from Compose Material 3 token files and visible spec renders (m3.material.io
does not consolidate these in a single table at this date):

- Extra small: ~32dp
- Small: ~40dp (matches baseline)
- Medium: ~56dp
- Large: ~80–96dp
- Extra large: ~120–136dp

Cross-check against `ButtonDefaults.*ContainerHeight` in the Compose
source before treating these as canonical. The harness should *not*
flag a 56dp button on an Expressive surface as "non-standard 40dp" —
that's the Medium variant.

### Extended FAB (Expressive update)
Per https://m3.material.io/components/extended-fab:

> "The extended FAB has three sizes: small (56dp), medium (80dp), and
> large (96dp), each with updated type styles. The baseline extended
> FAB (56dp) and Surface extended FAB are no longer recommended and
> should be replaced with the small extended FAB. Typography has also
> been adjusted to be larger."

Concrete sizes:
- Small extended FAB: **56dp** (replaces the deprecated baseline 56dp)
- Medium extended FAB: **80dp**
- Large extended FAB: **96dp**

Round FAB sizes (unchanged from baseline M3): default **56dp**, small
**40dp**, large **96dp**.

### New Expressive components
Per m3.material.io Expressive landing:
- **Split button** — pairs a primary button with a related-action menu
  trigger; uses Expressive shape and motion. Five sizes (XS / S / M /
  L / XL), four styles (Elevated, Filled, Tonal, Outlined).
- **Button group** — a horizontal row of buttons that shape-morphs on
  touch.
- **Loading indicator** — Expressive's reinterpreted progress indicator
  with a more expressive shape vocabulary.
- **Toolbar** (Expressive) — distinct from baseline M3 app bar.

### Expressive color schemes
Baseline M3 dynamic-color generates 5 tonal palettes from a seed.
Expressive adds **fixed accent slots** that stay consistent across
light and dark themes (per "What's new this year, so far" on
m3.material.io: "Fixed accents add a new set of colors that will remain
consistent across light and dark themes"). Use these for hero treatments
and emphasis surfaces where the brand identity should not flip with
theme. These slots **augment** `primary` / `secondary` / `tertiary`;
they don't replace them. The tertiary slot in Expressive often carries
more saturation than baseline M3 to enable the "emotional" surface
treatment.

### Expressive type scale
`(author judgment)` Expressive ships **emphasized variants** of the
baseline type roles (e.g., a heavier-weight `display-large-emphasized`
companion to `display-large`). The Compose `Typography` object exposes
these as separate text styles when on the Expressive theme. m3.material.io
does not consolidate the emphasized variants into a single token table at
this date — verify against Compose `MaterialTheme.typography` if the
exact size matters.

Baseline `Display Large` remains **57sp/64lh** (regular weight); the
emphasized variant uses a heavier weight at the same size. Weights
extend into 700+ range for emphasis roles where baseline M3 caps at 500.

### Touch targets in Expressive
The **48×48dp hit-target minimum is unchanged.** The visible container
can be smaller (a 40dp icon button is still M3-valid) but the touch
target must be padded to 48dp. Expressive's larger button sizes (Medium
and up) already exceed 48dp visually; for Extra small / Small, the
invisible padding is still required.

### Distinguishing Expressive from baseline drift
A finding that flags a "non-standard shape" or "non-baseline color"
should check whether the surface is opting into Expressive. Expressive ≠
system drift. A product that mixes baseline M3 components with
Expressive components is doing what the spec allows — call it out as a
deliberate mix, not as inconsistency, unless the mixing itself is
inconsistent across surfaces of the same kind.

Three Expressive signals to look for before flagging:
1. **Shape morph behavior** — buttons whose corner radius animates on
   press are Expressive, not custom.
2. **Saturated tertiary or fixed-accent colors** — colors that don't
   sit on the standard 5-palette tonal scale are Expressive's fixed
   accent slots, not arbitrary brand hex.
3. **Larger-than-40dp default button heights** — Medium / Large / Extra
   large Expressive sizes are deliberate, not drift.

### Predicates for harness
Concrete checks the design audit should run against captured UI:

1. **`shape_token_off_scale`** — flag any corner radius that is not in
   {0, 4, 8, 12, 16, 24, 28, full} **unless** the surface is identified
   as Expressive (via morph behavior, saturated tertiary, or component
   type — Split Button, Button Group, decorative shape from the 35-shape
   library).
2. **`elevation_color_and_shadow_double_dip`** — flag a surface whose
   tonal-elevation tint **and** shadow are both heavy. M3 picks one.
3. **`button_height_below_baseline`** — baseline-M3 buttons below 40dp
   are flagged. Expressive Extra small (~32dp) is exempt **only** when
   adjacent context confirms Expressive (see Expressive signals above).
4. **`hit_target_below_48dp`** — regardless of Expressive vs baseline,
   hit target must be ≥48dp. A visible 40dp button with no extra
   touch-padding fails.
5. **`extended_fab_deprecated_baseline_size`** — flag any 56dp extended
   FAB that is rendered with the old (deprecated) baseline-extended
   typography. Use the small-extended-FAB tokens instead.
6. **`tonal_color_off_palette`** — flag colors used in `primary` /
   `secondary` / `tertiary` roles that aren't generated from the 5
   tonal palettes. Expressive's fixed-accent slots are a documented
   exception when used in *accent* roles, not in the standard role
   slots.
7. **`text_field_label_in_placeholder`** — placeholder-as-label is not
   M3. Filled and outlined text fields must use a floating label.
8. **`mixed_expressive_and_baseline_inconsistently`** — flag when
   sibling components of the same kind on the same surface use
   different mode (one Expressive, one baseline). Mixing across
   surfaces is OK; mixing within a single surface group is not.
9. **`type_scale_off_increment`** — flag font sizes outside the M3 type
   scale (57/45/36/32/28/24/22/16/14/12/11sp) unless an Expressive
   emphasized variant explains the divergence.
10. **`button_label_casing_inconsistent`** — M3 allows ALL CAPS or
    sentence case for `Label Large`, but the product must pick one and
    use it consistently. Mixing within a product is a finding.

### Verification notes
- Sources fetched / queried (2026-05-23):
  - https://m3.material.io (Expressive landing) — for "Expanded: Shape
    library" 35-shape figure and shape-morph motion.
  - https://m3.material.io/components/extended-fab — for the explicit
    "small (56dp), medium (80dp), and large (96dp)" extended-FAB quote
    and the baseline-deprecation guidance.
  - https://m3.material.io/components/split-button/overview — for the
    five-size button vocabulary and four color styles.
  - https://m3.material.io/styles/shape/shape-morph — for the
    shape-morph-on-interaction rule on round button components.
  - https://m3.material.io/styles/elevation/tokens — for the level
    0–5 elevation dp values (1 / 3 / 6 / 8 / 12 surface tonal shift).
  - https://m3.material.io/blog/building-with-m3-expressive
  - https://m3.material.io/foundations/usability/applying-m-3-expressive
  - https://m3.material.io/styles/shape/shape-scale-tokens
  - https://m3.material.io/styles/typography/type-scale-tokens
  - https://developer.android.com/develop/ui/compose/designsystems/material3
    — Compose `Shapes` default values
    (`extraLarge = RoundedCornerShape(24.dp)`); also `ButtonDefaults`
    container-height tokens.
- Uncertainties marked `(author judgment)`:
  - Exact Expressive button container heights (XS / S / M / L / XL)
    are not consolidated in a single m3.material.io table at this
    date; the dp ranges given above are inferred from Compose
    Material 3 token files and visual reference renders. The harness
    should treat values as ranges, not exact thresholds, when
    determining Expressive adoption.
  - Exact `display-large-emphasized` weight / tracking values are not
    consolidated in a single m3.material.io table at this date.
  - The 24 vs 28 `extra-large` corner-radius discrepancy between
    Compose Material 3 (`24.dp`) and m3.material.io component specs
    (which document 28dp in some component contexts) is real and
    long-standing. Both should be accepted as in-spec.

## Sources
- Material 3 spec: https://m3.material.io
- Material 3 components: https://m3.material.io/components
- Material 3 foundations: https://m3.material.io/foundations
- Shape scale tokens: https://m3.material.io/styles/shape/shape-scale-tokens
- Shape morph: https://m3.material.io/styles/shape/shape-morph
- Elevation tokens: https://m3.material.io/styles/elevation/tokens
- Type scale tokens: https://m3.material.io/styles/typography/type-scale-tokens
- All buttons: https://m3.material.io/components/all-buttons
- Button specs: https://m3.material.io/components/buttons/specs
- Extended FAB: https://m3.material.io/components/extended-fab
- Split button: https://m3.material.io/components/split-button/overview
- Cards specs: https://m3.material.io/components/cards/specs
- Material 3 Expressive: https://m3.material.io/blog/building-with-m3-expressive
- Applying M3 Expressive: https://m3.material.io/foundations/usability/applying-m-3-expressive
- Compose Material 3 (Android Developers):
  https://developer.android.com/develop/ui/compose/designsystems/material3
