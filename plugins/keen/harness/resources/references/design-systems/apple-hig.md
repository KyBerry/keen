---
version: "iOS 26 / iPadOS 26 / macOS 26 baseline"
source: https://developer.apple.com/design/human-interface-guidelines
last_verified: 2026-05-23
---

# Apple Human Interface Guidelines

Condensed reference for iOS, iPadOS, and macOS surfaces. Cite by platform —
HIG rules differ between iOS and macOS more than people remember.

## Tokens

### Spacing
- Standard increment: **8pt** (also 4pt where tighter rhythm is needed).
- Layout margins: 16pt on iPhone (compact), 20pt on iPad regular width.

### Type (iOS — Dynamic Type)
| Style | Default size (Large) | Weight |
|-------|---------------------|--------|
| Large Title | 34 | Regular |
| Title 1 | 28 | Regular |
| Title 2 | 22 | Regular |
| Title 3 | 20 | Regular |
| Headline | 17 | Semibold |
| Body | 17 | Regular |
| Callout | 16 | Regular |
| Subheadline | 15 | Regular |
| Footnote | 13 | Regular |
| Caption 1 | 12 | Regular |
| Caption 2 | 11 | Regular |

System font: **SF Pro** (Text and Display optical variants below/above 20pt).

Dynamic Type means these sizes scale with user preference. Hardcoding pixel
values for body copy is anti-HIG.

### Shape (corner radius)
HIG prefers continuous (squircle) corners over circular. Common values:
- 6, 8, 10, 12 (controls)
- 14, 16, 20 (cards & sheets)
- 12 on iOS app icon mask is **not** to be reused — that's icon-specific.

## Component minimums

### Hit targets
- **44×44pt** absolute minimum on iOS. Smaller is non-compliant.
- macOS is more permissive (mouse precision); 28×28pt is acceptable for
  controls in chrome but 44×44 still preferred for primary surfaces.

### Buttons (iOS)
Three primary styles: **Plain**, **Gray** (tinted), **Filled**.
- Height: 44pt minimum (same as hit target).
- Corner radius: 12pt (rounded), 22pt (capsule), or rectangular for inline
  glyph buttons.
- Label: Headline (17pt semibold) for primary actions; Body for secondary.

### Buttons (macOS)
- Default push button: 28pt height in regular control size.
- Bordered buttons in toolbars: 28pt.
- Borderless / link buttons: type-led, no fixed height.

### Text fields
- iOS height: 44pt typical (taller for multi-line).
- macOS: 22pt regular, 19pt small, 16pt mini.
- Use a label outside the field for forms; placeholder copy is for hints, not
  labels (HIG calls this out specifically).

### Navigation bars
- iOS standard: 44pt content + 20pt status area = 64pt total (compact).
- iOS large title: 96pt height, scrolls into the standard bar on scroll.
- macOS toolbar: 52pt unified, 38pt compact.

### Tab bars
- iOS bottom tab bar: 49pt + safe area inset.
- 2–5 tabs. More than 5 means use a "More" tab.

### Lists & tables (iOS)
- Plain row: 44pt minimum.
- Subtitle row: 56pt.
- Inset grouped: 16pt margins on each side.

## Color

Adaptive colors are the system. Use the semantic palette:
- `systemBackground`, `secondarySystemBackground`, `tertiarySystemBackground`
- `label`, `secondaryLabel`, `tertiaryLabel`, `quaternaryLabel`
- `systemFill`, `secondarySystemFill`, etc. (4 levels)
- `systemBlue`, `systemRed`, etc. (12 system colors)

Hardcoded hex values that don't track Dark Mode are anti-HIG.

Contrast: HIG defers to WCAG AA (4.5:1 normal, 3:1 large). Apple's adaptive
colors meet this by design when used correctly.

## Motion

- Default easing: **standard ease** (~CubicBezier(0.42, 0, 0.58, 1)).
- Spring animations are first-class on iOS — UIKit/SwiftUI prefer them over
  duration-based curves for interactive elements.
- Reduce Motion (accessibility): respect `prefers-reduced-motion`. Don't just
  shorten durations — replace large translations with cross-fades.

## Accessibility (HIG-specific)

- VoiceOver labels: every interactive element. Match the visible label or
  describe the action ("Send", not "Button").
- Dynamic Type support: required for any text that isn't a brand wordmark.
- Switch Control & Voice Control: tab order must match visual order.

## What "in HIG" means in a critique

A product that claims to be HIG-aligned should match on at least:
- 44pt minimum hit targets
- Adaptive (Dark Mode-aware) colors via semantic tokens
- SF Pro or a justified brand replacement at the standard sizes
- Continuous corners on rounded surfaces
- Native feel for navigation chrome (back button placement, swipe-back)

The most common failure: a web app that copies HIG visuals but skips the
behavior — no Dynamic Type, no Dark Mode, hit targets sized at 32pt because
"it looks tighter." That's HIG cosplay, not HIG alignment.

## Materials & depth (iOS/iPadOS/macOS 26+)

Apple's translucent materials system was overhauled in iOS / iPadOS /
macOS 26 (2025). The new system is called **Liquid Glass**. It replaces
the prior visual-effect-view + `UIBlurEffect` approach for system surfaces
(nav bars, tab bars, sidebars, popovers, sheets). If a product claims to
be iOS 26-aligned, the chrome materials should be Liquid Glass, not the
older `.systemThinMaterial` / `.regularMaterial` patterns rendered through
the legacy blur stack.

### Material variants
Liquid Glass ships with **two variants**, per WWDC25 session 219 "Meet
Liquid Glass":

- **Regular** — the default, versatile, primary variant. Adapts to any
  size, over any content; "anything can be placed on top of it." Receives
  adaptive light/dark behavior for symbols and glyphs automatically.
  Use this for ~all chrome.
- **Clear** — permanently more transparent; does **not** have adaptive
  light/dark behavior. Use Clear only when **all three** of these
  conditions hold (per Apple): (1) the element is over media-rich
  content, (2) introducing a dimming layer below won't hurt the content
  layer, and (3) the content above is bold and bright.

> "Variants should never be mixed." — WWDC25 219. If you see a product
> mixing Clear and Regular Liquid Glass within the same surface stack,
> that's an authoring error, not a creative choice.

Note: the legacy `.regularMaterial` / `.thinMaterial` / `.ultraThinMaterial`
SwiftUI material constants still exist but represent the iOS 15–18
material stack, not Liquid Glass. Don't conflate the two.

### Vibrancy & adaptive behavior
Small Liquid Glass elements (navbars, tabbars, toolbar buttons)
"constantly adapt their appearance depending on what's behind them. They
also flip from light to dark based on the background." Larger elements
(menus, sidebars) adapt their tint but **don't** flip light/dark —
transitions at that surface area would be distracting. (Source:
WWDC25 219.)

Foreground content on glass uses **vibrant text/symbol colors** that
SwiftUI adapts automatically to maintain legibility against whatever's
beneath. This only works when foreground colors come from the **system
palette** (`label`, `secondaryLabel`, `systemBlue`, etc.). A custom RGB
hex punched through glass will not vibrancy-shift — it'll just blur and
lose contrast. Pair glass with semantic system colors, not brand hex
values.

Shadow behavior on Liquid Glass elements is also adaptive: the system
"increases the opacity of its shadow when it is over text" and "lowers
the opacity of its shadow when it is over a solid light background."
(Source: WWDC25 219.) Don't override this with a custom `.shadow()`
unless you can justify it — you're fighting the material.

### Tinting
Apple's rule is unambiguous: tint Liquid Glass elements **selectively,
for primary actions only**. "When every element is tinted, nothing
stands out, and it can be confusing. If you want to imbue color into
your app, do it in the content layer instead." (Source: WWDC25 219.)
A toolbar where every button is tinted is anti-HIG on iOS 26.

### Core API surface (SwiftUI)
- **`glassEffect(_:in:)`** — applies a Liquid Glass material to a custom
  view. The first argument is a `Glass` value (`.regular` is the only
  documented public variant as of iOS 26 — Clear ships with the system
  but isn't a public SwiftUI constant yet); the `in:` argument is the
  shape (default: capsule). Apply to the surface itself, not to each
  child. (Source:
  https://developer.apple.com/documentation/swiftui/view/glasseffect(_:in:))
- **`Glass.interactive(_:)`** — returns a Glass configured to scale,
  bounce, and shimmer on user interaction. Use on custom controls so
  they match the response of system toolbar buttons and sliders.
  (Source:
  https://developer.apple.com/documentation/swiftui/glass/interactive(_:))
- **`GlassEffectContainer { ... }`** — combines multiple Liquid Glass
  shapes into a single sampling context so adjacent glass surfaces share
  the same underlying region. **Glass cannot sample other glass** —
  this is the rule that drives container usage. Adjacent independent
  glass surfaces produce inconsistent visual behavior; wrap them in a
  container to fix it. Containers also enable fluid morphing transitions
  via `glassEffectID(_:in:)` + a `Namespace`. (Source: WWDC25 323
  "Build a SwiftUI app with the new design" and
  https://developer.apple.com/documentation/swiftui/glasseffectcontainer.)
- **`.glass` / `.glassProminent` button styles** —
  `buttonStyle(.glass)` applies Liquid Glass to a button; `.glassProminent`
  is the higher-emphasis variant. Bordered buttons now default to capsule
  shape on iOS 26; macOS retains rounded-rectangle for mini/small/medium
  control sizes (horizontal density). Use the existing `buttonBorderShape`
  modifier to override. iOS 26 also introduces **extra-large** control
  size for the most prominent actions. (Source: WWDC25 323;
  https://developer.apple.com/documentation/swiftui/glassbuttonstyle.)
- **`.scrollEdgeEffectStyle(_:for:)`** — system-managed fade/blur where
  scrolling content meets a Liquid Glass nav bar, sidebar, tab bar, or
  toolbar. Variants documented in SwiftUI:
  - `.automatic` — the default; system picks the appropriate effect for
    the surface.
  - `.soft` — lighter, softer blur for dense UIs with many floating
    elements (e.g., Calendar app).
  - `.hard` — stronger effect; use sparingly when the system default
    doesn't read.
  Tuning is for "denser UIs with a lot of floating elements" (WWDC25 323).
  Replaces the manual gradient-overlay hacks shipped on iOS 17–18.
  (Source:
  https://developer.apple.com/documentation/swiftui/scrolledgeeffectstyle.)
- **`.backgroundExtensionEffect()`** — duplicates the view into mirrored,
  blurred copies that fill the safe-area edges. Concrete use case: a
  hero photo at the top of a detail view extends behind the nav bar so
  the bar inherits the photo's tonality rather than fighting it. Also
  used to extend scroll content behind an inset sidebar. (Source:
  https://developer.apple.com/documentation/swiftui/view/backgroundextensioneffect()
  and "Landmarks: Applying a background extension effect" sample.)
- **`tabBarMinimizeBehavior(.onScrollDown)`** — opts the tab bar into
  minimize-on-scroll. When the user scrolls down, the bar collapses;
  scrolling up re-expands it. (Source: WWDC25 323.)
- **`.concentric(configuration: .containerConcentric)`** — keeps a
  custom view's corners concentric with its container's corners (so a
  button at the bottom of a sheet shares the sheet's corner center).
  New API in iOS 26. (Source: WWDC25 323.)

### Per-component Liquid Glass adoption
- **Tab bars** — **USE glass.** System default on iOS 26 is a floating
  glass tab bar that sits above content. Custom backgrounds are
  unnecessary and should be removed. (Source: WWDC25 323; WWDC25 356
  "Get to know the new design system.")
- **Nav bars / toolbars** — **USE glass.** Toolbar items auto-group on a
  Liquid Glass surface; the system applies the scroll-edge effect.
  Remove any custom backgrounds or borders — they "interfere with the
  effect." Group bar items by function with `ToolbarSpacer`. (Source:
  WWDC25 356.)
- **Sidebars (iPadOS / macOS)** — **USE glass.** "Sidebars are now inset
  and built with Liquid Glass, allowing content to flow behind them."
  Pair with `.backgroundExtensionEffect()` on the content so carousels
  and hero images glide under the sidebar. (Source: WWDC25 356.)
- **Sheets** — **USE glass (default).** iOS 26 sheets ship with a Liquid
  Glass background. Partial-height sheets are inset with curved edges
  that nest into the display; transitioning to full height makes the
  background opaque. **Remove existing `.presentationBackground(...)`
  modifiers** — they override the new material. (Source: WWDC25 323.)
- **Popovers, menus** — Glass by default in iOS 26.
- **Floating action surfaces / accessory views** — Glass is correct.
- **Cards / content surfaces (body)** — **AVOID glass.** Per WWDC25 219:
  "Liquid Glass…is best reserved for the navigation layer that floats
  above the content of your app." A table view rendered as glass would
  "compete with other elements and muddy the hierarchy." Use opaque
  surface tokens (`systemBackground`, `secondarySystemBackground`).
- **Body text containers** — **NEVER glass.** Glass over a busy background
  fails contrast intermittently as the underlying content scrolls.
- **Form fields / text inputs** — **NOT glass.** Legibility plus visual
  affordance (the user needs to see the input boundary clearly).
- **Status badges, error banners, alerts** — **NOT glass.** These must
  remain legible at rest regardless of underlying content.

### Composition rules (the "glass cannot sample other glass" rule)
Two adjacent glass surfaces sampling independently produces inconsistent
blur and tint. Three concrete rules:

1. **Wrap multiple adjacent glass elements in `GlassEffectContainer`.**
   This is non-optional when you have, e.g., a toolbar + a floating
   action button that visually relate. (Source: WWDC25 323.)
2. **Never stack glass on glass.** "Always avoid glass on glass.
   Stacking Liquid Glass elements on top of each other can quickly make
   the interface feel cluttered and confusing. When placing elements on
   top of Liquid Glass, avoid applying the material to both layers.
   Instead, use fills, transparency, and vibrancy for the top elements
   to make them feel like a thin overlay that is part of the material."
   (Source: WWDC25 219.)
3. **Avoid intersections in steady state.** "In steady states, such as
   when an app first launches, avoid intersections between content and
   Liquid Glass. Instead, reposition or scale the content to maintain
   separation." (Source: WWDC25 219.) Content can pass under glass
   during scroll — that's expected — but a rest-state intersection is
   an authoring problem.

### Performance budget
Apple does not publish a hard "max N glass surfaces per screen" number.
What they do say (WWDC25 323): glass elements grouped via
`GlassEffectContainer` share a sampling region and are cheaper than the
same elements rendered independently. The performance failure mode is
many *independently sampling* glass surfaces.

`(author judgment)` Practical ceiling for a typical screen on
mid-tier hardware: ~3 independent glass surface stacks (e.g., nav bar +
tab bar + one floating accessory). Beyond that, group with a container.
The harness should flag "many independent glass surfaces" rather than
gating on an absolute count.

### Accessibility crossover
Liquid Glass automatically responds to system accessibility settings —
this is a built-in behavior, not something the developer wires up. From
WWDC25 219:

- **Reduce Transparency** — "makes Liquid Glass frostier and obscures
  more of the content behind it." The material becomes effectively
  opaque, falling back to a high-contrast solid surface. Required by
  many users with low vision.
- **Increase Contrast** — "makes elements predominantly black or white
  and highlights them with a contrasting border." Glass tinting flattens
  and a contrast-providing border appears.
- **Reduce Motion** — "decreases the intensity of some effects and
  disables any elastic properties for the material." The bounce and
  shimmer from `Glass.interactive(_:)` are dampened.

Apple's testing recommendation (WWDC25 219): test your app with
**Increase Contrast on + Reduce Transparency off**, and with **both
on**. These two paths cover the design's behavior across the
accessibility-setting matrix users actually run.

Additional accessibility requirements that aren't automatic:
- **VoiceOver labels** — glass controls don't get labels for free.
  Every interactive glass element still needs an `accessibilityLabel`
  describing the action ("Send", not "Glass button").
- **Color contrast** — even with vibrancy adaptation, designers should
  validate that primary text on glass meets WCAG 2.2 AA (4.5:1 normal,
  3:1 large) against the **most-common** underlying content sample.
  Apple's adaptive colors get close, but a brand hex slammed onto glass
  over a photo can dip below threshold. (Source:
  https://developer.apple.com/help/app-store-connect/manage-app-accessibility/sufficient-contrast-evaluation-criteria.)
- **`accessibilityReduceTransparency` environment value** — read this
  to drive design-doc decisions like "show a solid-material fallback
  card." (Source:
  https://developer.apple.com/documentation/swiftui/view/accessibilityreducetransparency.)

### Migration tells
A product that says it's iOS 26-ready but is shipping iOS 17 chrome will
show:
- Nav bar with a flat `Color` background instead of glass.
- Tab bar that doesn't blur the scrolling content beneath it; no
  scroll-edge effect (content cuts hard against the nav bar).
- Sheet that uses `.presentationBackground(.regularMaterial)` instead of
  the iOS 26 default glass.
- Custom darkening overlays or borders around toolbar items (these now
  interfere with the system effect).
- Every toolbar button tinted instead of just the primary action.

Those are upgrade items, not necessarily defects. Note them as iOS 26
adoption gaps, not as HIG violations on iOS ≤18.

### Predicates for harness
Concrete checks the design audit should run against captured UI:

1. **`body_text_on_glass`** — flag any element classified as
   `kind=body_text` rendered on a glass surface. Reserve glass for the
   navigation layer.
2. **`glass_contrast_aa`** — sample the median underlying-content color
   beneath each glass surface; primary text on the glass must reach
   ≥4.5:1 contrast against that median (WCAG 2.2 AA normal text). Flag
   <4.5:1 as a contrast risk.
3. **`glass_on_glass_stack`** — flag any glass surface whose direct
   parent is also a glass surface. The rule "always avoid glass on
   glass" is explicit in WWDC25 219.
4. **`mixed_clear_and_regular_variants`** — if the design specifies a
   Clear-variant element adjacent to a Regular-variant element in the
   same surface stack, flag. Variants must not be mixed.
5. **`independent_glass_surfaces_uncontained`** — if 2+ glass surfaces
   appear within ~80pt of one another and are not wrapped in a
   `GlassEffectContainer`, flag as an adoption gap (composition will
   look inconsistent).
6. **`steady_state_glass_intersection`** — flag glass elements that
   visibly intersect non-glass content in the captured (steady) frame.
   Scrolling overlap is fine; rest-state overlap is not.
7. **`over_tinted_toolbar`** — if >1 toolbar item is tinted in the same
   toolbar group, flag against the "selectively for primary actions"
   rule.
8. **`reduce_transparency_fallback_unspecified`** — design-doc check.
   For any custom glass element, the asset/spec should call out the
   Reduce Transparency behavior. Missing fallback callout → flag.
9. **`legacy_blur_in_ios26_design`** — flag use of
   `.regularMaterial` / `.thinMaterial` / `UIBlurEffect.systemMaterial`
   on a design declared as iOS 26-aligned; these are the pre-Liquid
   Glass material constants.
10. **`custom_presentation_background_on_sheet`** — flag use of
    `.presentationBackground(...)` on iOS 26 sheets; it overrides the
    intended default glass.

### Verification notes
- Sources fetched (2026-05-23):
  - https://developer.apple.com/videos/play/wwdc2025/219/ ("Meet Liquid
    Glass") — primary source for variants, vibrancy, accessibility
    behavior, composition rules, glass-on-glass guidance.
  - https://developer.apple.com/videos/play/wwdc2025/323/ ("Build a
    SwiftUI app with the new design") — primary source for
    `GlassEffectContainer` semantics, `scrollEdgeEffectStyle`,
    `.backgroundExtensionEffect()`, `.tabBarMinimizeBehavior(...)`,
    button-style behavior, `.concentric(...)`.
  - https://developer.apple.com/videos/play/wwdc2025/356/ ("Get to know
    the new design system") — primary source for sidebar / sheet /
    tab-bar behavior on iOS 26.
  - https://developer.apple.com/documentation/swiftui/glasseffectcontainer
  - https://developer.apple.com/documentation/swiftui/scrolledgeeffectstyle
  - https://developer.apple.com/documentation/swiftui/view/glasseffect(_:in:)
  - https://developer.apple.com/documentation/swiftui/view/backgroundextensioneffect()
  - https://developer.apple.com/documentation/swiftui/glass/interactive(_:)
  - https://developer.apple.com/documentation/swiftui/glassbuttonstyle
- Uncertainties: Apple does not publicly publish (a) the dB blur radius
  of `.regular` Liquid Glass, (b) a hard concurrent-surface count for
  performance, or (c) the SwiftUI public constant for the Clear variant
  (Clear exists in the system but the public `Glass.clear` constant has
  not been observed in the documentation indexed at this date).
  Performance ceiling above is marked `(author judgment)`.

## Sources
- Human Interface Guidelines: https://developer.apple.com/design/human-interface-guidelines
- Materials: https://developer.apple.com/design/human-interface-guidelines/materials
- SF Symbols: https://developer.apple.com/sf-symbols/
- WWDC25 session 219 — "Meet Liquid Glass":
  https://developer.apple.com/videos/play/wwdc2025/219/
- WWDC25 session 323 — "Build a SwiftUI app with the new design":
  https://developer.apple.com/videos/play/wwdc2025/323/
- WWDC25 session 356 — "Get to know the new design system":
  https://developer.apple.com/videos/play/wwdc2025/356/
- Adopting Liquid Glass:
  https://developer.apple.com/documentation/TechnologyOverviews/adopting-liquid-glass
- Liquid Glass overview:
  https://developer.apple.com/documentation/TechnologyOverviews/liquid-glass
- Applying Liquid Glass to custom views:
  https://developer.apple.com/documentation/SwiftUI/Applying-Liquid-Glass-to-custom-views
