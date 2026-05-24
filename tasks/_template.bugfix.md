# <Bug fix: what's wrong, in one phrase>

> 2–4 sentence summary. Bug-fix tasks have a more constrained shape
> than features — the **observable problem** is the H1 framing,
> and the body explains repro + root cause + fix.
>
> Example: "`/admin/tools/leads` returns 500 when the score field
> is null. Root cause: the sort order assumes non-null scores.
> Fix: coalesce nulls to 0 in the sort comparator."
>
> H1 becomes the branch slug. Use bug-language: "Fix X" / "Stop X
> happening when Y" / "Repair Z handler under condition W".

## Why this exists

> The bug, observed. Cite the issue tracker, the failing test, the
> support ticket, or the operator's log line that surfaced it.
> Bug-fix tasks need this section to disambiguate scope — without
> it, the agent often "fixes" symptoms it imagines rather than the
> one you actually saw.

> If there's an issue: link it. (`#42`, `accessquint#117`, etc.)

## Reproduction steps

> **Bug-fix-specific section** — required.
>
> Step-by-step instructions to trigger the bug. The agent must be
> able to reproduce the failure before claiming a fix. The reviewer
> verifies the regression test exercises this path.
>
> Good:
> 1. `npm run dev`
> 2. POST `/api/tools/submit` with body `{toolType: "soc2", score: null}`
> 3. Observe: 500 response, stack trace mentions `Cannot read
>    properties of null`.
> 4. Expected: 200 with normalized payload.
>
> If the bug is environmental (only repros under specific OS / Node
> version / time-of-day), document those preconditions.

1. ...
2. ...
3. Observe: ...
4. Expected: ...

## What to do

> Numbered steps for the FIX itself. Bug fixes have two deliverables
> — a code change AND a regression test that would have caught the
> bug if it had existed before. The agent does both, not just the
> first.
>
> Good: "Update the sort comparator in `server/storage.ts:228` to
> use `(a.score ?? 0) - (b.score ?? 0)`. Add `tests/storage.test.ts`
> case: 'sortLeads handles null scores' that constructs a fixture
> with `score: null` and asserts no throw."

1. Identify the buggy code (file + line).
2. Apply the minimal fix.
3. Add a regression test exercising the repro path.
4. Verify existing tests still pass (no behavior changes outside
   the bug surface).

## Constraints (don't violate these)

> **Bug-fix-specific hard rules:**
>
> - **A regression test is required.** The reviewer fails the task
>   if the diff has only a code change and no test. The test must
>   exercise the repro path from `## Reproduction steps`.
> - **Minimal fix only.** Bug fixes are not refactors — don't
>   rewrite the surrounding function. If you find a deeper issue,
>   note it in your summary; ship it as a follow-up task.
> - **No new features.** Don't "while I'm here" anything.
> - **No new dependencies.** Most bug fixes don't need them; if
>   yours does, surface that decision in the spec, don't sneak it.
> - Existing tests pass without modification.
> - **No `git push`.** Host owns the branch.
> - **Off-limits**: `.yukticastle/`, secrets, deployed prod files.

## Files to read

> Bug fixes need:
>
> - The buggy file (point at the function/line if you can).
> - The existing test file for that area (so the regression test
>   slots into the right place).
> - Any caller of the buggy code that might be affected.
> - The issue / ticket linked in `## Why this exists` (so the agent
>   sees the original report's full context).

- `path/to/buggy-file.ts`  (line ~XXX)
- `tests/path/to/test-file.test.ts`
- `path/to/caller-or-related.ts`

## Acceptance criteria

> **Bug-fix-specific:**
>
> - `npx tsc --noEmit` passes.
> - **The regression test exists** at the expected path AND
>   reproduces the original bug if the fix is reverted (the
>   reviewer can verify this by checking out before-fix and
>   running the test).
> - **All existing tests pass** without modification.
> - **The repro from `## Reproduction steps` no longer triggers the
>   bug.** Re-run the steps; observe expected behavior.
> - The exact set of files created/modified — for a bug fix, this
>   is usually 1 source file + 1 test file.
> - **No scope creep** — diff stat shows a minimal change. Reviewer
>   flags any unrelated edits.

- ...
- ...
- ...

## When done

Emit `<promise>COMPLETE</promise>` on its own line so the
orchestrator knows you're finished.

In your final summary, include:
- One-line root-cause description.
- The exact line(s) of the fix.
- The path of the new regression test.
- Any deeper issues you noticed but didn't fix (these become the
  body of follow-up bug tasks).
