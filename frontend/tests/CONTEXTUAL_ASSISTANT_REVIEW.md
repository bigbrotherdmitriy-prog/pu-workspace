# Contextual assistant: explicit activation

Base: `f978c72541d1c368c7df425f5f3f0647afe45c6b`.

## Cause and change

The assistant previously listened to `mouseover` and `focusin` on the entire
document and displayed a fixed-position bubble above cards and controls after
550 ms (immediately for keyboard focus). This obscured task text during reading.

The component now remembers the last hovered/focused supported element without
rendering a bubble. The existing native AI Secretary button remains the only
activation point, with an accessible name and `type="button"`. Moving focus or
the pointer onto the mascot preserves the element context. Section navigation
clears it; without a selected element the prompt contains only the section,
rather than the entire body's text. No external action occurs on hover/focus.

This intentionally disables unsolicited bubbles across sections, not just the
task registry. Existing unused `.ai-hover-bubble` styles are left untouched to
avoid unrelated changes to shared stylesheet work.

## Verification

- `pnpm run check`: passed.
- `pnpm run test`: 45 tests across 11 files passed, including five new component
  tests for passive hover/focus, explicit activation, keyboard focus, context
  reset/fallback and listener cleanup.
- `git diff --check`: passed.
- Test runner needed execution outside the Windows sandbox because esbuild was
  denied directory access; no dependency or lockfile changes were required.

No full browser/production verification was performed by this agent. A native
button retains browser Enter/Space activation; the component tests check focus,
semantics and the click handler, not browser-generated keyboard click events.
Integration should verify Tab -> mascot -> Enter/Space and unchanged text
visibility after hovering a long task card.

## Keyboard-context follow-up

Integration browser QA found that focusing a control can scroll its card under
a stationary pointer, producing `mouseover` and replacing the focused context.
The follow-up retains focus context until mouse coordinates actually change in
`mousemove`; intentional pointer movement then resumes pointer selection.
Two component regressions cover stationary-pointer scroll events and intentional
movement. The updated suite passes 47 tests across 11 files; check also passes.
Native browser confirmation belongs to the integration QA.

No backend, task records, API, task module, shared CSS or generated `react_dist`
files were changed. Build and production deployment belong to the integration
step.
