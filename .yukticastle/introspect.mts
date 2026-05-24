// YuktiCastle introspect — `npm run agents:introspect`.
//
// Replaces the TODO-stub prompts that init-yukticastle.sh installs
// with project-specific drafts derived from the target's actual
// shape: framework, ORM, typecheck command, real .env files, manual
// migration convention, context docs, storage layer.
//
// The output still contains `<!-- TODO operator: confirm this -->`
// markers above each auto-detected section so operator review is
// still required — just on draft text instead of blank.
//
// Default refuses to overwrite existing prompts (idempotent —
// preserves operator customizations). `--force` regenerates.
//
// Usage:
//   npm run agents:introspect                 # render to .yukticastle/{implement,review}-prompt.md
//   npm run agents:introspect -- --force      # overwrite even if files exist
//   npm run agents:introspect -- --dry-run    # print what would be written
//   npm run agents:introspect -- --json       # emit structured fingerprint
//   npm run agents:introspect -- --quiet      # suppress diagnostic output
//
// Roadmap: docs/ENHANCEMENTS.md item #1. The biggest leverage on
// cross-project onboarding — the existing-project runbook calls
// hand-editing these prompts "load-bearing", and across 5–10
// projects this is where most "first run failed because reviewer
// didn't know our conventions" runs come from.
//
// No new deps — pure Node built-ins.

import { existsSync, readFileSync, readdirSync, writeFileSync, statSync } from "node:fs";
import { resolve as resolvePath, basename } from "node:path";

// ============================================================
// CLI flags
// ============================================================

const argv = process.argv.slice(2);
const FORCE = argv.includes("--force");
const DRY_RUN = argv.includes("--dry-run");
const JSON_MODE = argv.includes("--json");
const QUIET = argv.includes("--quiet");
const NO_COLOR =
    argv.includes("--no-color") ||
    !process.stdout.isTTY ||
    process.env.NO_COLOR !== undefined;

// ============================================================
// Tiny ANSI helpers (mirror doctor.mts/lint.mts)
// ============================================================

const c = {
    green: (s: string) => (NO_COLOR ? s : `\x1b[32m${s}\x1b[0m`),
    yellow: (s: string) => (NO_COLOR ? s : `\x1b[33m${s}\x1b[0m`),
    red: (s: string) => (NO_COLOR ? s : `\x1b[31m${s}\x1b[0m`),
    dim: (s: string) => (NO_COLOR ? s : `\x1b[2m${s}\x1b[0m`),
    bold: (s: string) => (NO_COLOR ? s : `\x1b[1m${s}\x1b[0m`),
};

function info(msg: string) {
    if (!QUIET && !JSON_MODE) console.log(c.dim(msg));
}
function warn(msg: string) {
    if (!QUIET && !JSON_MODE) console.warn(c.yellow(msg));
}

// ============================================================
// Project fingerprint — what we extract from the target
// ============================================================

type Framework =
    | "next"
    | "express"
    | "fastify"
    | "hono"
    | "elysia"
    | "remix"
    | "react-router"
    | "node";
type Orm =
    | "drizzle"
    | "prisma"
    | "kysely"
    | "typeorm"
    | "sequelize"
    | "mongoose"
    | null;
type UiLib = "react" | "vue" | "svelte" | "solid" | "preact" | null;
type TestRunner = "vitest" | "jest" | "mocha" | "playwright" | "node-test" | null;

interface PackageInfo {
    name: string;
    framework: Framework;
    orm: Orm;
    uiLib: UiLib;
    testRunner: TestRunner;
    /**
     * Detected typecheck command. `null` when the project has no
     * `typecheck` / `check` / `lint:tsc` npm script AND no
     * `tsconfig.json` at the project root — `npx tsc --noEmit`
     * would fail in that state. Render functions emit a TODO
     * instead of an invalid command when this is null.
     */
    typecheckCmd: string | null;
    smokeCmd: string | null;
}

interface Fingerprint {
    target: string;
    pkg: PackageInfo;
    contextDocs: string[];                // ["CLAUDE.md", "ARCHITECTURE.md", ...]
    realEnvFiles: string[];                // [".env", ".env.production", "env.txt"]
    storageLayer: string | null;           // "server/storage.ts" or null
    routesPath: string | null;             // "server/routes.ts" or null
    schemaPath: string | null;             // detected from drizzle.config / prisma/schema.prisma
    manualMigrationConvention: boolean;   // migrations/manual_*.sql exists
    migrationCommandToBlock: string | null; // db:push / db:migrate to block in prompts
    tsStrict: boolean;
}

// ============================================================
// Detection helpers
// ============================================================

function readJsonSafe(path: string): any | null {
    if (!existsSync(path)) return null;
    try {
        return JSON.parse(readFileSync(path, "utf8"));
    } catch {
        return null;
    }
}

function detectFramework(deps: Record<string, string>): Framework {
    if (deps["next"]) return "next";
    if (deps["@remix-run/node"] || deps["@remix-run/react"]) return "remix";
    if (deps["react-router"] && deps["react-router-dom"]) return "react-router";
    if (deps["express"]) return "express";
    if (deps["fastify"]) return "fastify";
    if (deps["hono"]) return "hono";
    if (deps["elysia"]) return "elysia";
    return "node";
}

function detectOrm(deps: Record<string, string>): Orm {
    if (deps["drizzle-orm"]) return "drizzle";
    if (deps["@prisma/client"] || deps["prisma"]) return "prisma";
    if (deps["kysely"]) return "kysely";
    if (deps["typeorm"]) return "typeorm";
    if (deps["sequelize"]) return "sequelize";
    if (deps["mongoose"]) return "mongoose";
    return null;
}

function detectUiLib(deps: Record<string, string>): UiLib {
    if (deps["react"]) return "react";
    if (deps["vue"]) return "vue";
    if (deps["svelte"]) return "svelte";
    if (deps["solid-js"]) return "solid";
    if (deps["preact"]) return "preact";
    return null;
}

function detectTestRunner(deps: Record<string, string>): TestRunner {
    if (deps["vitest"]) return "vitest";
    if (deps["jest"]) return "jest";
    if (deps["mocha"]) return "mocha";
    if (deps["@playwright/test"] || deps["playwright"]) return "playwright";
    return null;
}

function detectTypecheckCmd(
    scripts: Record<string, string>,
    target: string,
): string | null {
    // Operator may have a script that does more than tsc (e.g. eslint
    // + tsc). Prefer that over a bare tsc invocation.
    if (scripts["typecheck"]) return "npm run typecheck";
    if (scripts["check"]) return "npm run check";
    if (scripts["lint:tsc"]) return "npm run lint:tsc";
    // Bare `tsc --noEmit` only works when there's a tsconfig.json at
    // the project root. Recommending it without one yields a confusing
    // "no inputs were found in config file" error that the implementer
    // will spend iterations debugging. Return null → render functions
    // emit a TODO instead. Surfaced by gextrader bake test 2026-05-11.
    if (existsSync(resolvePath(target, "tsconfig.json"))) {
        return "npx tsc --noEmit";
    }
    return null;
}

function detectSmokeCmd(scripts: Record<string, string>): string | null {
    if (scripts["smoke"]) return "npm run smoke";
    if (scripts["test:smoke"]) return "npm run test:smoke";
    if (scripts["test"]) return "npm test";
    return null;
}

function detectContextDocs(target: string): string[] {
    const candidates = [
        "CLAUDE.md",
        "AGENTS.md",
        "ARCHITECTURE.md",
        "RUNBOOK.md",
        "SESSION_MEMORY.md",
        "GEMINI.md",
    ];
    return candidates.filter((f) => existsSync(resolvePath(target, f)));
}

function detectRealEnvFiles(target: string): string[] {
    // Real `.env*` files (the operator's project secrets). NOT
    // `.yukticastle/.env` — that's the YuktiCastle's own auth env.
    const out: string[] = [];
    for (const f of readdirSync(target)) {
        if (f === ".env" || /^\.env\..+/.test(f)) out.push(f);
    }
    // Verity uses env.txt in addition to .env — capture if present.
    if (existsSync(resolvePath(target, "env.txt"))) out.push("env.txt");
    return out;
}

function detectStorageLayer(target: string): string | null {
    // Common patterns observed across Yuktiv8 projects.
    const candidates = [
        "server/storage.ts",
        "src/storage.ts",
        "src/lib/storage.ts",
        "src/server/storage.ts",
        "lib/storage.ts",
        "app/storage.ts",
        "app/lib/storage.ts",
    ];
    return candidates.find((p) => existsSync(resolvePath(target, p))) ?? null;
}

function detectRoutesPath(target: string): string | null {
    // Two project shapes seen in practice:
    //   - Monolithic file: `server/routes.ts` etc. (yukticastle template default,
    //     AccessQuint, sandcastle-demo-auth)
    //   - Directory of feature files: `server/api/<feature>.ts` (verity)
    //     or `server/routes/<feature>.ts`, optionally under a `src/` layout.
    // Mirror `context.mts`'s detection so the implementer prompt and the
    // context pack agree on what gets surfaced — diverging caused the verity
    // bake-test miss documented in ENHANCEMENTS.md Tier 6 #36.
    const fileCandidates = [
        "server/routes.ts",
        "src/routes.ts",
        "src/server/routes.ts",
        "lib/routes.ts",
        "app/routes.ts",
    ];
    const fileHit = fileCandidates.find((p) => existsSync(resolvePath(target, p)));
    if (fileHit) return fileHit;

    const dirCandidates = [
        "server/api",
        "server/routes",
        "src/server/api",
        "src/server/routes",
    ];
    for (const relDir of dirCandidates) {
        const absDir = resolvePath(target, relDir);
        if (!existsSync(absDir)) continue;
        try {
            const hasTsFile = readdirSync(absDir).some((f) => /\.(t|j)s$/.test(f));
            if (hasTsFile) return relDir;
        } catch {
            // best-effort — skip unreadable dirs
        }
    }
    return null;
}

function detectSchemaPath(target: string, orm: Orm): string | null {
    if (orm === "prisma") {
        const p = "prisma/schema.prisma";
        return existsSync(resolvePath(target, p)) ? p : null;
    }
    if (orm === "drizzle") {
        // Drizzle puts the schema wherever the operator wants. Look for
        // the config to find the schema path; fall back to common
        // conventions if config not present.
        const drizzleConfigCandidates = [
            "drizzle.config.ts",
            "drizzle.config.js",
            "drizzle.config.mjs",
        ];
        for (const cfg of drizzleConfigCandidates) {
            const p = resolvePath(target, cfg);
            if (existsSync(p)) {
                const content = readFileSync(p, "utf8");
                // Loose regex: match `schema:` followed by a string. Good
                // enough; operator confirms.
                const m = /schema\s*:\s*["'`]([^"'`]+)["'`]/.exec(content);
                if (m) return m[1];
            }
        }
        const fallbacks = [
            "shared/schema.ts",
            "shared/models/cms.ts",
            "src/db/schema.ts",
            "server/db/schema.ts",
        ];
        return fallbacks.find((p) => existsSync(resolvePath(target, p))) ?? null;
    }
    return null;
}

function detectManualMigrationConvention(target: string): boolean {
    const dir = resolvePath(target, "migrations");
    if (!existsSync(dir)) return false;
    try {
        // Three naming conventions seen in practice — mirrored from
        // `context.mts` to keep the implementer prompt's migration-policy
        // block in sync with the context pack:
        //   - `manual_<feature>.sql`     (yukticastle template default, AccessQuint)
        //   - `<NNNN>_manual_<...>.sql`  (verity alongside drizzle-kit auto migrations)
        //   - `<NNNN>_manual.sql`        (verity leading-digit-only)
        return readdirSync(dir).some(
            (f) => /(?:^|_)manual(?:_|\.)/.test(f) && /\.sql$/.test(f),
        );
    } catch {
        return false;
    }
}

function detectMigrationCommand(orm: Orm, scripts: Record<string, string>): string | null {
    if (scripts["db:push"]) return "npm run db:push";
    if (scripts["db:migrate"]) return "npm run db:migrate";
    if (orm === "drizzle") return "npm run db:push";
    if (orm === "prisma") return "npx prisma migrate deploy";
    return null;
}

function detectTsStrict(target: string): boolean {
    const p = resolvePath(target, "tsconfig.json");
    if (!existsSync(p)) return false;
    try {
        // tsconfig.json often has comments; strip them roughly.
        const raw = readFileSync(p, "utf8")
            .replace(/\/\*[\s\S]*?\*\//g, "")
            .replace(/(^|[^:])\/\/.*$/gm, "$1");
        const json = JSON.parse(raw);
        return json?.compilerOptions?.strict === true;
    } catch {
        return false;
    }
}

// ============================================================
// Build the fingerprint
// ============================================================

function buildFingerprint(target: string): Fingerprint | null {
    const pkgPath = resolvePath(target, "package.json");
    const pkg = readJsonSafe(pkgPath);
    if (!pkg) {
        console.error(c.red(`No package.json at ${target} — not a Node project.`));
        return null;
    }
    const deps: Record<string, string> = {
        ...(pkg.dependencies ?? {}),
        ...(pkg.devDependencies ?? {}),
    };
    const scripts: Record<string, string> = pkg.scripts ?? {};

    const orm = detectOrm(deps);
    const fp: Fingerprint = {
        target,
        pkg: {
            name: pkg.name ?? basename(target),
            framework: detectFramework(deps),
            orm,
            uiLib: detectUiLib(deps),
            testRunner: detectTestRunner(deps),
            typecheckCmd: detectTypecheckCmd(scripts, target),
            smokeCmd: detectSmokeCmd(scripts),
        },
        contextDocs: detectContextDocs(target),
        realEnvFiles: detectRealEnvFiles(target),
        storageLayer: detectStorageLayer(target),
        routesPath: detectRoutesPath(target),
        schemaPath: detectSchemaPath(target, orm),
        manualMigrationConvention: detectManualMigrationConvention(target),
        migrationCommandToBlock: detectMigrationCommand(orm, scripts),
        tsStrict: detectTsStrict(target),
    };
    return fp;
}

// ============================================================
// Render prompts
// ============================================================

const TODO_MARKER = "<!-- TODO operator: confirm this -->";

function bullet(s: string): string {
    return `- ${s}`;
}

function stackBullets(fp: Fingerprint): string[] {
    const b: string[] = [];
    b.push(`Node + TypeScript${fp.tsStrict ? " (strict mode)" : ""}`);
    if (fp.pkg.framework !== "node") b.push(`Framework: ${fp.pkg.framework}`);
    if (fp.pkg.uiLib) b.push(`UI library: ${fp.pkg.uiLib} (do NOT introduce alternatives)`);
    if (fp.pkg.orm) b.push(`ORM: ${fp.pkg.orm}`);
    if (fp.pkg.testRunner) b.push(`Test runner: ${fp.pkg.testRunner}`);
    return b.map(bullet);
}

function conventionBullets(fp: Fingerprint): string[] {
    const b: string[] = [];
    if (fp.storageLayer)
        b.push(
            `Storage layer at \`${fp.storageLayer}\` — every DB query goes through here, not raw ORM in routes.`,
        );
    if (fp.routesPath)
        b.push(
            `Route registration at \`${fp.routesPath}\` — match the existing pattern.`,
        );
    if (fp.schemaPath)
        b.push(`Schema lives at \`${fp.schemaPath}\` — co-locate types + validation.`);
    if (b.length === 0)
        b.push(
            "_(no project-specific conventions auto-detected — operator should fill these in based on CLAUDE.md / ARCHITECTURE.md)_",
        );
    return b.map(bullet);
}

function offLimitsBullets(fp: Fingerprint): string[] {
    const b: string[] = [];
    b.push("`.yukticastle/`, `.git/`, `node_modules/`, `dist/`, `build/`");
    if (fp.realEnvFiles.length > 0) {
        b.push(
            `Real env files (contain product secrets): ${fp.realEnvFiles.map((f) => `\`${f}\``).join(", ")}`,
        );
    }
    if (fp.manualMigrationConvention) {
        b.push(
            "Existing `migrations/manual_*.sql` files (read for context, but don't edit prior ones — write a new `manual_<feature>.sql`)",
        );
    }
    return b.map(bullet);
}

function migrationPolicyBlock(fp: Fingerprint): string {
    if (fp.manualMigrationConvention) {
        const blocked = fp.migrationCommandToBlock
            ? `\`${fp.migrationCommandToBlock}\``
            : "automatic migration commands";
        return `## DB / migration policy — CRITICAL

${TODO_MARKER}

This is a PRODUCTION database. The project uses the
**manual-migration convention** (detected: \`migrations/manual_*.sql\`
files present).

- DO NOT run ${blocked} or any equivalent.
- WRITE migrations as \`migrations/manual_<feature>.sql\`.
- The operator applies them manually post-merge.
- If a schema change is needed:
  1. Update the schema file${fp.schemaPath ? ` (\`${fp.schemaPath}\`)` : ""}.
  2. Write SQL to \`migrations/manual_<feature>.sql\`.
  3. Note in the commit: "DB migration in manual_*.sql — apply manually".`;
    }
    if (fp.pkg.orm) {
        return `## DB / migration policy

${TODO_MARKER}

Detected ORM: \`${fp.pkg.orm}\`. No manual-migration convention
detected. **Decide before the first run whether the agent should
apply migrations or only write them:**

- **If production**: forbid \`${fp.migrationCommandToBlock ?? "the migration command"}\`
  in \`## Hard rules\` below; require migrations to be written as
  files only and applied by the operator post-merge.
- **If greenfield/dev-only**: allow the agent to run migrations,
  but list them as part of acceptance criteria so the reviewer
  verifies the resulting schema.`;
    }
    return `## DB / migration policy

${TODO_MARKER}

No ORM detected — if this project doesn't have a database, delete
this section. Otherwise add migration policy specific to your stack.`;
}

function readFirstSection(target: string, doc: string): string {
    const path = resolvePath(target, doc);
    if (!existsSync(path)) return "";
    const lines = readFileSync(path, "utf8").split(/\r?\n/);
    // Take everything after the first H1 up to the second H1 or
    // first H2 — captures the project's "what this is" framing.
    let started = false;
    const out: string[] = [];
    for (const line of lines) {
        if (/^#\s+/.test(line)) {
            if (!started) {
                started = true;
                continue;
            } else {
                break;
            }
        }
        if (/^##\s+/.test(line) && started) break;
        if (started) out.push(line);
    }
    return out.join("\n").trim();
}

function projectContextBlock(fp: Fingerprint): string {
    if (fp.contextDocs.length === 0) {
        return `> No project context docs detected (\`CLAUDE.md\`, \`AGENTS.md\`,
> \`ARCHITECTURE.md\`, \`RUNBOOK.md\`). The agent will operate
> without project-specific framing, which usually doubles
> iteration count. Consider adding at least a CLAUDE.md.`;
    }
    return `## Read these first

${TODO_MARKER}

${fp.contextDocs.map((d) => bullet(`\`${d}\``)).join("\n")}`;
}

function renderImplementPrompt(fp: Fingerprint): string {
    const stack = stackBullets(fp);
    const conventions = conventionBullets(fp);
    const offLimits = offLimitsBullets(fp);
    return `# Implementer — ${fp.pkg.name}

You are implementing a focused change to **${fp.pkg.name}**. ${
        fp.contextDocs.includes("CLAUDE.md") || fp.realEnvFiles.length > 0
            ? "This is a PRODUCTION codebase — caution matters more than speed."
            : "Keep changes scoped, focused, and reversible."
    }

## Task

{{TASK_DESCRIPTION}}

## Project context

> Auto-populated by \`npm run agents:context\` (storage layer
> excerpts, ADRs, manual migrations, public route inventory).
> Empty if the operator hasn't run it yet — that's fine, the
> agent falls back to discovery from \`## Read these first\` below.

{{CONTEXT}}

## Recent reviewer patterns

> Auto-populated from \`.yukticastle/learnings.jsonl\` — previous
> reviewer fix-ups the implementer should now avoid. Empty on first
> run or when the reviewer has had nothing to fix; this section is
> purely additive (skip an iteration by not making the same mistake).

{{RECENT_PATTERNS}}

${projectContextBlock(fp)}

## Stack (do NOT change)

${TODO_MARKER}

${stack.join("\n")}

## Project conventions (NON-NEGOTIABLE)

${TODO_MARKER}

${conventions.join("\n")}

${migrationPolicyBlock(fp)}

## Off-limits files

${TODO_MARKER}

${offLimits.join("\n")}

## What to do

1. Read the files listed above${fp.contextDocs.length > 0 ? ` (especially ${fp.contextDocs.map((d) => `\`${d}\``).join(", ")})` : ""}.
2. Make the change. Keep it focused — one task, one set of related commits.
3. **Typecheck.** ${
        fp.pkg.typecheckCmd
            ? `Run \`${fp.pkg.typecheckCmd}\`. Must pass.`
            : `${TODO_MARKER} No typecheck script and no \`tsconfig.json\` detected at project root. Add a \`tsconfig.json\` (or a \`"typecheck": "..."\` npm script) before running agent tasks — without one, the agent has no way to verify its work compiles.`
    }
4. **Runtime smoke — two layers.** Most "passes typecheck, breaks in prod" bugs get caught here. See YUKTICASTLE-GUIDE §15a for the 3-layer pattern.

   **4a. Dev-mode smoke (required for any new server-side module):**
   \`\`\`
   npx tsx --eval 'import("./path/to/new-module.ts").then(m => m.representativeFn()).then(r => console.log("ok:", JSON.stringify(r).slice(0, 200))).catch(e => { console.error("FAIL:", e); process.exit(1); })'
   \`\`\`

   **4b. Built-bundle smoke (required when 4a applies AND this project deploys a bundled artifact):**

   ${TODO_MARKER} Replace this paragraph with the build + post-build smoke
   commands for THIS project's bundler output. Two shapes seen in practice
   — pick whichever matches the project's build script + output format:

   - **CJS bundle** (esbuild \`--format=cjs\` / webpack / swc default):
     \`\`\`
     npm run build:<script>    # whichever bundle script is in package.json (e.g. build:api)
     node -e 'const m = require("./path/to/output.cjs"); m.representativeFn().then(r => console.log("ok:", JSON.stringify(r).slice(0, 200))).catch(e => { console.error("FAIL:", e); process.exit(1); })'
     \`\`\`

   - **ESM bundle** (esbuild \`--format=esm\` / Node \`"type": "module"\`):
     \`\`\`
     npm run build:<script>    # e.g. build:vercel for an esbuild --format=esm step
     node --input-type=module -e 'const m = await import("./path/to/output.mjs"); m.representativeFn().then(r => console.log("ok:", JSON.stringify(r).slice(0, 200))).catch(e => { console.error("FAIL:", e); process.exit(1); })'
     \`\`\`

   If this project doesn't ship a bundled server module${
        fp.pkg.framework === "next" || fp.pkg.framework === "remix" ||
        fp.pkg.framework === "react-router"
            ? ` (it doesn't — ${fp.pkg.framework} bundles the whole app for you, so 4b doesn't apply and \`npm run build\` is the gate)`
            : ""
   }, skip 4b — the framework's production build is the gate. **Don't ship a literal \`require()\` against a path that doesn't exist; that hides the actual production-format risk this gate is meant to catch.**

   **When 4b applies:** new module pulls in a NEW top-level dep (or transitively reaches one) AND this project deploys via esbuild/webpack/swc bundling. Pure refactors of existing modules don't need 4b. Empirical cost: ~5s once the operator's filled in the commands above. The AccessQuint shakedown caught 3 production bugs at exactly this layer that all preceding gates (tsc, esbuild build success, dev-mode smoke) missed — see YUKTICASTLE-GUIDE §15a.${
        fp.pkg.smokeCmd
            ? `\n5. **Project smoke** if applicable: \`${fp.pkg.smokeCmd}\``
            : ""
    }
${fp.pkg.smokeCmd ? "6" : "5"}. Commit with a clear message. Multiple commits are fine if they're focused.
${fp.pkg.smokeCmd ? "7" : "6"}. Emit the literal token \`<promise>COMPLETE</promise>\` on its own line so the orchestrator knows you're done.

## Hard rules

- NO \`git push\`. NO secrets in commits. NO scope creep.
- Don't apply DB migrations to production unless the migration policy section above says you can.
- Commit in focused chunks. One logical unit per commit.
`;
}

function renderReviewPrompt(fp: Fingerprint): string {
    const conventionChecks: string[] = [];
    if (fp.storageLayer)
        conventionChecks.push(
            `**Storage layer**: new DB queries go through \`${fp.storageLayer}\`, not raw ORM in routes.`,
        );
    if (fp.routesPath)
        conventionChecks.push(
            `**Routes**: new endpoints registered in \`${fp.routesPath}\` matching the existing pattern.`,
        );
    if (fp.schemaPath)
        conventionChecks.push(
            `**Schema**: changes co-located in \`${fp.schemaPath}\`.`,
        );
    if (fp.pkg.uiLib)
        conventionChecks.push(
            `**UI library**: ${fp.pkg.uiLib} only — flag any imports of alternative frameworks.`,
        );
    if (fp.tsStrict)
        conventionChecks.push(
            "**Type safety** (strict mode): no `any`, no `as unknown as`, no `@ts-ignore`.",
        );
    if (conventionChecks.length === 0) {
        conventionChecks.push(
            "_(no project-specific convention checks auto-detected — operator should fill these in)_",
        );
    }

    const migrationCheck = fp.manualMigrationConvention
        ? `\n4. **Migration policy**: any schema change MUST land in \`migrations/manual_*.sql\`. Block on any \`${fp.migrationCommandToBlock ?? "automatic migration"}\` invocation. Schema changes outside the manual file fail review.\n`
        : "";

    return `# Reviewer — ${fp.pkg.name}

You are reviewing the implementer's work on branch \`{{BRANCH}}\`.

## Original task

{{TASK_DESCRIPTION}}

## What to read

- The diff: !\`git diff main..{{BRANCH}}\`
- The commits: !\`git log main..{{BRANCH}} --oneline\`
- The implementer's most recent commit message: !\`git log -1 --format=%B\`
${fp.contextDocs.map((d) => `- \`${d}\` for project conventions`).join("\n")}

## Project context

> Auto-populated by \`npm run agents:context\` (storage layer
> excerpts, ADRs, manual migrations, public route inventory).
> Empty if the operator hasn't run it yet — falls back to discovery
> from the diff + ${fp.contextDocs.length > 0 ? fp.contextDocs.map((d) => `\`${d}\``).join("/") : "context docs"} above.

{{CONTEXT}}

## Recent reviewer patterns

> Auto-populated from \`.yukticastle/learnings.jsonl\` — previous
> reviewer fix-ups on this codebase. Use them to spot recurring
> mistakes the implementer may have repeated. Empty on first run
> or when prior reviewers had nothing to fix.

{{RECENT_PATTERNS}}

## What to check

1. **Correctness.** Does the change do what the task asked?
2. **Typecheck.** ${
        fp.pkg.typecheckCmd
            ? `Run \`${fp.pkg.typecheckCmd}\`. **Must pass.** If it fails, fix it on the branch directly.`
            : `${TODO_MARKER} No typecheck command detected (no \`typecheck\`/\`check\`/\`lint:tsc\` script, no \`tsconfig.json\`). Skip this check or fail the review and ask the operator to wire one up.`
    }
3. **Scope.** Are unrelated files modified? Flag scope creep.${migrationCheck}
${fp.manualMigrationConvention ? "5" : "4"}. **Conventions** ${TODO_MARKER}:

${conventionChecks.map((s) => `   - ${s}`).join("\n")}

${fp.manualMigrationConvention ? "6" : "5"}. **Secrets**: nothing real committed (\`.env\`, API keys, DB URLs).
${fp.manualMigrationConvention ? "7" : "6"}. **Project-specific contracts** ${TODO_MARKER}:

   _(operator should add ADR-specific or architecture-specific
   checks here that the implementer must respect)_

## Decision rules

- Typecheck fails → fix on branch (don't just flag).
- Convention violation → fix on branch.
${fp.manualMigrationConvention ? "- Migration policy violation → fix on branch (rewrite as `manual_*.sql`).\n" : ""}- Off-limits file modified → revert it.
- Style nits → leave a brief note in your final summary.

## What you do

1. Read the diff and commit messages.
2. Check each item above.
3. Fix issues on the branch with focused commits.
4. End with a 4-8 line summary.
5. Emit \`<promise>COMPLETE</promise>\` on its own line.

## Hard rules

- Don't \`git push\` or merge. The host process owns the branch.
${fp.manualMigrationConvention ? "- Don't apply DB migrations to production. The operator does that manually post-merge.\n" : ""}`;
}

// ============================================================
// Main
// ============================================================

const target = resolvePath(process.cwd());
const fp = buildFingerprint(target);

if (!fp) {
    process.exit(2);
}

if (JSON_MODE) {
    console.log(JSON.stringify(fp, null, 2));
    process.exit(0);
}

// Diagnostic summary
if (!QUIET) {
    console.log(c.bold(`Introspecting:`) + ` ${target}`);
    console.log(`  ${c.dim("project:")}     ${fp.pkg.name}`);
    console.log(`  ${c.dim("framework:")}   ${fp.pkg.framework}`);
    console.log(`  ${c.dim("orm:")}         ${fp.pkg.orm ?? "—"}`);
    console.log(`  ${c.dim("ui lib:")}      ${fp.pkg.uiLib ?? "—"}`);
    console.log(`  ${c.dim("test runner:")}  ${fp.pkg.testRunner ?? "—"}`);
    console.log(
        `  ${c.dim("typecheck:")}   ${fp.pkg.typecheckCmd ?? c.yellow("(none — no script, no tsconfig.json)")}`,
    );
    console.log(`  ${c.dim("smoke:")}       ${fp.pkg.smokeCmd ?? "—"}`);
    console.log(
        `  ${c.dim("storage:")}     ${fp.storageLayer ?? c.yellow("(not detected — operator should fill)")}`,
    );
    console.log(
        `  ${c.dim("routes:")}      ${fp.routesPath ?? c.yellow("(not detected)")}`,
    );
    console.log(
        `  ${c.dim("schema:")}      ${fp.schemaPath ?? c.yellow("(not detected)")}`,
    );
    console.log(
        `  ${c.dim("manual migrations:")} ${fp.manualMigrationConvention ? c.green("yes (manual_*.sql convention)") : c.dim("no")}`,
    );
    console.log(
        `  ${c.dim("context docs:")} ${fp.contextDocs.length > 0 ? fp.contextDocs.join(", ") : c.yellow("(none — recommend at least CLAUDE.md)")}`,
    );
    console.log(
        `  ${c.dim("real env files:")} ${fp.realEnvFiles.length > 0 ? fp.realEnvFiles.join(", ") : c.dim("(none)")}`,
    );
    console.log(`  ${c.dim("ts strict:")}   ${fp.tsStrict ? c.green("yes") : c.yellow("no")}`);
    console.log("");
}

const implementPath = resolvePath(target, ".yukticastle/implement-prompt.md");
const reviewPath = resolvePath(target, ".yukticastle/review-prompt.md");

const implementContent = renderImplementPrompt(fp);
const reviewContent = renderReviewPrompt(fp);

if (DRY_RUN) {
    console.log(c.bold("=== implement-prompt.md ==="));
    console.log(implementContent);
    console.log("");
    console.log(c.bold("=== review-prompt.md ==="));
    console.log(reviewContent);
    process.exit(0);
}

// Idempotency: refuse to overwrite without --force.
const skipped: string[] = [];
const written: string[] = [];

function maybeWrite(path: string, content: string) {
    const exists = existsSync(path);
    if (exists && !FORCE) {
        skipped.push(path);
        return;
    }
    writeFileSync(path, content);
    written.push(path);
}

maybeWrite(implementPath, implementContent);
maybeWrite(reviewPath, reviewContent);

for (const p of written) {
    info(c.green(`✓ wrote ${p.replace(target + "/", "")}`));
}
for (const p of skipped) {
    warn(`↷ skipped ${p.replace(target + "/", "")} (already exists; use --force to overwrite)`);
}

if (written.length === 0 && skipped.length > 0) {
    info(
        `\nNo prompts regenerated. Re-run with ${c.bold("--force")} to overwrite, or ${c.bold("--dry-run")} to preview.`,
    );
}

process.exit(0);
