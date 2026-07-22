---
source: internal
last_verified: 2026-05-23
---

# System archetypes

Five working stances a design system can take. Pick one before naming
tokens. The archetype determines the priors for density, contrast,
saturation, motion, and component sizing.

These aren't tribes; products legitimately blend them. But the *primary*
stance has to be one of these or the system doesn't have a point of view.

---

## 1. Utilitarian

**Lineage**: Craigslist, Hacker News, GOV.UK, the IRS website at its best.

**Stance**: the page is the thing. Chrome, branding, and decoration are
deliberately minimal because they cost the user attention they need for
the content. Visual design is in service of legibility and information
density, not impression.

**Priors**:
- Body text: 16–18px on white. Type contrast 7:1+, not 4.5:1.
- Color palette: 1 primary action color, neutrals, and semantic. No
  decorative accents.
- Type scale: 4–5 sizes max. Often just `body`, `h2`, `h1`.
- Radii: 0px or 2px. Nothing more.
- Borders, not shadows. 1px hairlines for separation.
- No motion beyond browser-default.

**Right for**: government services, reference material, utility apps,
information density tools, tools-for-experts where polish reads as
condescension.

**Wrong for**: anything where users choose your product over a competitor
based on feel.

**Tells of a successful utilitarian system**: a screenshot is hard to
mistake for "an app from 2012" because it's actually correct, not dated.
GOV.UK looks the same in 2025 as in 2014 because it didn't have a trend
to chase.

---

## 2. Dense / power-user

**Lineage**: Linear, Jira (when it's good), Bloomberg Terminal, Figma's
inspector, Sublime Text, Tana.

**Stance**: the user is an expert who works in this product daily. Time
spent learning the layout pays back in years of productivity. Information
density is a feature; whitespace is a tax.

**Priors**:
- Body text: 13–14px. Smaller than the web standard, intentionally.
- Type contrast: tight scale. The jump from `body` to `h2` might be just
  +2px and a weight change. Hierarchy is built more with weight and
  position than with size.
- Color: heavy use of subtle text colors (text.subtle, text.subtlest)
  for secondary information. Sparing accents.
- Spacing: 4px grid. Increments of 4, 8, 12. Section gaps at 16, not 32.
- Component sizing: 28–32px buttons, 32px inputs, 28px row heights.
- Keyboard shortcuts for everything frequent. Mouse-only is failure.
- Motion: 100–150ms. Nothing slower; the user doesn't have time.

**Right for**: project management, code editors, financial tools,
dashboards, anything used 4+ hours a day.

**Wrong for**: marketing sites, onboarding flows, anything used by
casual users.

**Tells**: Linear's `↑` arrow on its priority cells, the precise 32px
row in Jira's queue view, Figma's right panel cramming 14 controls into
240px. Every pixel is doing work.

---

## 3. Clarity-first

**Lineage**: Stripe, Notion, Apple's product pages, Vercel docs, Linear's
marketing site (different from the app), modern Shopify admin.

**Stance**: every element has room. The user shouldn't have to *parse* the
page; the eye should land where it needs to without effort.

**Priors**:
- Body text: 16px standard, sometimes 17–18px for marketing.
- Type contrast: wide scale. Big jumps between body and headings (1.333+
  ratio). Display sizes used liberally.
- Color: muted neutrals, one strong primary, semantic colors used
  sparingly and decisively.
- Spacing: 8px grid. Section rhythm at 48, 64, 96px.
- Component sizing: 40px buttons (with 44px hit target), 40–44px inputs,
  generous internal padding.
- Borders: light, often optional. Cards sit on subtle surfaces, not
  hard-bordered.
- Motion: 200–300ms ease-out. Visible enough to feel intentional.

**Right for**: developer tools where adoption depends on first impression,
fintech, productivity tools targeting both casual and power users,
documentation.

**Wrong for**: high-density information work, expert-only tools, anything
where the user spends 6+ hours daily and resents the whitespace.

**Tells**: Stripe's documentation cards with no visible borders, Notion's
empty-state generosity, Apple's gigantic h1 on product pages. The product
is confident enough to give content room.

---

## 4. Brand-forward

**Lineage**: Vercel (post-rebrand), Arc browser, Linear's marketing,
Framer, Posthog, mid-2020s YC-funded landing pages.

**Stance**: the brand has a strong visual identity, and the product is
an extension of that identity. Color, typography, and motion serve the
brand's voice as much as they serve usability.

**Priors**:
- Body text: 14–16px in a distinctive (often custom) typeface.
- Strong primary color, often non-default (Vercel's pure black, Arc's
  shifting gradients, Posthog's hedgehog-orange). Used assertively, not
  apologetically.
- Spacing: usually 8px grid, but with deliberate departures for hero
  moments.
- Distinct shape language: either consistently sharp (0–4px radius),
  consistently soft (12px+), or boldly mixed for character.
- Custom motion: the product has a "feel" — Arc's elastic transitions,
  Vercel's instant snap, Linear's tight cubic-bezier easings.
- Often dark-mode-first or dark-mode-equal.

**Right for**: products competing on identity (developer tools, design
tools, consumer software), companies with strong design teams, anything
where "looks generic" is the product's actual death sentence.

**Wrong for**: products where the brand is incidental (B2B reporting,
internal tools, government), products with users who don't choose the
product (employer-mandated software).

**Tells**: a screenshot is recognizable as that product even with the
logo cropped out. That's the goal of this archetype.

---

## 5. Editorial

**Lineage**: New York Times, Substack, Medium, MIT Tech Review, the New
Yorker's web reader, Are.na.

**Stance**: the content is long-form prose or curated visual material.
The interface frames the content; it doesn't compete with it. Reading
comfort over months of daily use is the goal.

**Priors**:
- Body text: 18–20px in a serif typeface, optimized line length
  (60–75 characters). Line height 1.5–1.7.
- Type scale: extended, often with display sizes for article titles
  (40–60px+).
- Color: warm whites, near-blacks, rare accent. Color is for marking
  links and the occasional pull-quote.
- Spacing: vertical rhythm tuned to the body line-height. Headings sit
  in negative space proportional to their size.
- Borders rare; whitespace and typographic hierarchy do the work.
- No motion in reading view. Motion only at navigation/UI level.

**Right for**: long-form publications, knowledge bases, documentation
that prioritizes reading time over scanning, archive interfaces.

**Wrong for**: anything transactional, dashboards, tools that require
frequent UI interaction.

**Tells**: the article view feels like the chrome receded entirely. The
NYT article view has a navigation bar that effectively disappears once
you scroll past the headline.

---

## How to pick one

Ask the user: **"In one sentence, what's the user doing in this product
and how often?"**

- "Filing one document a year" → utilitarian
- "Working in this 6 hours a day" → dense
- "Visiting weekly to perform a known task" → clarity-first
- "Choosing this product over a competitor partly on feel" → brand-forward
- "Reading for 20+ minutes per session" → editorial

If the answer combines two ("they're choosing it on feel AND working in
it 6 hours a day"), pick the one that matters when the answers conflict.
A dense system can have brand-forward marketing pages that link to the
product; a brand-forward product *can't* have a dense main interface
without breaking its own promise.

## How archetypes interact with the existing reference systems

- Material 3 = clarity-first by default, easily nudged to brand-forward.
- Apple HIG = clarity-first on Mac, slightly denser on iOS.
- Fluent 2 = clarity-first to dense (varies by surface).
- Atlassian = dense, unambiguously.
- Carbon = dense to clarity-first, leans utilitarian for enterprise.
- Polaris = clarity-first.

If the user's archetype matches one of these, that's a strong signal to
adopt that system rather than mint a new one. See `skills/system-design/SKILL.md`
section "When to *not* propose a custom system".

## Sources
- Internal taxonomy distilled from observation of the named lineage
  products (Linear, Stripe, GOV.UK, Vercel, New York Times, etc.). Not
  drawn from a single external reference. Treat as opinionated working
  vocabulary, not as a citable framework.
