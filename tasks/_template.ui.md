# <UI: which page or component is being added/changed>

> 2–4 sentence summary. State which user-visible surface this
> touches and what the user sees afterward. UI tasks are visual
> by definition — describe the visible state, not just the code
> structure.
>
> Example: "Add a 'Hot leads' card to `/admin/dashboard`. Shows
> count of leads where `priority = 'hot'` and not contacted in 7+
> days, links to `/admin/tools/leads?priority=hot`. Empty state:
> 'No hot leads — nice work.'"
>
> H1 becomes the branch slug. Keep it tight: "Add X card to Y" /
> "Refactor X form" / "Add /Z page".

## Why this exists

> 1–3 short paragraphs. UI tasks usually come from product priorities
> rather than architectural pressure — name the user need, the
> behavioral metric you're trying to move, or the manual workflow
> this replaces.
>
> Visual references help: link to a Figma frame, a screenshot of
> the desired state, or the closest existing page if the new one
> mirrors a sibling.

## What to do

> Numbered, concrete steps. UI tasks are especially prone to
> implementation drift — be explicit about which library primitives
> to use.
>
> Good: "Create `client/src/pages/admin/dashboard/HotLeadsCard.tsx`.
> Use the existing `<Card>` component from `@/components/ui/card.tsx`,
> NOT a raw `<div>`. Empty state uses `<EmptyState>` from
> `@/components/ui/empty-state.tsx`. Link uses `<Link>` from
> `wouter` (project routing convention). No inline styles."
>
> Bad: "Add a hot leads card."

1. ...
2. ...
3. ...

## Constraints (don't violate these)

> **UI-specific hard rules. Reviewer enforces, especially library lock.**
>
> - **UI library lock.** This project uses `<UI library — fill in:
>   shadcn/ui, MUI, Chakra, etc.>` — do not introduce alternatives.
>   Reviewer flags any new framework imports.
> - **No inline styles.** All styling goes through the project's
>   convention (Tailwind classes, CSS modules, theme tokens, etc.).
> - **Semantic HTML required.** Buttons are `<button>`, links are
>   `<a>` / framework `<Link>`, headings nest correctly. No
>   `onClick` on `<div>` without `role="button"`.
> - **Accessibility minimums** (WCAG 2.2 AA basics, reviewer checks):
>   - Every `<img>` has `alt`.
>   - Every form input has a `<label>` (or `aria-label`).
>   - Color contrast ≥ 4.5:1 for text (use existing theme tokens
>     to inherit project's tested contrast).
>   - Keyboard navigation works (Tab through, Enter activates).
> - **No new client-side dependencies** (`react-toastify`, etc.)
>   without explicit approval. Project's primitive set is sufficient
>   for most UI tasks.
> - **Loading + error + empty states** all addressed. Don't ship
>   a "happy path only" UI.
> - **No `git push`.** Host owns the branch.
> - **Off-limits**: `.yukticastle/`, secrets, deployed prod files.

## Files to read

> UI tasks need:
>
> - The closest existing component or page that mirrors the shape
>   you're building. ("Make X like Y" tasks succeed because Y is
>   actually loaded into context.)
> - The project's design-token / theme file (so the agent uses the
>   right palette + spacing scale).
> - The shared UI primitive set (`components/ui/*` or equivalent) —
>   so the agent reaches for `<Card>` and `<Button>` instead of
>   re-inventing.
> - The data-loading hook the new component will use (TanStack
>   Query, SWR, server actions, etc.).

- `client/src/pages/admin/<closest-sibling>.tsx`
- `client/src/components/ui/card.tsx`
- `client/src/lib/theme.ts`  (or wherever design tokens live)
- `client/src/lib/api.ts`  (or wherever the data hook lives)

## Acceptance criteria

> **UI-specific:**
>
> - `npx tsc --noEmit` passes.
> - **Visual verification one of:**
>   - Playwright/Cypress test asserting the rendered text + key
>     interactive elements (preferred — automatable).
>   - Screenshot comparison against a reference PNG checked into
>     `tests/fixtures/screenshots/` (works for static layouts).
>   - A specific manual-QA checklist the operator runs locally
>     post-merge (acceptable for one-off internal admin views).
> - **UI library lock satisfied** — `git diff package.json` adds
>   no new client deps; no new imports from `@mui/*`, `@chakra-ui/*`,
>   etc. (whatever's NOT in the project's locked set).
> - **No inline styles** — `style={{...}}` doesn't appear in the diff.
> - **Empty / loading / error states** all rendered (test or
>   screenshot covers each).
> - The exact set of files created/modified.

- ...
- ...
- ...

## When done

Emit `<promise>COMPLETE</promise>` on its own line so the
orchestrator knows you're finished.

In your final summary, include:
- The component / page path(s) you created.
- The data flow (which hook / endpoint feeds the new UI).
- The empty / loading / error state behavior, in 1-2 sentences each.
- The local URL for operator review (e.g. `http://localhost:3002/admin/dashboard`).
