# Active-KB badge on KB cards — design

**Date:** 2026-05-21
**Status:** approved (design), pending implementation

## Problem

The KB that answers chat questions is chosen by a single global preference,
`defaultKbName`, set only in **Settings → "Default knowledge base."** Nothing in
the UI shows which KB is currently active, so on the `/kb` page every card looks
the same. Users can't tell at a glance which KB their questions go to.

## Goal

On the `/kb` page, visibly mark the one KB card that is the current active
default for chat. Read-only — no behaviour change to selection.

## Non-goals (explicitly out of scope)

- Clicking the badge to switch the active KB (selection stays in Settings).
- Any indicator in the chat view or the sidebar.
- Multi-KB selection (the backend supports `kb_names`, but the UI exposes one).
- Build / health / indexing states. This badge means only "active for chat."

## Design

A small **`● ACTIVE` pill** appears next to the title of the KB card whose name
equals the saved `defaultKbName`. All other cards are unchanged. The existing
decorative yellow dot (`aria-hidden`) stays exactly as-is.

### Data flow (pure frontend — no API/backend change)

- `frontend/src/app/kb/page.tsx` (the list page) reads `defaultKbName` from the
  preferences lib (`@/lib/preferences`, backed by localStorage). Preferences are
  read **after mount** (in an effect / client-only) so the value is `null` on
  the server render and resolves on the client — avoiding an SSR/hydration
  mismatch. The page passes `isActive={k.name === activeKbName}` into each
  `KBCard`.
- `KBCard` renders the pill only when `isActive` is true.

### Visual / accessibility

- The pill reuses the `rounded-full` chip styling and the CNRS-yellow token
  already used on the page (mirrors the existing red error pill / grey chip
  patterns). It contains the **visible text "ACTIVE"** (the inner dot is
  decorative), so the signal is textual, not color-only.

### Edge cases

- `defaultKbName` is `null` ("No KB — web only") or points to a KB that no
  longer exists → no card matches → **no pill shown anywhere.**
- The badge reflects the *current default* (what new questions will use), not
  the KB any past conversation happened to use.

## Files touched

- `frontend/src/app/kb/page.tsx` — the page (read preference, compute
  `activeKbName`, pass `isActive`) and the `KBCard` component (render the pill).

## Testing

- Check for existing frontend test infrastructure during planning. If present
  (e.g. Vitest / React Testing Library), add a small render test: a card whose
  name matches the active preference shows "ACTIVE"; a non-matching card does
  not; `null` preference shows no badge.
- If no frontend test harness exists, verify manually on `/kb` (set a default KB
  in Settings → confirm exactly that card is badged; clear it → confirm none).
