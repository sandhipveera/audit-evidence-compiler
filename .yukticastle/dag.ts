// YuktiCastle declarative DAG config — ENHANCEMENTS.md item #18.
//
// Loads `.yukticastle/roles.json` and exposes per-phase config to
// main.mts. v1 supports the three roles we have today: spec_critic,
// implementer, reviewer. Operators edit roles.json to:
//
//   - Swap models per phase (claude-opus → claude-sonnet, etc.)
//   - Change max_iterations / effort
//   - Gate a phase via env var (when: { env: "FOO" })
//
// Future expansion (ENHANCEMENTS.md #15 + #18 follow-ups):
//   - New role types (security_auditor, migration_auditor, doc_writer)
//   - Dynamic phase dispatch via executeDag() that iterates over
//     config.phases instead of main.mts's hard-coded sequential blocks
//   - Conditional `when: { diff_matches: "..." }` evaluated post-Phase-1
//   - Parallel execution groups
//
// For v1, the executor lives implicitly in main.mts; this module
// just provides the typed loader + helpers. Adding a 4th role becomes
// "add a row to roles.json + a handler block in main.mts" — until the
// executor refactor, which we'll do when there's a real motivator.
//
// No new deps — pure Node `fs` + `JSON.parse`.

import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// ============================================================
// Types
// ============================================================

export type Provider = "anthropic" | "openai" | "openrouter";

export type WhenClause =
    | "always"
    | { env: string };
    // Future: | { diff_matches: string } | { files_touched: string[] }

export interface PhaseConfig {
    /** Role name — matches a handler in main.mts. v1 known: spec_critic / implementer / reviewer. */
    role: string;
    /** When to run this phase. Default: "always". */
    when?: WhenClause;
    /** Provider for this phase's model. */
    provider: Provider;
    /** Model id — e.g. "claude-opus-4-6", "gpt-5-codex", "meta-llama/llama-3.3-70b-instruct:free". */
    model: string;
    /** Override the upstream max_iterations. Defaults: impl=5, reviewer=3, spec_critic=1. */
    max_iterations?: number;
    /** Effort hint passed to claudeCode agents. */
    effort?: "low" | "medium" | "high";
    /** Free-form per-role options the handler interprets. */
    options?: Record<string, unknown>;
}

export interface DagConfig {
    /** Schema version of the config file. Currently 1. */
    schema_version: number;
    /** Ordered phase list. Order matters; v1 dispatch is sequential. */
    phases: PhaseConfig[];
}

// ============================================================
// Default config — exactly mirrors today's hard-coded behavior
// when no roles.json is present
// ============================================================

export const DEFAULT_CONFIG: DagConfig = {
    schema_version: 1,
    phases: [
        {
            role: "spec_critic",
            when: { env: "YUKTICASTLE_SPEC_CRITIC" },
            provider: "openrouter",
            model: "meta-llama/llama-3.3-70b-instruct:free",
            max_iterations: 1,
        },
        // PR I (ROLES.md §2.1 / §6 #5) — Architect / Planner.
        // Opt-in pre-implementer phase. Decomposes the spec into a
        // structured plan: files to create/modify, sequencing,
        // identified risks. Output committed as
        // `.yukticastle/plans/<branch>.md`; implementer's prompt
        // gets the plan content via {{PLAN}} substitution.
        //
        // Different cognitive mode from the implementer (which is
        // claude-opus too) — separate phase, distinct prompt, can
        // be model-bumped independently. Common pattern when this
        // role lands: keep architect on Opus and downgrade
        // implementer to Sonnet for cost.
        //
        // Gated by env only in PR I. Future enhancement: add
        // size-based heuristic (skip on tasks < N files / < M LOC
        // estimated from the spec).
        {
            role: "architect",
            when: { env: "YUKTICASTLE_ARCHITECT" },
            provider: "anthropic",
            model: "claude-opus-4-6",
            max_iterations: 1,
            effort: "high",
        },
        {
            role: "implementer",
            when: "always",
            provider: "anthropic",
            model: "claude-opus-4-6",
            max_iterations: 5,
            effort: "high",
        },
        {
            role: "reviewer",
            when: "always",
            provider: "anthropic",
            model: "claude-sonnet-4-6",
            max_iterations: 3,
        },
        // PR F (ROLES.md §2.3 / §6 #3) — Security Auditor.
        // Opt-in via YUKTICASTLE_SECURITY_AUDITOR=true. Codex-backed
        // for independent perspective from the Claude-family implementer
        // + reviewer (one of the points of multi-role: don't review with
        // the same model that wrote the code).
        //
        // Audit-only: produces findings, never commits. Critical findings
        // emit HaltSignal so the run halts and auto-PR is suppressed.
        //
        // Model default: gpt-5 (broadly available on ChatGPT OAuth +
        // OPENAI_API_KEY paths; the `-codex` variants 400 on OAuth so
        // they're paid-API-only and unsuitable as the default).
        {
            role: "security_auditor",
            when: { env: "YUKTICASTLE_SECURITY_AUDITOR" },
            provider: "openai",
            model: "gpt-5",
            max_iterations: 1,
        },
        // PR H (ROLES.md §2.3 / §6 #4) — Migration Auditor.
        // Opt-in via YUKTICASTLE_MIGRATION_AUDITOR=true AND the diff
        // must touch a migration path (configurable via
        // YUKTICASTLE_MIGRATION_PATHS; default prisma/migrations/,
        // migrations/, db/migrate/, drizzle/, *.sql). Two-gate
        // structure ensures the auditor doesn't burn quota on runs
        // that touch no schema changes.
        //
        // The env gate is the only thing dag.ts can express today
        // (WhenClause v1 supports `always` | `{ env: ... }`); the
        // diff-match gate is enforced in main.mts before composing
        // the plan.
        //
        // Codex-backed (gpt-5) for independent perspective. Audit-
        // only: produces findings at .yukticastle/migration-findings/
        // <branch>.md, never modifies migrations. Critical findings
        // emit HaltSignal → run halts, auto-PR suppressed.
        {
            role: "migration_auditor",
            when: { env: "YUKTICASTLE_MIGRATION_AUDITOR" },
            provider: "openai",
            model: "gpt-5",
            max_iterations: 1,
        },
        // PR J (ROLES.md §2.2 / §6 #6) — Test Engineer.
        // Opt-in via YUKTICASTLE_TEST_ENGINEER=true. Runs post-
        // implementer in the same parallel group as the reviewer +
        // auditors. Reads the implementer's diff, identifies code
        // without tests, writes tests, commits them as a single
        // "test: coverage for <branch>" commit.
        //
        // Distinct from the auditors in two ways:
        //   1. Test Engineer WRITES CODE (test files committed to
        //      the branch). Auditors are read-only producers of
        //      findings markdown.
        //   2. Test Engineer has NO halt path. A test gap is
        //      forgivable (operator can add tests later); a critical
        //      security finding is not.
        //
        // Failure mode: forgiving. test_engineer_crashed records the
        // gap in runs.jsonl but doesn't halt the run.
        //
        // Model default: claude-sonnet-4-6 — same model family the
        // implementer uses (writing tests is closer to implementation
        // than to planning). Sonnet, not Opus, because tests are
        // pattern-following work that doesn't need maximum reasoning.
        {
            role: "test_engineer",
            when: { env: "YUKTICASTLE_TEST_ENGINEER" },
            provider: "anthropic",
            model: "claude-sonnet-4-6",
            max_iterations: 2,
            effort: "medium",
        },
    ],
};

// ============================================================
// Loader
// ============================================================

/**
 * Loads `.yukticastle/roles.json` from CWD. Falls back to DEFAULT_CONFIG
 * if the file is missing — preserves backward compatibility for projects
 * that haven't opted into the declarative DAG.
 *
 * Throws on a malformed file (loud-failure preferred over silent
 * default — a hand-edited roles.json that's wrong should surface
 * fast, not run with surprise defaults).
 */
export function loadDagConfig(cwd: string = process.cwd()): DagConfig {
    const path = resolvePath(cwd, ".yukticastle/roles.json");
    if (!existsSync(path)) return DEFAULT_CONFIG;
    let raw: string;
    try {
        raw = readFileSync(path, "utf8");
    } catch (err) {
        throw new Error(
            `[yukticastle] could not read .yukticastle/roles.json: ${(err as Error).message}`,
        );
    }
    let parsed: unknown;
    try {
        parsed = JSON.parse(raw);
    } catch (err) {
        throw new Error(
            `[yukticastle] .yukticastle/roles.json is not valid JSON: ${(err as Error).message}`,
        );
    }
    const validated = validateDagConfig(parsed);
    return validated;
}

function validateDagConfig(input: unknown): DagConfig {
    if (typeof input !== "object" || input === null) {
        throw new Error("[yukticastle] roles.json: top-level must be an object");
    }
    const obj = input as Record<string, unknown>;
    const schema_version = Number(obj.schema_version ?? 1);
    if (!Number.isFinite(schema_version)) {
        throw new Error("[yukticastle] roles.json: schema_version must be a number");
    }
    if (!Array.isArray(obj.phases)) {
        throw new Error("[yukticastle] roles.json: `phases` must be an array");
    }
    const phases: PhaseConfig[] = obj.phases.map((p, i) => {
        if (typeof p !== "object" || p === null) {
            throw new Error(`[yukticastle] roles.json: phases[${i}] must be an object`);
        }
        const ph = p as Record<string, unknown>;
        if (typeof ph.role !== "string") {
            throw new Error(`[yukticastle] roles.json: phases[${i}].role must be a string`);
        }
        if (typeof ph.provider !== "string") {
            throw new Error(`[yukticastle] roles.json: phases[${i}].provider must be a string`);
        }
        if (!["anthropic", "openai", "openrouter"].includes(ph.provider)) {
            throw new Error(
                `[yukticastle] roles.json: phases[${i}].provider must be anthropic|openai|openrouter, got "${ph.provider}"`,
            );
        }
        if (typeof ph.model !== "string") {
            throw new Error(`[yukticastle] roles.json: phases[${i}].model must be a string`);
        }
        const when = (ph.when as WhenClause | undefined) ?? "always";
        if (
            when !== "always" &&
            (typeof when !== "object" || !("env" in when) || typeof when.env !== "string")
        ) {
            throw new Error(
                `[yukticastle] roles.json: phases[${i}].when must be "always" or {env: "VAR_NAME"}`,
            );
        }
        return {
            role: ph.role as string,
            when,
            provider: ph.provider as Provider,
            model: ph.model as string,
            max_iterations:
                typeof ph.max_iterations === "number" ? ph.max_iterations : undefined,
            effort: (ph.effort as PhaseConfig["effort"]) ?? undefined,
            options: (ph.options as Record<string, unknown>) ?? undefined,
        };
    });
    return { schema_version, phases };
}

// ============================================================
// Per-phase accessors used by main.mts
// ============================================================

/**
 * Get the config for a specific role. Returns undefined if the role
 * isn't in the config (e.g. operator deleted the reviewer phase
 * because their workflow doesn't want one).
 */
export function phaseFor(
    config: DagConfig,
    role: string,
): PhaseConfig | undefined {
    return config.phases.find((p) => p.role === role);
}

/**
 * Should this phase run, given the current environment?
 *
 * - `when: "always"` (or omitted) → true
 * - `when: { env: "FOO" }` → true iff process.env.FOO is truthy
 *   ("true", "1", "yes" — case-insensitive)
 */
export function shouldRunPhase(phase: PhaseConfig): boolean {
    const when = phase.when ?? "always";
    if (when === "always") return true;
    if (typeof when === "object" && "env" in when) {
        const value = (process.env[when.env] ?? "").toLowerCase();
        return value === "true" || value === "1" || value === "yes";
    }
    return false;
}

// ============================================================
// Diagnostic — used by main.mts when loading
// ============================================================

export function describeConfig(config: DagConfig): string {
    const lines: string[] = [];
    lines.push(`[yukticastle] DAG: ${config.phases.length} phase(s) configured`);
    for (const p of config.phases) {
        const gate =
            p.when === "always" || p.when === undefined
                ? "always"
                : typeof p.when === "object" && "env" in p.when
                  ? `env:${p.when.env}`
                  : "?";
        const enabled = shouldRunPhase(p) ? "✓" : "·";
        lines.push(
            `  ${enabled} ${p.role.padEnd(16, " ")} ${p.provider}:${p.model}` +
                (p.max_iterations ? ` iter=${p.max_iterations}` : "") +
                ` (when=${gate})`,
        );
    }
    return lines.join("\n");
}
