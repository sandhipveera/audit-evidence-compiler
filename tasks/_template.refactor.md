# <Refactor: what's being restructured>

> 2–4 sentence summary. **Refactors are bit-for-bit behavior-preserving
> by definition** — name what's changing structurally without
> changing what users observe. Example: "Extract the campaign-send
> loop from `server/routes.ts` into a dedicated `server/campaigns/runner.ts`
> module so the cron handler and the admin retry button can share
> it." Bad: "Clean up campaigns code." (Reviewer can't verify "clean".)
>
> H1 becomes the branch slug, so keep it short, verb-led:
> "Refactor X into Y" / "Extract X from Y" / "Inline X into Y".

## Why this exists

> 1–3 short paragraphs. Refactors fall into two camps — **prep** for
> upcoming feature work that needs a cleaner shape, or **debt
> paydown** because the current shape now actively costs you.
> Naming which camp this is helps the agent calibrate scope.
>
> If a follow-up feature depends on this refactor landing, link to
> that task or issue.

## What to do

> Numbered, concrete steps. Refactors have especially strict
> change boundaries — make them explicit.
>
> Good: "Move `runCampaignCycle()` from `server/routes.ts` (lines
> 240–340) into a new `server/campaigns/runner.ts`. Export under the
> same name. Update the only caller in `server/routes.ts:cronHandler`
> to import + invoke. Don't change the function body or its call
> signature."
>
> Bad: "Refactor campaigns to use better patterns."

1. ...
2. ...
3. ...

## Constraints (don't violate these)

> **Refactor-specific hard rules — reviewer enforces by diffing
> behavior, not just structure:**
>
> - **NO behavior changes.** Same inputs produce same outputs, same
>   side effects, same error paths. If you find a bug along the way,
>   note it in your summary — DO NOT fix it in this PR.
> - **NO new dependencies.** Refactors restructure existing code;
>   adding a dep is a separate decision.
> - **NO public API changes.** Exported function signatures, route
>   paths, schema columns, env vars all stay identical.
> - **NO schema changes.** If the refactor needs schema work,
>   it's a schema task — use `_template.schema.md` instead.
> - Existing tests pass without modification (or only with
>   mechanical updates like import-path changes — call those out
>   in the diff explicitly).
> - **No `git push`.** Host owns the branch.
> - **Off-limits**: `.yukticastle/`, secrets, deployed prod files.

## Files to read

> Refactors need to read:
>
> - The file(s) being restructured.
> - Every CALLER of the function/module/type being moved (use
>   `grep -r` results in the spec — explicitly list call sites so
>   the agent doesn't miss one).
> - The ADR that motivates the new shape, if any.
> - Existing tests for the code being moved (so the agent knows
>   what behavior is contractually preserved).

- `path/to/source-being-refactored.ts`
- `path/to/caller-1.ts`
- `path/to/caller-2.ts`
- `tests/source-being-refactored.test.ts`
- `docs/decisions/000X-the-relevant-adr.md`

## Acceptance criteria

> **Refactor-specific:**
>
> - `npx tsc --noEmit` passes (zero errors).
> - **All existing tests pass with no body changes** — only
>   mechanical updates (import paths, etc.) are acceptable.
> - **No new public exports** beyond what was there before.
> - **No new dependencies** in `package.json`.
> - The exact set of files created/modified (closes the door on
>   scope creep). For a refactor, this list is small and known
>   upfront — reviewer flags anything beyond it.
> - Behavior preservation: smoke test exits 0 with same output as
>   before, OR a specific behavioral check ("`/api/campaigns/run`
>   still returns the same JSON shape for a given fixture
>   campaign").

- ...
- ...
- ...

## When done

Emit `<promise>COMPLETE</promise>` on its own line so the
orchestrator knows you're finished.

If you discovered a bug while refactoring, mention it in your
final summary — but do NOT fix it. Bug fixes belong in their own
task (`_template.bugfix.md`).
