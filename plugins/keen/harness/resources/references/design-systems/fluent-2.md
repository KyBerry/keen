---
version: "Fluent 2 baseline"
source: https://fluent2.microsoft.design
last_verified: 2026-05-23
---

# Fluent 2 (Microsoft)

Microsoft's design system, used across Windows, Microsoft 365, Teams, Edge,
GitHub UI for the Microsoft side, and Copilot surfaces. Fluent 2 is denser
than Material — especially for productivity apps.

## Tokens

### Spacing
- Base grid: **4px** (referred to as `sizeXX` tokens in Fluent).
- Common increments: 4, 8, 12, 16, 20, 24, 32, 40, 48.
- "Compact" density is a first-class option — don't critique a 32px button
  for being too small if the surface is explicitly using compact density.

### Type
| Role | Size | Line height | Weight |
|------|------|-------------|--------|
| Caption 2 | 10 | 14 | 400 |
| Caption 1 | 12 | 16 | 400 |
| Body 1 | 14 | 20 | 400 |
| Body 1 Strong | 14 | 20 | 600 |
| Body 2 | 16 | 22 | 400 |
| Subtitle 2 | 16 | 22 | 600 |
| Subtitle 1 | 20 | 28 | 600 |
| Title 3 | 24 | 32 | 600 |
| Title 2 | 28 | 36 | 600 |
| Title 1 | 32 | 40 | 700 |
| Large Title | 40 | 52 | 700 |
| Display | 68 | 92 | 700 |

Default font: **Segoe UI Variable** (Windows / Microsoft 365); the variable
font supports optical sizing (Display vs. Text). On the web, fallbacks chain
to Segoe UI → -apple-system → system-ui.

### Shape
Border radius:
- None: 0
- Small: 2
- Medium: 4
- Large: 6
- X-Large: 8
- Circular: 9999

Note Fluent's radii are **smaller** than Material/Apple. A 12px-radius card on
Fluent reads as "not Fluent."

## Component minimums

### Hit targets
- **32×32px** standard. Compact density goes as low as **24×24px**.
- Below that is out-of-system.
- Touch surfaces (Surface devices, dual-mode): bump to **44×44px**.

### Buttons
- Sizes: small (24), medium (32), large (40).
- Shapes: rounded (default), circular, square.
- Appearance: primary, secondary (default), outline, subtle, transparent.
- Padding: 16px horizontal for medium, 12px for small, 20px for large.

### Text inputs (Input)
- Sizes: small (24), medium (32), large (40).
- Border treatment: outline (default), underline (denser), filled-darker,
  filled-lighter.
- Always paired with a Label component above (or to the side, declared explicitly).

### Dialogs
- Width: 420px default for prompts, 600px for content.
- Padding: 24px.
- Corner radius: 8px (X-Large).

### Navigation
- Top nav (Microsoft 365 header): 48px.
- Side nav: 48px collapsed, 280px expanded.
- Tab list: 44px height.

## Color

Fluent uses semantic tokens with explicit theme variants (Web Light, Web Dark,
Teams Light, Teams Dark, Teams High Contrast). Tokens to know:

- `colorNeutralForeground1` / `2` / `3` / `4`
- `colorNeutralBackground1` / `2` / `3` / `4` / `5` / `6`
- `colorNeutralStroke1` / `2`
- `colorBrandForeground1` / `2`
- `colorBrandBackground` / `colorBrandBackground2` / `colorBrandBackgroundHover`
- `colorStatusDanger*`, `colorStatusWarning*`, `colorStatusSuccess*`

High Contrast theme is a first-class requirement — any product claiming
Fluent that breaks under Windows High Contrast is non-compliant.

## What "in Fluent" means in a critique

Match on at least:
- 4px spacing grid
- Corner radius from {0, 2, 4, 6, 8} — anything else is out-of-system
- Type from the table; Segoe UI Variable or fallback chain
- Density mode declared and used consistently
- Theme tokens, not raw hex
- High Contrast support (testable via `forced-colors: active`)

The most common Fluent failure is a product that uses Material-y radii (12px+)
or Material-y density (48px touch targets, generous padding) while claiming
Fluent. The systems have different opinions about density on purpose — be
explicit about which one you're auditing against.

## Sources
- Fluent 2 design system: https://fluent2.microsoft.design
