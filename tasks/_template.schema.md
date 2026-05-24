# <Schema change: what columns/tables get added or modified>

> 2–4 sentence summary. State which tables are touched and what the
> end-state schema looks like. Schema changes are the highest-risk
> task shape — be explicit about reversibility, indexing, and any
> backfill logic.
>
> Example: "Add `lastContactedAt` and `unsubscribedAt` columns to
> `prospects`. Both nullable. New rows default null; backfill is
> not needed (the columns track future activity)."
>
> H1 becomes the branch slug. Keep it tight: "Add X to Y" /
> "Migrate Z column to …" / "Drop unused W table".

## Why this exists

> 1–3 short paragraphs. Schema changes typically come from one of:
> - A feature task that needs new columns (link to it).
> - A migration to fix a data-modeling decision that's now wrong.
> - Cleanup of unused columns/tables.
>
> If this is preparing for downstream feature work, link the
> follow-up. Schema-first deploys are easier to roll back than
> feature-first ones.

## What to do

> Numbered steps. Two surfaces ALWAYS get touched in lockstep:
>
> 1. **The schema file** (TypeScript / Prisma / Drizzle source of
>    truth) — for type safety + IDE/agent awareness.
> 2. **A new SQL file at `migrations/manual_<feature>.sql`** — the
>    actual statements the operator runs against the DB.
>
> Both must match. Don't run the migration in the agent loop. The
> operator applies it manually post-merge against staging then
> production.
>
> Good: "Add to `shared/schema.ts`:
> `unsubscribedAt: timestamp('unsubscribed_at')`. Then write
> `migrations/manual_add_prospect_columns.sql`:
> `ALTER TABLE prospects ADD COLUMN unsubscribed_at timestamptz;`"
>
> **Entry-point check for new state columns.** When the new column
> represents domain state (a status flag, a transition timestamp,
> a moderation decision), apply the entry-point checklist from
> `tasks/_template.md` BEFORE writing the spec — enumerate every
> place that writes the column (POST/PUT routes, bulk importers,
> cron jobs, webhook handlers) and either spec all of them or call
> out a shared helper. The implementer won't infer entry points
> from a schema diff alone.

1. ...
2. ...
3. ...

## Constraints (don't violate these)

> **Schema-specific hard rules. The reviewer is especially strict here.**
>
> - **DO NOT run any automatic DB migration command.** Forbidden
>   invocations include (see project's `.yukticastle/policy.json`
>   for the full list):
>
>   ```text
>   forbidden — never invoke from agent code:
>     <db push command for your ORM>
>     <db migrate deploy command for your ORM>
>   ```
>
>   The reviewer blocks on any of these appearing in scripts or
>   commit messages.
> - **All schema changes go to `migrations/manual_<feature>.sql`.**
>   One file per task. No editing prior `manual_*.sql` files.
> - **The schema file (TS source of truth) MUST match the SQL.**
>   Both surfaces updated in the same commit.
> - **Reversibility note required.** In the SQL file, add a comment
>   block at the top explaining how to undo this migration if needed
>   (the inverse statements). The reviewer fails the task if absent
>   for non-trivial changes.
> - **Index strategy explicit.** For new tables or columns over
>   existing high-traffic tables, specify whether to create indexes
>   `CONCURRENTLY` (Postgres) or in a follow-up off-hours migration.
> - **No backfill in the agent loop.** If new columns need data
>   backfilled, the SQL file includes the backfill statement but
>   the operator runs it post-deploy with appropriate batching.
> - **No `git push`.** Host owns the branch.
> - **Off-limits**: `.yukticastle/`, secrets, deployed prod files.

## Files to read

> Schema tasks need:
>
> - The schema source-of-truth file (`shared/schema.ts`,
>   `prisma/schema.prisma`, etc.) — to see existing column patterns.
> - The most recent `migrations/manual_*.sql` — to mirror style
>   (CREATE TABLE order, ALTER pattern, comment header format).
> - Any storage-layer code that currently queries the affected
>   tables — to flag downstream call sites that may need updates.
> - The relevant ADR if there's one ("ADR-0042: prospects table is
>   the source of truth for marketable contacts").

- `shared/schema.ts`  (or `prisma/schema.prisma`)
- `migrations/manual_<latest>.sql`
- `server/storage.ts`  (or whatever queries the affected tables)
- `docs/decisions/000X-the-relevant-adr.md`

## Acceptance criteria

> **Schema-specific:**
>
> - `npx tsc --noEmit` passes (proves the schema-file change typechecks).
> - **`migrations/manual_<feature>.sql` exists** at the expected
>   path. Reviewer verifies file presence, not just code.
> - **Schema source-of-truth matches the SQL.** Same column names,
>   types, NOT NULL constraints, defaults. Reviewer cross-checks.
> - **Reversibility comment block at the top of the SQL file**
>   listing the inverse statements.
> - **No `db:push` / `db:migrate` invocation** anywhere in the diff.
> - The exact set of files created/modified.
> - For new public-API columns (e.g. exposed in REST/GraphQL):
>   downstream API contract noted in the commit body — operator
>   knows to update OpenAPI/SDL after this lands.

- ...
- ...
- ...

## When done

Emit `<promise>COMPLETE</promise>` on its own line so the
orchestrator knows you're finished.

In your final summary, include:
- The exact path to the new `manual_*.sql` file.
- One-line note: "Apply manually post-merge: `psql $DATABASE_URL
  -f migrations/manual_<feature>.sql`".
- Any downstream API/storage-layer updates the operator should
  ship in a follow-up task.
