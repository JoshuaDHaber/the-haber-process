# Working notes

## UI changes: keep the whole app consistent

Any change under `frontend/src` that users can see sets a pattern. Make the rest of the app match it in the same PR, so no cleanup PR is needed later.

1. **Sweep before building.** Name the pattern you're touching:
   - a state: loading, empty, error, partial, refreshing
   - a control: sort, filter chips, segmented picker, retry or CTA
   - a surface: card, tile, section header with count, muted note, primer

   Then grep for every other place that renders it, including the old way of doing it. Check both leagues (`App.tsx`, `NbaApp.tsx`), both game pages (`GameOverview`, `NbaGameOverview`) and every board in `components/`. Bring each match along, or list it in the PR under "Not brought along" with the reason.
2. **Cover every state of every board or section you touch.**

   | State | What it should be |
   |---|---|
   | loading | `Skeleton` shaped like the content; measure the layout jump |
   | filled | the board itself |
   | empty | `EmptyState` |
   | error | `LoadError`; never let a failure fall through to empty-state copy |
   | partial | `.board-note` naming what's missing and when it fills in |
   | refreshing | `.refreshing` + `aria-busy` while placeholder data shows |
3. **Use the shared building blocks** instead of one-off markup:
   - `EmptyState` / `LoadError` (`components/EmptyState.tsx`)
   - `Skeleton` with a `label`
   - `SortBar` (phones) + `SortTh` (keyboard-sortable headers with `aria-sort`)
   - `HowToRead` primers, shown only with rows
   - `.sub-h` headers with "· N" counts
   - `.board-note`
   - `chip-btn` (32px touch target)
   - the 16px card radius
4. **Check it in the live preview**, not just in code:
   - at 375px and desktop width, in light and dark mode
   - keyboard focus never lost, and changes announced to screen readers
   - force the states: stub `fetch` to return 500 for an error, delay it to see loading and refreshing, and clear the `query-cache:*` localStorage keys so the persisted cache doesn't hide them
   - measure with JS instead of eyeballing

   List what you checked in the PR under "UX consistency review".
