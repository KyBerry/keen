---
source: https://www.nngroup.com/articles/ten-usability-heuristics/
last_verified: 2026-05-23
---

# Nielsen's 10 Usability Heuristics (Condensed)

These are evaluation lenses, not laws. Cite them by number when used.

## 1. Visibility of system status

The interface shows what's happening through appropriate feedback, in
reasonable time.

**Tells in a screenshot:**
- A button or link with no hover/active/loading affordance distinguishable
  from its idle state.
- Async actions (save, send, upload) with no progress, spinner, or skeleton.
- Selection state on a list item that doesn't visibly differ from idle.
- A multi-step flow with no step indicator.

**Cite as:** "violates Nielsen #1 — no visible status for [action]."

## 2. Match between system and the real world

Use language the user knows. Order information naturally.

**Tells:**
- Dev jargon in user-facing strings ("entity", "payload", "404", "exception").
- Date/time/currency in formats that don't match the user's locale.
- Icons that mean different things to different audiences with no label.
- Error codes shown without explanation.

## 3. User control and freedom

Easy escape from accidental states. Undo and redo. Cancel from any modal.

**Tells:**
- Modal with no close affordance (X), no `Esc`-to-close.
- Destructive action with no undo or confirmation.
- Multi-step form that loses state on back-navigation.
- A "this cannot be undone" without an actual confirmation step.

## 4. Consistency and standards

Same word, situation, and action everywhere. Follow platform conventions.

**Tells:**
- "Save" / "Submit" / "Confirm" / "Apply" used interchangeably for the same
  action across the product.
- Primary button on the left in one dialog, right in another.
- Different icons for the same concept on adjacent screens.
- Disagreement with the host platform's conventions for no benefit (e.g., a
  custom right-click menu on the web that omits "Open in new tab").

## 5. Error prevention

Better than good error messages: design that prevents the error.

**Tells:**
- A free-text date field with no format hint and no calendar picker.
- Destructive primary button immediately adjacent to a frequent benign action.
- A form that validates only on submit when it could validate on blur.
- Required fields marked only after a failed submit.

## 6. Recognition rather than recall

The user shouldn't have to remember information across screens.

**Tells:**
- Step 3 of a wizard doesn't show what was entered in step 1.
- A search results page that doesn't show the query that produced it.
- Settings that require remembering a code or ID from another screen.
- A keyboard shortcut with no on-screen reference (no menu listing, no
  tooltip, no help overlay).

## 7. Flexibility and efficiency of use

Accelerators for experts, defaults for novices. Both populations served.

**Tells:**
- No keyboard shortcuts for high-frequency actions.
- No bulk operations on lists where bulk is the obvious need.
- No saved views, no recents, no pinned items.
- Mandatory wizard for an action that should be a single command for
  experienced users.

## 8. Aesthetic and minimalist design

Every extra element competes with the relevant ones. Information density
should be deliberate, not accidental.

**Tells:**
- Heavy gradients, drop-shadows, and chrome on UI elements that aren't the
  focus.
- Multiple competing primary actions on one screen.
- Decorative imagery that pushes core content below the fold.
- Three or more distinct font families on one screen.

This is not "make it minimalist." It's "remove what isn't load-bearing." A
dense Bloomberg-terminal screen can fully satisfy #8 if every pixel is doing
work.

## 9. Help users recognize, diagnose, and recover from errors

Plain language. Pinpoint the problem. Suggest a fix.

**Tells:**
- "Something went wrong" with no detail and no next action.
- Error toast that disappears before the user can read it (< 5s for a
  multi-clause message).
- Validation message far from the offending field.
- Red text on red background, or any error styling that itself fails contrast.

## 10. Help and documentation

Even better when not needed, but should exist for the cases that need it.

**Tells:**
- A complex feature with no inline help, tooltips, or "Learn more" link.
- Help that requires leaving the product entirely (full page redirect to
  docs in a new domain) for first-step questions.
- Empty states with no guidance on what to do next.

---

## How to use this in critique

Pair every heuristic citation with:
1. The specific element (selector or component label).
2. Which sentence of the heuristic is violated.
3. The fix, in one sentence.

Example:
> Modal at `[data-testid=delete-dialog]` violates Nielsen #3 (User Control):
> no `Esc` handler and no X button. Add an `aria-label="Close"` icon button
> top-right and bind `Esc` to its click handler.

## Sources
- Nielsen Norman Group, "10 Usability Heuristics for User Interface Design":
  https://www.nngroup.com/articles/ten-usability-heuristics/
