// YuktiCastle context pack — `npm run agents:context`.
//
// Walks the project once and emits `.yukticastle/context.md` — a
// compact reference that the implementer prompt loads via `{{CONTEXT}}`.
// Cuts the agent's discovery iterations roughly in half on existing-
// project tasks: the implementer doesn't have to grep the tree for
// the storage layer, the route registry, or the latest ADR — it's
// all already in its prompt context.
//
// Sections (each <2KB to stay under the 8KB token budget):
//   - Key file paths with first-30-lines excerpts (storage, routes, schema)
//   - ADR list (docs/decisions/*.md titles + first sentence)
//   - Manual migrations listing
//   - Public route inventory (Express + Next.js App Router)
//
// Idempotent: re-running overwrites `.yukticastle/context.md`. Operator
// regenerates after significant project changes; otherwise the file
// is cached and reused across runs.
//
// Roadmap: docs/ENHANCEMENTS.md item #3a. The companion #3b (learnings
// ledger — reviewer fix-ups captured as patterns for future runs) is
// a follow-up; out of scope for this PR.
//
// Usage:
//   npm run agents:context              # write .yukticastle/context.md
//   npm run agents:context -- --dry-run # print what would be written
//   npm run agents:context -- --json    # emit structured detection JSON
//   npm run agents:context -- --quiet   # suppress diagnostic summary
//
// No new deps — pure Node built-ins.

import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { resolve as resolvePath, basename, join, relative } from "node:path";

// ============================================================
// CLI
// ============================================================

const argv = process.argv.slice(2);
const DRY_RUN = argv.includes("--dry-run");
const JSON_MODE = argv.includes("--json");
const QUIET = argv.includes("--quiet");
const NO_COLOR =
    argv.includes("--no-color") ||
    !process.stdout.isTTY ||
    process.env.NO_COLOR !== undefined;

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

// ============================================================
// Detection
// ============================================================

const TARGET = resolvePath(process.cwd());

interface FileExcerpt {
    label: string;        // "Storage layer"
    path: string;         // relative path from TARGET
    excerpt: string;      // first ~30 lines
    truncated: boolean;
}

interface AdrEntry {
    path: string;
    title: string;
    summary: string;      // first non-empty paragraph (capped)
    sortKey: string;
}

/** Top-level project docs found at `docs/*.md` when no `docs/decisions/`. */
interface ProjectDocEntry {
    path: string;
    title: string;
    summary: string;
}

interface DbProvider {
    /** e.g. "postgresql", "mysql", "sqlite", "mongodb" — string-typed to
     *  pass through whatever value the schema/config file declares. */
    kind: string;
    /** Where the provider was detected — informs how the agent should
     *  treat it (Prisma's `prisma migrate` vs Drizzle's `db:push`). */
    source: "prisma" | "drizzle";
}

interface RouteEntry {
    method: string;
    path: string;
    file: string;
    line: number;
}

interface ContextDetection {
    keyFiles: FileExcerpt[];
    adrs: AdrEntry[];
    projectDocs: ProjectDocEntry[];   // populated only when adrs is empty
    manualMigrations: string[];
    routes: RouteEntry[];
    framework: "next" | "express" | "fastify" | "hono" | "elysia" | "node";
    dbProvider: DbProvider | null;
}

function readJsonSafe(path: string): any | null {
    try {
        return JSON.parse(readFileSync(path, "utf8"));
    } catch {
        return null;
    }
}

function detectFramework(): ContextDetection["framework"] {
    const pkg = readJsonSafe(resolvePath(TARGET, "package.json"));
    if (!pkg) return "node";
    const deps: Record<string, string> = {
        ...(pkg.dependencies ?? {}),
        ...(pkg.devDependencies ?? {}),
    };
    if (deps["next"]) return "next";
    if (deps["express"]) return "express";
    if (deps["fastify"]) return "fastify";
    if (deps["hono"]) return "hono";
    if (deps["elysia"]) return "elysia";
    return "node";
}

function takeExcerpt(absPath: string, maxLines = 30): FileExcerpt {
    const rel = relative(TARGET, absPath);
    const raw = readFileSync(absPath, "utf8");
    const lines = raw.split(/\r?\n/);
    const truncated = lines.length > maxLines;
    const head = lines.slice(0, maxLines).join("\n");
    return {
        label: rel,
        path: rel,
        excerpt: head,
        truncated,
    };
}

function detectKeyFiles(): FileExcerpt[] {
    const out: FileExcerpt[] = [];
    const candidates: Array<{ label: string; paths: string[] }> = [
        {
            label: "Storage layer",
            paths: [
                "server/storage.ts",
                "src/storage.ts",
                "src/lib/storage.ts",
                "src/server/storage.ts",
                "lib/storage.ts",
                "app/storage.ts",
                "app/lib/storage.ts",
            ],
        },
        {
            label: "Route registry",
            paths: [
                "server/routes.ts",
                "src/routes.ts",
                "src/server/routes.ts",
                "lib/routes.ts",
            ],
        },
        {
            label: "Schema",
            paths: [
                "shared/schema.ts",
                "shared/models/cms.ts",
                "src/db/schema.ts",
                "server/db/schema.ts",
                "prisma/schema.prisma",
                "drizzle/schema.ts",
            ],
        },
        {
            label: "Auth middleware",
            paths: [
                "server/auth.ts",
                "src/server/auth.ts",
                "lib/auth.ts",
                "app/lib/auth.ts",
                "middleware.ts",
            ],
        },
    ];

    for (const cand of candidates) {
        for (const rel of cand.paths) {
            const abs = resolvePath(TARGET, rel);
            if (existsSync(abs) && statSync(abs).isFile()) {
                const excerpt = takeExcerpt(abs);
                excerpt.label = cand.label;
                out.push(excerpt);
                break; // first match wins per category
            }
        }
    }
    return out;
}

// Shared title + first-paragraph-summary extractor. Returns null when
// the file has no H1 (probably not a structured doc — e.g. a stub or
// a transcript). Used by both detectAdrs() and detectProjectDocs().
function readDocSummary(absPath: string, fallbackTitle: string): {
    title: string;
    summary: string;
} | null {
    let raw: string;
    try {
        raw = readFileSync(absPath, "utf8");
    } catch {
        return null;
    }
    const lines = raw.split(/\r?\n/);
    const titleLine = lines.find((l) => /^#\s+/.test(l));
    const title = titleLine
        ? titleLine.replace(/^#\s+/, "").trim()
        : fallbackTitle;
    // First non-empty, non-heading, non-blockquote paragraph.
    let summary = "";
    let inSection = false;
    for (const line of lines) {
        if (/^#/.test(line)) {
            if (inSection) break;
            inSection = true;
            continue;
        }
        const t = line.trim();
        if (!t) continue;
        if (t.startsWith(">")) continue;
        summary = t.slice(0, 200);
        break;
    }
    return { title, summary };
}

function detectAdrs(): AdrEntry[] {
    const dir = resolvePath(TARGET, "docs/decisions");
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return [];
    const out: AdrEntry[] = [];
    let files: string[] = [];
    try {
        files = readdirSync(dir).filter((f) => /\.md$/i.test(f));
    } catch {
        return [];
    }
    for (const f of files) {
        const abs = join(dir, f);
        const parsed = readDocSummary(abs, f.replace(/\.md$/i, ""));
        if (!parsed) continue;
        out.push({
            path: relative(TARGET, abs),
            title: parsed.title,
            summary: parsed.summary,
            sortKey: f, // numeric prefixes (NNNN-…) sort newest-last
        });
    }
    // Sort newest first when ADRs use numeric prefixes.
    out.sort((a, b) => b.sortKey.localeCompare(a.sortKey));
    return out;
}

// Fallback for projects that keep architectural records as top-level
// `docs/*.md` "living docs" (e.g. gextrader's docs/ARCHITECTURE.md,
// docs/STATE.md, docs/RETIREMENT_LOG.md) instead of a `docs/decisions/`
// subdir. Only fires when detectAdrs() returned nothing — avoids
// duplicating content across two sections.
//
// Excludes README.md and CHANGELOG.md (those have specific roles and
// aren't ADR-shaped). Also caps at 10 entries to keep the context pack
// under the 8KB budget on doc-heavy projects.
function detectProjectDocs(): ProjectDocEntry[] {
    const dir = resolvePath(TARGET, "docs");
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return [];
    const out: ProjectDocEntry[] = [];
    let files: string[] = [];
    try {
        files = readdirSync(dir).filter((f) => {
            if (!/\.md$/i.test(f)) return false;
            const lower = f.toLowerCase();
            // Skip files that aren't architectural records by convention.
            if (lower === "readme.md") return false;
            if (lower === "changelog.md") return false;
            if (lower === "license.md") return false;
            return true;
        });
    } catch {
        return [];
    }
    for (const f of files) {
        const abs = join(dir, f);
        if (!statSync(abs).isFile()) continue;
        const parsed = readDocSummary(abs, f.replace(/\.md$/i, ""));
        if (!parsed) continue; // no H1 → probably not a structured doc
        out.push({
            path: relative(TARGET, abs),
            title: parsed.title,
            summary: parsed.summary,
        });
    }
    // Alphabetical (no reliable numeric ordering for living-docs).
    out.sort((a, b) => a.path.localeCompare(b.path));
    return out.slice(0, 10);
}

// Surface the database engine the project uses, when knowable from the
// Prisma schema or Drizzle config. Lets the implementer write SQL in
// the correct dialect without grep-walking the tree to find out.
function detectDbProvider(): DbProvider | null {
    // Prisma: peek prisma/schema.prisma for `provider = "postgresql"`
    const prismaPath = resolvePath(TARGET, "prisma/schema.prisma");
    if (existsSync(prismaPath)) {
        try {
            const raw = readFileSync(prismaPath, "utf8");
            // datasource db { provider = "postgresql" ... }
            const m = /\bprovider\s*=\s*["']([a-zA-Z0-9_-]+)["']/.exec(raw);
            if (m && m[1]) {
                return { kind: m[1], source: "prisma" };
            }
        } catch {
            // best-effort
        }
    }
    // Drizzle: peek drizzle.config.{ts,js,mjs} for `dialect: "postgresql"`
    for (const cfg of [
        "drizzle.config.ts",
        "drizzle.config.js",
        "drizzle.config.mjs",
    ]) {
        const p = resolvePath(TARGET, cfg);
        if (!existsSync(p)) continue;
        try {
            const raw = readFileSync(p, "utf8");
            const m = /\bdialect\s*:\s*["']([a-zA-Z0-9_-]+)["']/.exec(raw);
            if (m && m[1]) {
                return { kind: m[1], source: "drizzle" };
            }
        } catch {
            // best-effort
        }
    }
    return null;
}

function detectManualMigrations(): string[] {
    const dir = resolvePath(TARGET, "migrations");
    if (!existsSync(dir) || !statSync(dir).isDirectory()) return [];
    try {
        // Two naming conventions seen in practice:
        //   - `manual_<feature>.sql`   (AccessQuint / yukticastle default)
        //   - `<NNNN>_manual_<...>.sql` (verity / drizzle-kit alongside)
        //   - `<NNNN>_manual.sql`      (verity / leading-digit-only)
        return readdirSync(dir)
            .filter((f) => /(?:^|_)manual(?:_|\.)/.test(f) && /\.sql$/.test(f))
            .sort();
    } catch {
        return [];
    }
}

// Express-style route detection. Looks for app.METHOD / router.METHOD
// patterns in the route registry and adjacent server/ files.
const EXPRESS_ROUTE_RE =
    /\b(?:app|router)\.(get|post|put|patch|delete|use)\s*\(\s*["'`]([^"'`]+)["'`]/gi;

function detectExpressRoutes(): RouteEntry[] {
    const out: RouteEntry[] = [];
    const searchPaths = [
        "server/routes.ts",
        "server/index.ts",
        "src/server/routes.ts",
        "src/server/index.ts",
        "src/routes.ts",
        "src/index.ts",
        "lib/routes.ts",
        "app/index.ts",
    ];
    // Also walk routes-ish directories (one level). Two conventions:
    //   - server/routes/<feature>.ts (Express + a routes/ folder)
    //   - server/api/<feature>.ts    (Verity-style; mounted via app.use)
    // Both are common; check both, plus `src/server/api/` for src-layout.
    const routesDirCandidates = [
        "server/routes",
        "server/api",
        "src/server/routes",
        "src/server/api",
    ];
    for (const relDir of routesDirCandidates) {
        const absDir = resolvePath(TARGET, relDir);
        if (!existsSync(absDir) || !statSync(absDir).isDirectory()) continue;
        try {
            for (const f of readdirSync(absDir)) {
                if (/\.(t|j)s$/.test(f)) {
                    searchPaths.push(`${relDir}/${f}`);
                }
            }
        } catch {
            // skip — best-effort detection
        }
    }
    for (const rel of searchPaths) {
        const abs = resolvePath(TARGET, rel);
        if (!existsSync(abs)) continue;
        let raw: string;
        try {
            raw = readFileSync(abs, "utf8");
        } catch {
            continue;
        }
        const lines = raw.split(/\r?\n/);
        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            EXPRESS_ROUTE_RE.lastIndex = 0;
            let m: RegExpExecArray | null;
            while ((m = EXPRESS_ROUTE_RE.exec(line)) !== null) {
                const method = m[1].toUpperCase();
                if (method === "USE") continue; // mounts, not endpoints
                out.push({
                    method,
                    path: m[2],
                    file: rel,
                    line: i + 1,
                });
            }
        }
    }
    return out;
}

// Next.js App Router: file-based routing. Looks for app/**/route.ts
// (or .js / .tsx). Each file is one endpoint; the path is derived
// from the directory.
function detectNextRoutes(): RouteEntry[] {
    const out: RouteEntry[] = [];
    // Next.js supports BOTH `./app/` (root-level) and `./src/app/`
    // (the with-src-directory convention). gextrader uses the latter
    // and the original detection missed 51 route.ts files. Check both.
    const candidates = ["app", "src/app"];
    const appRel = candidates.find((p) => {
        const abs = resolvePath(TARGET, p);
        return existsSync(abs) && statSync(abs).isDirectory();
    });
    if (!appRel) return out;
    const appDir = resolvePath(TARGET, appRel);
    function walk(dir: string) {
        let entries: string[];
        try {
            entries = readdirSync(dir);
        } catch {
            return;
        }
        for (const e of entries) {
            const abs = join(dir, e);
            let s;
            try {
                s = statSync(abs);
            } catch {
                continue;
            }
            if (s.isDirectory()) {
                if (e === "node_modules" || e.startsWith(".")) continue;
                walk(abs);
            } else if (/^route\.(t|j)sx?$/.test(e)) {
                // Derive path from directory under app/.
                const rel = relative(appDir, dir);
                const route = "/" + rel.replace(/\\/g, "/");
                let raw: string;
                try {
                    raw = readFileSync(abs, "utf8");
                } catch {
                    continue;
                }
                // Look for `export async function GET/POST/...`.
                const methodRe =
                    /export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b/g;
                const lines = raw.split(/\r?\n/);
                for (let i = 0; i < lines.length; i++) {
                    let m: RegExpExecArray | null;
                    methodRe.lastIndex = 0;
                    while ((m = methodRe.exec(lines[i])) !== null) {
                        out.push({
                            method: m[1],
                            path: route === "/" ? "/" : route,
                            file: relative(TARGET, abs),
                            line: i + 1,
                        });
                    }
                }
            }
        }
    }
    walk(appDir);
    return out;
}

function detectRoutes(framework: ContextDetection["framework"]): RouteEntry[] {
    if (framework === "next") {
        const next = detectNextRoutes();
        // Next projects often also have an Express layer for /api edges.
        const express = detectExpressRoutes();
        return [...next, ...express];
    }
    return detectExpressRoutes();
}

function buildDetection(): ContextDetection {
    const framework = detectFramework();
    const adrs = detectAdrs();
    return {
        keyFiles: detectKeyFiles(),
        adrs,
        // Project docs fallback fires only when adrs is empty — keeps
        // the rendered output from duplicating content across two
        // sections. Projects with a real docs/decisions/ stick with
        // that; projects with a living-doc pattern (docs/*.md at top
        // level) get those surfaced instead.
        projectDocs: adrs.length === 0 ? detectProjectDocs() : [],
        manualMigrations: detectManualMigrations(),
        routes: detectRoutes(framework),
        framework,
        dbProvider: detectDbProvider(),
    };
}

// ============================================================
// Render
// ============================================================

const PROJECT_NAME = (() => {
    const pkg = readJsonSafe(resolvePath(TARGET, "package.json"));
    return pkg?.name ?? basename(TARGET);
})();

function renderContextMd(d: ContextDetection): string {
    const generated = new Date().toISOString().slice(0, 19) + "Z";
    const parts: string[] = [];

    parts.push(
        `# Project context — ${PROJECT_NAME}`,
        ``,
        `<!-- Auto-generated by \`npm run agents:context\` (${generated}). -->`,
        `<!-- Re-run after significant project changes. Operator may       -->`,
        `<!-- trim sections that aren't useful for your task spec.         -->`,
        ``,
    );

    if (d.keyFiles.length > 0) {
        parts.push(`## Key file paths`, ``);
        for (const f of d.keyFiles) {
            parts.push(
                `### ${f.label}: \`${f.path}\``,
                ``,
                "```",
                f.excerpt,
                "```",
                f.truncated ? `_(file continues — first ~30 lines shown)_` : ``,
                ``,
            );
        }
    } else {
        parts.push(
            `## Key file paths`,
            ``,
            `_(no canonical Yuktiv8-convention files detected — operator should add the actual paths)_`,
            ``,
        );
    }

    if (d.adrs.length > 0) {
        parts.push(`## ADRs (newest first)`, ``);
        for (const a of d.adrs) {
            parts.push(
                `- **\`${a.path}\`** — ${a.title}` +
                    (a.summary ? `\n  ${a.summary}` : ""),
            );
        }
        parts.push(``);
    } else if (d.projectDocs.length > 0) {
        // Living-doc pattern (no docs/decisions/ subdir; docs/*.md
        // at top level). These aren't strictly ADRs but they're the
        // closest architectural-record-shaped artifacts the project
        // has. Labelled distinctly so the agent doesn't mistake a
        // RUNBOOK or STATE doc for a formal decision record.
        parts.push(`## Project docs (top-level \`docs/*.md\`)`, ``);
        for (const p of d.projectDocs) {
            parts.push(
                `- **\`${p.path}\`** — ${p.title}` +
                    (p.summary ? `\n  ${p.summary}` : ""),
            );
        }
        parts.push(``);
    }

    if (d.manualMigrations.length > 0) {
        parts.push(`## Manual migrations`, ``);
        for (const m of d.manualMigrations) {
            parts.push(`- \`migrations/${m}\``);
        }
        parts.push(
            ``,
            `_The agent must NOT edit prior \`manual_*.sql\` files. New schema changes go to a new \`migrations/manual_<feature>.sql\`._`,
            ``,
        );
    }

    if (d.routes.length > 0) {
        parts.push(`## Public route inventory`, ``);
        // Bound the listing — routes can be 100+ on a mature project.
        const MAX_ROUTES = 60;
        const shown = d.routes.slice(0, MAX_ROUTES);
        for (const r of shown) {
            parts.push(
                `- \`${r.method.padEnd(6)} ${r.path}\`  ${`(${r.file}:${r.line})`}`,
            );
        }
        if (d.routes.length > MAX_ROUTES) {
            parts.push(
                ``,
                `_(${d.routes.length - MAX_ROUTES} more routes elided — re-run \`npm run agents:context\` to refresh)_`,
            );
        }
        parts.push(``);
    }

    const summaryParts = [
        `framework=${d.framework}`,
        `key-files=${d.keyFiles.length}`,
        d.adrs.length > 0
            ? `adrs=${d.adrs.length}`
            : `project-docs=${d.projectDocs.length}`,
        `manual-migrations=${d.manualMigrations.length}`,
        `routes=${d.routes.length}`,
    ];
    if (d.dbProvider) {
        summaryParts.push(`db=${d.dbProvider.kind} (via ${d.dbProvider.source})`);
    }
    parts.push(
        `---`,
        ``,
        `_Detection summary: ${summaryParts.join(", ")}._`,
        ``,
    );

    return parts.join("\n");
}

// ============================================================
// Main
// ============================================================

const detection = buildDetection();

if (JSON_MODE) {
    console.log(JSON.stringify(detection, null, 2));
    process.exit(0);
}

// Diagnostic summary (unless --quiet)
if (!QUIET) {
    console.log(c.bold(`Building context pack:`) + ` ${TARGET}`);
    console.log(`  ${c.dim("framework:")}         ${detection.framework}`);
    console.log(
        `  ${c.dim("key files:")}         ${detection.keyFiles.length > 0 ? detection.keyFiles.map((f) => f.label).join(", ") : c.yellow("(none detected)")}`,
    );
    if (detection.adrs.length > 0) {
        console.log(`  ${c.dim("adrs:")}              ${detection.adrs.length}`);
    } else if (detection.projectDocs.length > 0) {
        console.log(
            `  ${c.dim("project docs:")}      ${detection.projectDocs.length} ${c.dim("(living-doc fallback; no docs/decisions/)")}`,
        );
    } else {
        console.log(`  ${c.dim("adrs:")}              ${c.yellow("0 (no docs/decisions/ or docs/*.md)")}`);
    }
    console.log(
        `  ${c.dim("manual migrations:")} ${detection.manualMigrations.length}`,
    );
    console.log(`  ${c.dim("routes:")}            ${detection.routes.length}`);
    console.log(
        `  ${c.dim("db provider:")}      ${detection.dbProvider ? `${detection.dbProvider.kind} (via ${detection.dbProvider.source})` : c.dim("(not detected)")}`,
    );
    console.log(``);
}

const content = renderContextMd(detection);
const sizeKb = (Buffer.byteLength(content) / 1024).toFixed(1);

if (DRY_RUN) {
    console.log(content);
    info(`\n(dry-run; would write ${sizeKb}KB to .yukticastle/context.md)`);
    process.exit(0);
}

const outPath = resolvePath(TARGET, ".yukticastle/context.md");
writeFileSync(outPath, content);
info(c.green(`✓ wrote .yukticastle/context.md (${sizeKb}KB)`));

// Soft warning if we blew the token budget
if (Buffer.byteLength(content) > 8 * 1024) {
    console.warn(
        c.yellow(
            `⚠ context.md is ${sizeKb}KB — over the 8KB budget. Consider trimming the routes section or pruning ADRs.`,
        ),
    );
}

process.exit(0);
