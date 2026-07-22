---
version: "Carbon v11 baseline"
source: https://carbondesignsystem.com
last_verified: 2026-05-23
---

# IBM Carbon

Carbon is enterprise-data-shaped — built for products where users spend hours,
information density matters more than visual delight, and accessibility is
load-bearing. If you're auditing observability dashboards, analytics tools,
financial software, or government applications, Carbon is often the reference
even when the product doesn't claim it.

## Tokens

### Spacing
- Base grid: **8px** (Carbon uses an 8px Mini-Unit, with a 2px sub-grid).
- Spacing scale: `spacing-01` (2), `02` (4), `03` (8), `04` (12), `05` (16),
  `06` (24), `07` (32), `08` (40), `09` (48), `10` (64), `11` (80), `12` (96),
  `13` (160).

### Type — IBM Plex Sans
| Role | Size | Line height | Weight |
|------|------|-------------|--------|
| Caption 01 | 12 | 16 | 400 |
| Caption 02 | 14 | 18 | 400 |
| Label 01 | 12 | 16 | 400 |
| Helper Text 01 | 12 | 16 | 400 |
| Body Compact 01 | 14 | 18 | 400 |
| Body Compact 02 | 16 | 22 | 400 |
| Body 01 | 14 | 20 | 400 |
| Body 02 | 16 | 24 | 400 |
| Heading Compact 01 | 14 | 18 | 600 |
| Heading 01 | 14 | 20 | 600 |
| Heading 02 | 16 | 24 | 600 |
| Heading 03 | 20 | 28 | 400 |
| Heading 04 | 28 | 36 | 400 |
| Heading 05 | 32 | 40 | 400 |
| Heading 06 | 42 | 50 | 300 |
| Heading 07 | 54 | 64 | 300 |

Default font: **IBM Plex Sans** (and IBM Plex Mono for code). Plex is
strongly recommended; Carbon visuals look notably off without it.

### Shape
Carbon prefers **square corners**. Radii are sparse:
- `border-radius`: 0 (default), 4 (small), 8 (medium).
- Cards, inputs, buttons are typically 0 or 4. 8 is for marketing/onboarding.

The square corners are the single biggest visual signal of "this is Carbon."

## Component minimums

### Hit targets
- **40×40px** standard (Carbon's "field height" is 40).
- 32×32 acceptable in compact density.
- 48×48 in expressive / large density.

### Buttons
- Sizes: small (32), medium (40), large (48), expressive XL (64), expressive 2XL (80).
- Variants: primary, secondary, tertiary, ghost, danger, danger-tertiary, danger-ghost.
- Carbon emphasizes **square corners** and tight horizontal padding (16px).
- Primary actions are filled; everything else is outlined or text.

### Text inputs
- Heights match button sizes: 32 / 40 / 48.
- **Underline-style inputs** are the Carbon look (border-bottom only by default
  for the "lighter" variant). The full-bordered version exists too.
- Always include a Label above (Label 01) and a HelperText below.
- Validation: red border + helper-text + icon. All three.

### Data tables
Carbon's most differentiated component:
- Row heights: compact (32), short (40), medium (48), tall (64), extra-tall (80).
- Sticky header by default for scrolling tables.
- Inline actions (icon buttons) at the row end; bulk actions in a toolbar above.
- **Striped rows are deprecated** in Carbon's current guidance; row separators
  via border are preferred.

### Tile / Card
- Padding: 16px (spacing-05) default.
- Corner radius: 0 (or 4 for "expressive" variant).
- Click target: the entire tile, not a sub-button, when the tile represents an
  actionable item.

### Notifications
- Inline (within a page): full-width, colored left border.
- Toast: top-right, dismissible, auto-dismiss for non-critical.
- Actionable: must include a button to take action; if there's no action, use
  inline.

## Color

Carbon's color tokens are theme-aware. Themes:
- White (light, default)
- Gray 10 (light, secondary background)
- Gray 90 (dark)
- Gray 100 (dark, deeper)

Token roles:
- `background`, `layer-01` / `02` / `03`, `layer-accent-01`...
- `text-primary` / `text-secondary` / `text-helper` / `text-on-color` / `text-error`
- `border-subtle-01` / `02` / `03`, `border-strong`, `border-interactive`
- `support-error` / `support-success` / `support-warning` / `support-info`
- `interactive` / `link-primary` / `link-secondary` / `focus`

The focus token is special — Carbon prescribes a **2px solid outline + 1px
inner offset** for focus, which is more aggressive than most systems and
intentional given the keyboard-heavy workflows Carbon targets.

## What "in Carbon" means in a critique

Match on at least:
- 8px spacing grid via `spacing-*` tokens
- IBM Plex Sans (not Helvetica or Inter)
- Type from the table — particularly the heavy use of 14/18 and 14/20 body
- Square or 4px-radius corners
- Underlined input style or the tighter outlined variant
- Dense data tables, not card-heavy layouts
- Strong, visible focus rings (2px + offset)

Carbon is most often miscredited: products that are actually using IBM Plex +
square corners but free-form spacing. The font and shape cues read as Carbon,
but the spacing reveals an ad-hoc system. Worth flagging when you see it.

## Sources
- Carbon design system: https://carbondesignsystem.com
