# <E2E: which user-visible flow gets covered>

> 2–4 sentence summary. State the user-visible flow this task adds
> coverage for, AND why it deserves a regression check now (e.g. "we
> just shipped this and want to pin it", or "a bug here would silently
> drop revenue").
>
> Example: "Add e2e coverage for `/admin/leads` filter+export. Pins
> the regression-fix from #117 (export button was a 404 on prod
> 2026-05-09). Single spec, two assertions: filter narrows the list,
> CSV download returns 200 with non-empty body."
>
> H1 becomes the branch slug. E2E tasks: prefix "Add e2e for X" /
> "Pin regression for Y".

## Why this exists

> 1–3 short paragraphs. E2E tasks usually come from one of:
>
> - A specific production bug just got fixed and you want a
>   regression test pinning the behavior so it can't drift back.
> - A user-facing flow that's load-bearing for the business has
>   zero coverage and an outage would be silent.
> - A new feature shipped and you want a smoke that runs against
>   every deployed preview from now on.
>
> Linking to the incident / issue / PR that motivated this saves
> the agent guessing about scope.

## What to do

> Numbered, concrete. E2E tasks have an unusually constrained shape:
> ONE spec file per task; ZERO modifications to production code; use
> the existing `authedPage` fixture from `tests/fixtures/auth.ts`
> (or a documented adapter for non-passport auth shapes — see
> `tests/README.md`).
>
> Good: "Create `tests/smoke/leads-export.spec.ts`. Use
> `authedPage`. Three assertions: (1) GET `/admin/leads` returns
> 200 and lists at least one row, (2) filtering by `priority=hot`
> reduces the row count, (3) clicking 'Export CSV' triggers a
> download where the response body length > 100 bytes."
>
> Bad: "Add e2e for the leads page."

1. Create `tests/smoke/<feature>.spec.ts` (copy from
   `tests/smoke/_template.spec.ts` as a starting structure).
2. Replace `FEATURE_NAME` / `FEATURE_PATH` / `FEATURE_ENDPOINT`
   placeholders with the real values for this task.
3. Add the specific assertions for THIS flow. List them explicitly
   in the spec — each `test()` block has one user-observable
   contract.
4. Run locally against `npm run dev` AND against a deployed preview
   URL (if `BASE_URL` env is set in the test env) — see Acceptance
   criteria.

## Constraints (don't violate these)

> **E2E-specific hard rules. Reviewer enforces.**
>
> - **Test-only changes.** This task adds files under `tests/`
>   ONLY. No edits to production code. If the spec reveals a bug,
>   note it in your summary as a follow-up — bug fixes belong in a
>   separate `_template.bugfix.md` task.
> - **Use the existing `authedPage` fixture.** Don't roll your own
>   login flow per-spec; that drifts. If the existing fixture
>   doesn't fit your auth shape, update it (still test-only) AND
>   document in `tests/README.md` why.
> - **Bot account only.** Specs that need login use the `e2e-bot`
>   account provisioned via `script/create-e2e-bot.ts`. Never a
>   seed user or a real human admin account.
> - **No production-data assumptions.** Don't assert specific row
>   IDs, names, or counts that would drift over time. Assert
>   shape (>0 rows, response is JSON, button exists) not content.
> - **No flaky waits.** Use Playwright's auto-wait. If a test
>   needs `page.waitForTimeout(...)`, that's a sign the spec is
>   wrong — fix it instead of pinning the flakiness.
> - **No `git push`.** Host owns the branch.

## Files to read

> E2E tasks need:
>
> - `tests/fixtures/auth.ts` — to understand the `authedPage`
>   fixture's contract (what the test gets pre-logged-in as).
> - `tests/smoke/_template.spec.ts` — the proven shape to copy.
> - `tests/README.md` — convention doc; auth-shape adapter notes.
> - The closest existing spec to the one being added (look at
>   `tests/smoke/<sibling-feature>.spec.ts`).
> - Any production code path the spec exercises (read-only — to
>   know what the spec is asserting against).

- `tests/fixtures/auth.ts`
- `tests/smoke/_template.spec.ts`
- `tests/README.md`
- `tests/smoke/<closest-sibling>.spec.ts`
- `<production code being exercised — read-only>`

## Acceptance criteria

> **E2E-specific. Reviewer runs each.**
>
> - `npx tsc --noEmit` passes (specs are typechecked too).
> - `npm run e2e` exits 0 against `npm run dev` (local).
> - `BASE_URL=<preview-url> npm run e2e -- tests/smoke/<feature>.spec.ts`
>   exits 0 against a deployed preview (proves the suite works
>   in CI, not just on the operator's machine). If the operator
>   doesn't have a preview URL handy, the reviewer can substitute
>   `BASE_URL=http://localhost:3000` and skip this step — but the
>   spec should still be CI-clean by design.
> - The new spec file exists at the exact path listed in
>   `## What to do`. Reviewer flags any spec landing elsewhere.
> - No diffs outside `tests/` (no production code changed).
> - Each `test()` block has a clear contract — the test name reads
>   like a sentence describing what's being verified.

- ...
- ...
- ...

## When done

Emit `<promise>COMPLETE</promise>` on its own line so the
orchestrator knows you're finished.

In your final summary, include:
- The spec path you created.
- How many `test()` blocks the file has.
- Local pass/fail count (`X/Y passed`).
- Preview-URL pass/fail count if you ran against one.
- Any production bug the spec surfaced but did NOT fix (these
  become follow-up `_template.bugfix.md` tasks).
