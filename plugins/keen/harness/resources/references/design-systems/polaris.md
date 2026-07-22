---
version: "Polaris baseline"
source: https://polaris.shopify.com
last_verified: 2026-05-23
---

# Shopify Polaris

Polaris is admin-app-shaped — designed for merchants doing focused work, not
consumer-facing surfaces. Density and information clarity are primary; brand
flourish is rare. If you're auditing a B2B admin tool, Polaris is often the
most relevant reference even if the product isn't on Shopify.

## Tokens

### Spacing
- Base grid: **4px**.
- Spacing scale (named): `space-025` (1px), `space-050` (2), `space-100` (4),
  `space-150` (6), `space-200` (8), `space-300` (12), `space-400` (16),
  `space-500` (20), `space-600` (24), `space-800` (32), `space-1000` (40),
  `space-1200` (48), `space-1600` (64), `space-2000` (80), `space-2400` (96),
  `space-2800` (112), `space-3200` (128).

### Type
| Role | Size | Line height | Weight |
|------|------|-------------|--------|
| Heading XS | 11 | 16 | 600 |
| Heading SM | 12 | 16 | 600 |
| Heading MD | 13 | 20 | 600 |
| Heading LG | 14 | 20 | 600 |
| Heading XL | 16 | 24 | 600 |
| Heading 2XL | 20 | 28 | 700 |
| Heading 3XL | 24 | 32 | 700 |
| Heading 4XL | 28 | 36 | 700 |
| Body XS | 11 | 16 | 400 |
| Body SM | 12 | 16 | 400 |
| Body MD | 13 | 20 | 400 |
| Body LG | 14 | 20 | 400 |

Default font: **Inter** (Polaris ships its own variable build; system fallback
is `-apple-system`, `system-ui`).

### Shape
- Radius scale: `border-radius-100` (4), `200` (6), `300` (8), `400` (12).
- Most surfaces use 200 (6px) or 300 (8px). 12px is reserved for hero
  surfaces and modals.

## Component minimums

### Hit targets
- Polaris targets **44×44px** as the practical minimum for touch and is fine
  with smaller pointer-only controls (28–32px) inside dense data tables.

### Buttons
- Variants: `primary`, `secondary` (default), `tertiary`, `plain`,
  `monochromePlain`, `primary` with `tone="critical"` for destructive.
- Sizes: `micro` (20), `slim` (28), `medium` (36 — default), `large` (44).
- Padding: 12px horizontal default, 16px for large.
- Use `outline` only sparingly — Polaris prefers tone over outline for
  hierarchy.

### TextField
- Default height: 36px.
- Always paired with a label above (label-side variant exists but discouraged).
- Helper text: Body SM, neutral subdued color.
- Error text: Body SM, red, with an error icon — both, not just one.

### Cards
- Default padding: 16px.
- Corner radius: 8px.
- One level of elevation via subtle shadow; multiple stacked elevations is
  discouraged.

### IndexTable / DataTable
Polaris's bread-and-butter component. Rules to know:

- First column is typically a checkbox + identifier; the identifier is the
  primary action target, not a separate "view" button.
- Bulk actions appear in a sticky bar above the table when items are selected.
- Sorting affordances on column headers (arrow icons), not sort menus.
- Pagination: page-based for short lists, cursor-based for long ones — Polaris
  has both, choose by data shape.

### Page
The `Page` component is the layout primitive — title, breadcrumb, primary
action, secondary actions, then `Layout` with `Layout.Section` columns. A
Polaris-aligned admin should always have:
- A clear page title (Heading 2XL or 3XL).
- A breadcrumb when not at top level.
- Primary action top-right, secondary actions in a `...more` menu when there
  are >2.

## Color

Polaris uses semantic tone tokens, not raw colors. Roles:
- `text` / `text-emphasis` / `text-secondary` / `text-disabled`
- `bg-surface` / `bg-surface-secondary` / `bg-surface-tertiary` / `bg-surface-success` / `bg-surface-warning` / `bg-surface-critical`
- `border` / `border-secondary` / `border-disabled`
- `icon` / `icon-secondary` / `icon-success` / `icon-warning` / `icon-critical`
- `bg-fill-brand` / `bg-fill-brand-hover` / `bg-fill-brand-active`

Critical / warning / success tones are sparing. A Polaris UI that uses red
for emphasis (not error) is wrong.

## What "in Polaris" means in a critique

Match on at least:
- 4px spacing grid via `space-*` tokens
- Type from the table; Inter or a justified fallback
- Buttons use tone (not outline) for hierarchy
- Critical/warning colors reserved for actual problems
- Page-level structure: title, breadcrumb, primary action top-right
- Tables are IndexTable/DataTable shape — sortable headers, sticky bulk-action
  bar, page-shaped pagination

Polaris is the design system most likely to flag a product as "trying too
hard." Excessive color, gradient hero areas, illustrative empty states larger
than necessary — all out-of-system. Polaris likes a calm, dense, scannable
admin surface.

## Sources
- Polaris design system: https://polaris.shopify.com
