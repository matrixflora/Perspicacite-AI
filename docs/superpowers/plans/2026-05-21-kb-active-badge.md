# Active-KB badge on KB cards — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the `/kb` page, visibly mark the one KB card that is the current active default for chat (`defaultKbName`).

**Architecture:** Pure frontend, single file. The list page reads `defaultKbName` from the preferences lib (localStorage) in a mount effect and passes an `isActive` boolean to each `KBCard`; the card renders a small "Active" pill when `isActive` is true. No API/backend change.

**Tech Stack:** Next.js (App Router, client component), React, Tailwind. No frontend test runner exists in this project, so verification is `tsc --noEmit` + a manual browser check (per the spec).

Spec: `docs/superpowers/specs/2026-05-21-kb-active-badge-design.md`

---

## File Structure

- **Modify:** `frontend/src/app/kb/page.tsx` — both the `KBListPage` component (read the preference, pass `isActive`) and the `KBCard` component (render the pill). These live in the same file and change together.

No other files. No new files.

---

### Task 1: Mark the active KB on the KB list page

**Files:**
- Modify: `frontend/src/app/kb/page.tsx` (imports, `KBListPage`, `KBCard`)

- [ ] **Step 1: Import the preferences loader**

In `frontend/src/app/kb/page.tsx`, the current imports are:

```tsx
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "@/components/PageHeader";
import { kb, type KBSummary } from "@/lib/api";
```

Add the preferences import as a new line after the `@/lib/api` import:

```tsx
import { loadPreferences } from "@/lib/preferences";
```

- [ ] **Step 2: Track the active KB name in `KBListPage`**

In `KBListPage`, the current state declarations are:

```tsx
  const [items, setItems] = useState<KBSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
```

Add one more state line below them:

```tsx
  const [activeKbName, setActiveKbName] = useState<string | null>(null);
```

Then, immediately after the existing `useEffect` that calls `refresh()`:

```tsx
  useEffect(() => {
    refresh();
  }, [refresh]);
```

add a second effect that reads the preference on the client (avoids an SSR/hydration mismatch — the value is `null` on the server render and resolves after mount):

```tsx
  useEffect(() => {
    setActiveKbName(loadPreferences().defaultKbName);
  }, []);
```

- [ ] **Step 3: Pass `isActive` to each card**

Change the card render inside the list. Current:

```tsx
            {items?.map((k) => (
              <li key={k.name}>
                <KBCard kb={k} />
              </li>
            ))}
```

to:

```tsx
            {items?.map((k) => (
              <li key={k.name}>
                <KBCard kb={k} isActive={k.name === activeKbName} />
              </li>
            ))}
```

- [ ] **Step 4: Render the pill in `KBCard`**

Change the `KBCard` signature. Current:

```tsx
function KBCard({ kb: k }: { kb: KBSummary }) {
```

to:

```tsx
function KBCard({ kb: k, isActive }: { kb: KBSummary; isActive: boolean }) {
```

Then replace the card's header block. Current:

```tsx
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-lg font-semibold tracking-tight text-[var(--cnrs-blue)] group-hover:underline">
          {k.name}
        </h3>
        <span
          className="grid h-2.5 w-2.5 place-items-center rounded-full transition group-hover:scale-125"
          style={{ background: "var(--cnrs-yellow)" }}
          aria-hidden
        />
      </div>
```

with (adds an "Active" pill to the left of the existing decorative dot when this card is the active KB; the dot is unchanged):

```tsx
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-lg font-semibold tracking-tight text-[var(--cnrs-blue)] group-hover:underline">
          {k.name}
        </h3>
        <div className="flex shrink-0 items-center gap-2">
          {isActive && (
            <span className="inline-flex items-center gap-1 rounded-full bg-[var(--cnrs-yellow)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--cnrs-blue)]">
              <span
                className="h-1.5 w-1.5 rounded-full bg-[var(--cnrs-blue)]"
                aria-hidden
              />
              Active
            </span>
          )}
          <span
            className="grid h-2.5 w-2.5 place-items-center rounded-full transition group-hover:scale-125"
            style={{ background: "var(--cnrs-yellow)" }}
            aria-hidden
          />
        </div>
      </div>
```

The pill reuses the codebase's existing yellow-chip pattern (`rounded-full bg-[var(--cnrs-yellow)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--cnrs-blue)]`, as used on `survey/[id]/page.tsx`). The visible word "Active" makes it a text signal, not color-only.

- [ ] **Step 5: Typecheck**

Run:

```bash
cd frontend && npx tsc --noEmit
```

Expected: no output / exit 0 (no type errors). If it complains that `isActive` is missing on a `KBCard` usage, you missed Step 3.

- [ ] **Step 6: Manual verification in the browser**

With the app running (`./dev.sh` from the repo root, or `cd frontend && npm run dev`):

1. Open **Settings** → **Default knowledge base** → select a KB (e.g. `AI_scientist`). The selection persists to localStorage immediately.
2. Open **/kb**. Expected: the `AI_scientist` card shows a yellow **● Active** pill next to its name; no other card does.
3. Back in **Settings**, set the dropdown to **"— No KB (web only) —"**.
4. Reload **/kb**. Expected: **no** card shows the pill.

(Hard-refresh — Ctrl+Shift+R — if a card doesn't update, to bypass cache.)

- [ ] **Step 7: Commit**

```bash
cd /mnt/d/new_repos/perspicacite_v2
git add frontend/src/app/kb/page.tsx docs/superpowers/specs/2026-05-21-kb-active-badge-design.md docs/superpowers/plans/2026-05-21-kb-active-badge.md
git commit -m "feat(frontend): mark the active KB on the KB list page

The active KB for chat is a single preference (defaultKbName) set in
Settings, with nothing in the UI showing which KB is active. Add a small
'Active' pill to that KB's card on /kb: the page reads defaultKbName from
the preferences lib in a mount effect and passes isActive to each KBCard,
which renders the pill when true. Read-only; selection is unchanged.

Includes the design spec + this plan."
```

---

## Self-Review

**1. Spec coverage:**
- Badge on the active KB card → Steps 3–4. ✓
- Read `defaultKbName` client-side via effect (no hydration mismatch) → Step 2. ✓
- Pass `isActive`; card renders pill when true → Steps 3–4. ✓
- Visual: `rounded-full` chip, CNRS-yellow token, visible "Active" text → Step 4. ✓
- Edge cases: `defaultKbName` null or pointing to a deleted KB → `activeKbName` is `null` / no name matches → no pill anywhere (the `k.name === activeKbName` comparison is simply false for every card). ✓
- Testing: no FE harness → manual verification → Step 6. ✓
- Files touched: only `frontend/src/app/kb/page.tsx` → matches spec. ✓

**2. Placeholder scan:** No TBD/TODO; every code and command step is concrete. ✓

**3. Type consistency:** `isActive: boolean` (Step 4 signature) matches the prop passed in Step 3; `activeKbName: string | null` (Step 2) matches `defaultKbName`'s type and the `=== activeKbName` comparison. ✓
