# <Imperative title — what gets built/changed>

> Replace this blockquote with a 2–4 sentence summary. State what
> this task is and why it's worth doing. The agent makes better
> trade-offs when it understands the goal, not just the surface
> instruction.
>
> The H1 above becomes the branch slug, so keep it short, verb-led,
> and free of filler ("Add X", "Refactor Y", "Migrate Z" — not
> "I want to maybe add some kind of X").

## Why this exists

> 1–3 short paragraphs on context. Mention the constraint, customer
> need, or architectural force that motivates the change. If there's
> a deadline, name it. If this composes with other work, link it.
>
> Skippable for trivial tasks (one-line tweaks). For anything bigger
> than one file, keep it.

## Before finalizing the spec — entry-point checklist

> **Read this BEFORE drafting "What to do" below. Delete this section
> from your final spec — it's authoring guidance, not agent
> instructions.**
>
> If the task changes behavior on a **state transition** (e.g. "when
> X becomes `published` / `approved` / `paid` / `sent`"), enumerate
> **all entry points** into that state. The implementer obeys the
> spec; if you only mention one entry point, the implementer wires
> only that one and the feature appears broken for any other
> workflow that reaches the same state.
>
> Common gaps to check before writing the spec:
>
> - **POST creating-in-target-state** vs **PUT transitioning-to-target-state**
>   (e.g. "new post with `published: true`" vs "existing post flipped
>   from `false → true`" — both hit the same downstream pipeline,
>   need the same hook).
> - **Bulk import** endpoints (`POST /import`, CSV uploads, batch
>   admin actions).
> - **Cron jobs / background workers** that write to the same field
>   from a schedule.
> - **Webhook handlers** (incoming events from third parties that
>   flip state).
> - **Direct SQL or admin-tool flips** (less common — skipping is
>   usually fine; mention in the spec if the operator does these
>   manually).
>
> If two or more entry points apply, the implementer should extract
> a shared helper rather than duplicating logic — call this out
> explicitly in "What to do" so it doesn't get inlined.
>
> Same authoring discipline applies to: "added a new column, forgot
> to wire it into the insert schema," "added a new email type,
> forgot to add it to the bulk-send list," etc. Cheap to spec
> correctly; expensive to catch in post-deploy testing.
>
> _Real example that motivated this section: AccessQuint
> `merge-manual-blog-into-pipeline` (PR #47) specced only the
> `PUT /api/admin/blog-posts/:id` route. The UI defaulted new posts
> to `published: true`, which goes through POST. Feature appeared
> broken for the default workflow. Hand-fixed in PR #48 by
> extracting a shared `distributeManualBlogPost()` helper called
> from both POST and PUT._

## What to do

> Numbered list of concrete steps, with explicit file paths,
> commands, schema names, etc. The agent reads this as a checklist;
> the more specific you are, the fewer iterations it takes.
>
> Good: "Create `server/domains/hospitality.ts` mirroring
> `server/domains/wellness.ts`. Persona = hotel-industry analyst,
> 15+ years experience. Compliance lens = ADA + FTC + no-medical."
>
> Bad: "Add a hospitality domain config."

1. ...
2. ...
3. ...

## Constraints (don't violate these)

> Hard rules. Reviewer enforces. Block on these. Common ones:
>
> - **Architecture/contract violations** — name the ADR, the
>   pattern, the interface. ("No hardcoded vertical strings outside
>   `server/domains/<slug>.ts` per ADR-0046.")
> - **Off-limits files** — yukticastle scaffold, secrets, prod state.
> - **Migration safety** — "DB migration goes in
>   `migrations/000N_manual.sql`. Operator applies manually. Don't
>   apply during the agent loop."
> - **No `git push`. No PRs. No merging.** The host owns the branch.
> - **Scope budget** — "Keep under ~400 LOC" or "Modify N files
>   max" if you want a tight blast radius.
> - **No new dependencies** if you want to keep package.json clean.

## Files to read

> Explicit list. Saves the agent discovery iterations and stops it
> from grep-walking the whole tree to find the pattern.
>
> Include: the canonical reference implementation, the type
> definition, the registry/config it gets wired into, the relevant
> ADR(s), the closest sibling test/script.

- `path/to/canonical-reference.ts`
- `path/to/types.ts`
- `path/to/registry-or-config.ts`
- `docs/decisions/000X-the-relevant-adr.md`

## Acceptance criteria

> Concrete, testable, mechanical. Reviewer runs each. Examples that
> work well:
>
> - `npx tsc --noEmit` passes (zero errors).
> - `npm run smoke` exits 0 and prints `SMOKE OK`.
> - `npm test -- <new-file>` passes.
> - The exact set of files created/modified (closes the door on
>   scope creep — reviewer flags anything beyond this list).
> - Specific output value or DB state ("the row at UUID
>   `…N` exists with `field = 'expected-value'`").

- ...
- ...
- ...

## When done

Emit `<promise>COMPLETE</promise>` on its own line so the
orchestrator knows you're finished.
