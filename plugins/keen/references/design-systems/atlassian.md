---
version: "Atlassian Design System baseline"
source: https://atlassian.design
last_verified: 2026-05-23
---

# Atlassian Design System (ADS)

Used across Jira, Confluence, Bitbucket, Trello, and Atlassian's developer
products. Document-and-collaboration first; dense by default. When you cite
this, name the section and the specific value.

## Tokens

### Spacing
- Base grid: **4px**.
- Named scale: `space.025` (2), `.050` (4), `.075` (6), `.100` (8), `.150` (12),
  `.200` (16), `.250` (20), `.300` (24), `.400` (32), `.500` (40), `.600` (48),
  `.800` (64), `1000` (80).
- Layouts compose in multiples of 8 at the section level, 4 at the component level.

### Type scale (px)
| Role | Size | Weight | Line height |
|------|------|--------|-------------|
| Heading XXLarge | 35 | 500 | 40 |
| Heading XLarge | 29 | 500 | 32 |
| Heading Large | 24 | 500 | 28 |
| Heading Medium | 20 | 500 | 24 |
| Heading Small | 16 | 600 | 20 |
| Heading XSmall | 14 | 600 | 16 |
| Heading XXSmall | 12 | 600 | 16 |
| Body Large | 16 | 400 | 24 |
| Body Medium | 14 | 400 | 20 |
| Body Small | 12 | 400 | 16 |
| UI (default) | 14 | 400 | 16 |

Default font: **Charlie Sans** (Atlassian brand) with **Inter** as the open-source
fallback. Code: **Fira Code** or system mono.

### Shape (corner radius)
- `border.radius.050`: **3px** (small inputs, tags)
- `border.radius`: **4px** (default — buttons, cards, most surfaces)
- `border.radius.100`: **8px** (modals, large cards, sheets)
- `border.radius.200`: **16px** (rare — only large feature surfaces)
- `border.radius.circle`: 9999

### Elevation
- `shadow.raised`: subtle, for cards lifted off the page.
- `shadow.overlay`: stronger, for popovers, dropdowns, dialogs.
- ADS leans on **borders** more than shadows. A 1px `color.border` is the
  default separator, not a drop shadow.

## Component minimums

### Buttons
- Heights: **default 32px**, compact 24px, large 40px.
- Hit target: **40×40px** minimum (ADS uses an invisible padding shim for the
  smaller visual heights).
- Padding: 12px horizontal default; 8px for compact.
- Icon buttons: **24px** visual / 32px container / **40px** hit target.

### Form inputs
- Default height: **40px**. Compact: 32px.
- Border: 2px on focus, 1px otherwise (ADS uses a thicker focus ring than most
  systems — this is intentional).
- Label sits **above** the input. Inline labels are non-standard.
- Help text and error text use 12px body small.

### Tables (extremely load-bearing in Jira/Confluence)
- Row height: **40px** comfortable, **32px** compact, **48px** spacious.
- Cell padding: 8px vertical, 12px horizontal.
- Sortable headers underline on hover, show a chevron when active.
- Striped rows are non-default in modern ADS — borders only.

### Modals / Dialogs
- Width: **small 400**, **medium 600**, **large 800**, **x-large 968**.
- Padding: 24px on all sides.
- Header: 20px Heading Medium, 24px below for body start.
- Action row: 24px above, right-aligned, primary button rightmost.

### Tags / Lozenges
- Height: **20px** standard, **24px** large.
- Border radius: 3px.
- Padding: 4px horizontal.
- Font: 12px, 600 weight, often uppercase for status (DONE, IN PROGRESS).

### Tabs
- Height: **40px**. Selected tab: 2px bottom border in `color.border.selected`.
- Inactive tabs use `color.text.subtle`, not full strength text.

## Color system

ADS uses **semantic tokens exclusively** in modern code; raw hex is a smell.

Foundational categories:
- `color.background.*`: `neutral`, `neutral.subtle`, `neutral.hovered`, `selected`,
  `accent.{red,blue,green,yellow,purple,teal,magenta,orange,lime}.subtle/.bolder`.
- `color.text.*`: `default`, `subtle`, `subtlest`, `disabled`, `inverse`,
  `accent.{color}`.
- `color.border.*`: `default`, `bold`, `selected`, `focused`, `disabled`.
- `color.icon.*`: same shape as text.

Status colors (these are *opinionated* in ADS):
- **Success**: green
- **Information**: blue (also "in progress")
- **Warning**: yellow
- **Danger / Removed**: red
- **Discovery / New**: purple
- **Moved**: orange

A warning rendered in red, or a success rendered in blue, is a violation of
ADS even if the contrast is fine.

Contrast: AA 4.5:1 minimum for body text, 3:1 for large text and UI components.
ADS publishes "accessible" variants of each accent (e.g.
`color.text.accent.red` is darker than the brand red specifically so body text
clears 4.5:1).

## Motion

- Standard duration: **200ms** for most transitions, **350ms** for entrances.
- Easing: standard `cubic-bezier(0.15, 1, 0.3, 1)` (an "out-quint" feel).
- Modals fade + 8px translate-up on enter.

## Density

ADS ships explicit **comfortable / compact / spacious** modes. Jira's defaults
to compact for table-heavy views; Confluence to comfortable for reading. A
product claiming ADS should declare which density it's using and stay
consistent — mixed densities on one page is a tell.

## What "in Atlassian Design System" means in a critique

A product that says it's on ADS should match on at least:
- 4px spacing grid, with 8px section rhythm
- Default 32px button height / 40px input height
- Lozenge-style status tags with the official color mapping (success=green,
  warning=yellow, danger=red, info=blue, discovery=purple, moved=orange)
- 1px borders as the default separator, not shadows
- Charlie Sans (or Inter as fallback) at 14px UI default
- Semantic color tokens — no raw hex in component code

Drift on any one is normal. Drift on three or more — especially the status
color mapping or the border-vs-shadow choice — and the product is doing
"Jira-flavored bespoke" rather than ADS.

## Sources
- Atlassian Design System: https://atlassian.design
