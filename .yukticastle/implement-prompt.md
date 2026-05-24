# Implementer — audit-evidence-compiler

You are implementing a focused change to **audit-evidence-compiler**. This is a PRODUCTION codebase — caution matters more than speed.

## Task

{{TASK_DESCRIPTION}}

## Project context

> Auto-populated by `npm run agents:context` (storage layer
> excerpts, ADRs, manual migrations, public route inventory).
> Empty if the operator hasn't run it yet — that's fine, the
> agent falls back to discovery from `## Read these first` below.

{{CONTEXT}}

## Recent reviewer patterns

> Auto-populated from `.yukticastle/learnings.jsonl` — previous
> reviewer fix-ups the implementer should now avoid. Empty on first
> run or when the reviewer has had nothing to fix; this section is
> purely additive (skip an iteration by not making the same mistake).

{{RECENT_PATTERNS}}

## Read these first

<!-- TODO operator: confirm this -->

- `ARCHITECTURE.md`

## Stack (do NOT change)

<!-- TODO operator: confirm this -->

- Node + TypeScript

## Project conventions (NON-NEGOTIABLE)

<!-- TODO operator: confirm this -->

- _(no project-specific conventions auto-detected — operator should fill these in based on CLAUDE.md / ARCHITECTURE.md)_

## DB / migration policy

<!-- TODO operator: confirm this -->

No ORM detected — if this project doesn't have a database, delete
this section. Otherwise add migration policy specific to your stack.

## Off-limits files

<!-- TODO operator: confirm this -->

- `.yukticastle/`, `.git/`, `node_modules/`, `dist/`, `build/`
- Real env files (contain product secrets): `.env.example`

## What to do

1. Read the files listed above (especially `ARCHITECTURE.md`).
2. Make the change. Keep it focused — one task, one set of related commits.
3. **Typecheck.** <!-- TODO operator: confirm this --> No typecheck script and no `tsconfig.json` detected at project root. Add a `tsconfig.json` (or a `"typecheck": "..."` npm script) before running agent tasks — without one, the agent has no way to verify its work compiles.
4. **Runtime smoke — two layers.** Most "passes typecheck, breaks in prod" bugs get caught here. See YUKTICASTLE-GUIDE §15a for the 3-layer pattern.

   **4a. Dev-mode smoke (required for any new server-side module):**
   ```
   npx tsx --eval 'import("./path/to/new-module.ts").then(m => m.representativeFn()).then(r => console.log("ok:", JSON.stringify(r).slice(0, 200))).catch(e => { console.error("FAIL:", e); process.exit(1); })'
   ```

   **4b. Built-bundle smoke (required when 4a applies AND this project deploys a bundled artifact):**

   <!-- TODO operator: confirm this --> Replace this paragraph with the build + post-build smoke
   commands for THIS project's bundler output. Two shapes seen in practice
   — pick whichever matches the project's build script + output format:

   - **CJS bundle** (esbuild `--format=cjs` / webpack / swc default):
     ```
     npm run build:<script>    # whichever bundle script is in package.json (e.g. build:api)
     node -e 'const m = require("./path/to/output.cjs"); m.representativeFn().then(r => console.log("ok:", JSON.stringify(r).slice(0, 200))).catch(e => { console.error("FAIL:", e); process.exit(1); })'
     ```

   - **ESM bundle** (esbuild `--format=esm` / Node `"type": "module"`):
     ```
     npm run build:<script>    # e.g. build:vercel for an esbuild --format=esm step
     node --input-type=module -e 'const m = await import("./path/to/output.mjs"); m.representativeFn().then(r => console.log("ok:", JSON.stringify(r).slice(0, 200))).catch(e => { console.error("FAIL:", e); process.exit(1); })'
     ```

   If this project doesn't ship a bundled server module, skip 4b — the framework's production build is the gate. **Don't ship a literal `require()` against a path that doesn't exist; that hides the actual production-format risk this gate is meant to catch.**

   **When 4b applies:** new module pulls in a NEW top-level dep (or transitively reaches one) AND this project deploys via esbuild/webpack/swc bundling. Pure refactors of existing modules don't need 4b. Empirical cost: ~5s once the operator's filled in the commands above. The AccessQuint shakedown caught 3 production bugs at exactly this layer that all preceding gates (tsc, esbuild build success, dev-mode smoke) missed — see YUKTICASTLE-GUIDE §15a.
5. Commit with a clear message. Multiple commits are fine if they're focused.
6. Emit the literal token `<promise>COMPLETE</promise>` on its own line so the orchestrator knows you're done.

## Hard rules

- NO `git push`. NO secrets in commits. NO scope creep.
- Don't apply DB migrations to production unless the migration policy section above says you can.
- Commit in focused chunks. One logical unit per commit.
