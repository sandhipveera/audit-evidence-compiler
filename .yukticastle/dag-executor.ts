// YuktiCastle DAG executor — the spine for multi-role agent
// orchestration. Companion module to `dag.ts` (config loader).
//
// Design lineage:
//   docs/DAG-EXECUTOR.md  — the design doc (PR #47).
//                           Read that first; this is the
//                           implementation of §3 types + §6
//                           executeDag() semantics.
//   docs/ROLES.md         — what roles get added on top
//                           (Security Auditor, Architect, etc.)
//   docs/ENHANCEMENTS.md  — companion roadmap items (#18 declarative
//                           DAG config; this is the runtime that
//                           consumes it).
//
// What this module DOES
// ─────────────────────
// - Defines the type surface (PhaseHandler, PhaseResult, PhaseGroup,
//   DagPlan, DagResult, RunContext, FailurePolicy) that every future
//   role will compile against.
// - Provides `executeDag()` — runs sequential phase groups, respects
//   failure policies, propagates abort reasons, aggregates reports.
// - Provides `compileFlatToPlan()` — converts today's flat
//   `PhaseConfig[]` from `roles.json` into a `DagPlan` of one-phase
//   sequential groups (the v1 compiler per design doc §7).
//
// What this module DOES NOT
// ─────────────────────────
// - Print summaries, push branches, write runs.jsonl, exit the
//   process. Those concerns stay in main.mts as the caller. The
//   executor is pure orchestration.
// - Cleanly cancel in-flight sandcastle containers when fail_fast
//   trips. The Docker sandboxes the parallel handlers spawn keep
//   running until they finish naturally. PR E (this PR) implements
//   parallel execution; true cancellation requires a sandcastle
//   library change that's not yet planned. See §9 question #4
//   below for the operational mitigation.
// - Refresh anything cross-phase. Inter-phase concerns like Anthropic
//   OAuth refresh or codex auth.json refresh stay where they are in
//   main.mts. The executor's responsibility ends at "call the handler,
//   collect the result".
//
// Migration status (which phases use this executor)
// ─────────────────────────────────────────────────
// PR B  (#51)       spec_critic        ✓ merged
// PR C  (#52→#54)   implementer        ✓ merged
// PR D  (#55)       reviewer           ✓ merged
// PR E  (this)      parallel groups    ✓ this PR
// PR F  (next)      first parallel role (Security Auditor)
//
// As of PR E, every existing phase runs through executeDag(). The
// executor supports both sequential AND parallel groups. The next
// step is adding a real parallel role (Security Auditor first per
// ROLES.md §6 #3) that runs concurrently with the reviewer phase.
//
// No new deps — pure TypeScript over Node built-ins.

import type { PhaseConfig } from "./dag.js";

// ============================================================
// §3.1 RunContext — shared state across phases
// ============================================================

/**
 * Shape of the project context pack (.yukticastle/context.md content
 * if present, empty string otherwise). Typed for clarity; the
 * underlying value is just a string.
 */
export type ContextPack = string;

/**
 * Pattern-list aggregated from `.yukticastle/learnings.jsonl` (prior
 * reviewer fix-ups). Empty string if no learnings file yet.
 */
export type RecentPatterns = string;

/**
 * Run-level signals a phase can raise to abort the rest of the run.
 *
 * - `phase_failure` — a phase threw and its `onFailure` policy was "halt"
 * - `halt_signal`   — a phase deliberately requested halt (e.g. an
 *                      auditor with a hard-block finding)
 * - `cancelled`     — Ctrl-C or external SIGTERM observed
 */
export interface AbortReason {
    kind: "phase_failure" | "halt_signal" | "cancelled";
    /** Role that raised the abort. */
    role: string;
    /** Operator-visible message; safe to print directly. */
    message: string;
    /** Optional cause (e.g. the underlying Error from a throw). */
    cause?: unknown;
}

/**
 * Loose "tell future-me what happened" payload a phase can emit.
 * Examples:
 *   spec_critic     → { critique: "...", model_used: "...", tokens_in, tokens_out, elapsed_ms }
 *   architect       → { plan_path: ".yukticastle/plans/<branch>.md" }
 *   security_auditor → { findings: [...], severity: "low|med|high|crit" }
 *
 * Stored on PhaseReport.outputs; readers can narrow by role name.
 */
export type PhaseOutputs = Record<string, unknown>;

/**
 * Mutable shared state. The executor passes the same `RunContext`
 * to every handler. Handlers MAY mutate it (append to `reports`,
 * set `abortReason`). Handlers MUST NOT mutate the readonly fields.
 *
 * Initial-build note: this is intentionally a superset of what
 * main.mts currently passes around as module-scope state. Future
 * refactors of main.mts move more of that state into here so
 * handlers stop reaching into module scope. PR B keeps handlers as
 * closures over main.mts's scope; PR C/D will tighten that.
 */
export interface RunContext {
    // ───── Immutable for the run ─────
    readonly branch: string;
    readonly contextPack: ContextPack;
    readonly recentPatterns: RecentPatterns;
    /** Captured at run-start so handlers see a stable env even if
     *  someone mutates process.env mid-run. */
    readonly hostEnv: Readonly<Record<string, string | undefined>>;

    // ───── Mutable, append-only by phase handlers ─────
    /** Ordered by completion time. */
    reports: PhaseReport[];
    /** First non-null abort wins; subsequent groups skip. */
    abortReason: AbortReason | null;

    // ───── Free-form pin board for handler-specific scratch ─────
    /** e.g. spec_critic stashes its critique here so a future
     *  Architect or Implementer prompt can substitute it.
     *  String keys avoid coupling to specific role names. */
    scratchpad: Record<string, unknown>;
}

// ============================================================
// §3.3 PhaseResult + PhaseReport
// ============================================================

export interface IterationSummary {
    tokens_in: number;
    tokens_out: number;
    cache_read?: number;
    cache_create?: number;
}

export interface AggregatedUsage {
    input: number;
    output: number;
    cache_read: number;
    cache_create: number;
}

/**
 * Per-phase outcome record. Always populated (success or failure);
 * the executor uses this to build runs.jsonl entries downstream.
 */
export interface PhaseReport {
    role: string;
    model: string;
    /** ISO 8601 timestamps. */
    startedAt: string;
    finishedAt: string;
    durationMs: number;
    /** Iteration-level token usage; empty array for one-shot phases
     *  like spec_critic that don't iterate. */
    iterations: IterationSummary[];
    usage: AggregatedUsage;
    costUsd: number;
    /** SHAs this phase produced on the branch. Empty for non-committing
     *  phases (spec_critic, security_auditor in audit-only mode, etc.). */
    commits: string[];
    /** Role-specific structured data — see PhaseOutputs jsdoc. */
    outputs: PhaseOutputs;
    /** If this phase intentionally requested a run-level halt, the
     *  signal is recorded here. Distinct from a throw — a halt
     *  signal is signal, not noise. */
    halt?: HaltSignal;
}

/**
 * A phase can deliberately request the run to stop. Used by auditors
 * exercising their veto.
 */
export interface HaltSignal {
    /** Operator-visible reason. */
    reason: string;
    /** Severity hint for downstream UI / dashboard. */
    severity: "warning" | "error" | "critical";
}

/**
 * Why a phase was skipped without running. Distinct from failure.
 */
export type SkipReason =
    | { kind: "gated_off"; envVar: string; value: string | undefined }
    | { kind: "no_diff_to_review" }
    | { kind: "scope_below_threshold"; rule: string }
    /** Future Router rules can extend this discriminated union. */
    | { kind: "router_excluded"; reason: string };

/**
 * Sum type a handler returns. Exactly one of the three kinds.
 */
export type PhaseResult =
    | { kind: "success"; report: PhaseReport }
    | { kind: "failure"; report: PhaseReport; error: Error }
    | { kind: "skipped"; report: PhaseReport; reason: SkipReason };

// ============================================================
// §3.2 PhaseHandler — the signature every role implements
// ============================================================

/**
 * Each role implements one of these. The executor calls handlers
 * sequentially within a group (parallel within a parallel group,
 * once that lands in PR E).
 *
 * Handler contract:
 * - MUST return a PhaseResult; MUST NOT throw uncaught.
 *   (The executor catches uncaught throws and converts them to a
 *   `failure` result; the handler should still prefer explicit
 *   return for control.)
 * - MAY mutate `ctx.reports` and `ctx.abortReason` and `ctx.scratchpad`.
 * - MUST NOT mutate `ctx.branch`, `ctx.contextPack`, `ctx.recentPatterns`,
 *   `ctx.hostEnv`.
 * - MAY call process.exit() ONLY for unrecoverable host-level failures
 *   (e.g. Docker daemon dead). Prefer returning a `failure` result
 *   instead — the orchestrator (main.mts) can print summary + flush
 *   runs.jsonl before the process exits.
 */
export type PhaseHandler = (
    config: PhaseConfig,
    ctx: RunContext,
) => Promise<PhaseResult>;

/**
 * Registry mapping `role` name to handler. Built by main.mts and
 * passed to executeDag(). A config entry whose `role` isn't in the
 * registry is a startup error — loud-failure preferred over silent
 * skip.
 */
export type HandlerRegistry = Readonly<Record<string, PhaseHandler>>;

// ============================================================
// §3.4 PhaseGroup + DagPlan
// ============================================================

/**
 * Parallel-group failure policy. Active as of PR E.
 *
 * - `fail_fast`: the group resolves as soon as ONE sibling resolves
 *   with `failure`. Pending siblings keep running (sandcastle
 *   containers don't support clean cancellation) but their results
 *   are dropped from the report set. Use when one auditor blocks
 *   downstream value (e.g. Security Auditor crashing on a critical
 *   gate).
 * - `best_effort`: wait for every sibling to settle, record all
 *   results. Use when each auditor's value is independent — losing
 *   one reviewer's findings shouldn't suppress another's.
 */
export type ParallelFailurePolicy = "fail_fast" | "best_effort";

export type PhaseGroup =
    | { kind: "sequential"; phases: PhaseConfig[] }
    | { kind: "parallel"; phases: PhaseConfig[]; failurePolicy: ParallelFailurePolicy };

export interface DagPlan {
    /** Groups run in array order; each group blocks the next. */
    groups: PhaseGroup[];
}

// ============================================================
// §3.5 FailurePolicy — per-phase halt vs continue
// ============================================================

export type FailureAction = "halt" | "continue" | "continue_with_warning";

/**
 * Optional per-phase overrides for failure semantics. When a config
 * entry doesn't specify these, the executor applies role-aware
 * defaults via `defaultFailurePolicy()`.
 */
export interface PhaseFailurePolicy {
    /** What happens to the run if this phase fails (throws or returns
     *  `failure`). */
    onFailure?: FailureAction;
    /** What happens to the run if this phase emits a halt signal. */
    onHaltSignal?: "halt" | "continue_with_warning";
    /** Retry budget for known-transient classes. Default 1 (no retry). */
    retry?: {
        maxAttempts: number;
        onlyOn: Array<"docker_daemon" | "network" | "agent_timeout">;
    };
}

/**
 * Default policies inferred from the role name. Operators can
 * override via roles.json (future schema extension; v1 doesn't
 * expose these knobs yet — defaults always win).
 *
 * Rationale (design doc §3.5 defaults):
 *   spec_critic + implementer → halt (errors here block progress)
 *   reviewer + auditors      → continue_with_warning (errors don't
 *                               invalidate the implementer's work)
 *   anything explicit halt   → halt (auditors' intentional veto)
 */
export function defaultFailurePolicy(role: string): PhaseFailurePolicy {
    if (role === "spec_critic" || role === "implementer") {
        return { onFailure: "halt", onHaltSignal: "halt" };
    }
    // Reviewers and auditors are post-implementation perspectives;
    // a crash there doesn't invalidate the work that's already
    // committed. The orchestrator records the gap and continues.
    return { onFailure: "continue_with_warning", onHaltSignal: "halt" };
}

// ============================================================
// §3.6 DagResult — what executeDag() returns
// ============================================================

export type DagOutcome =
    | { kind: "complete" }
    | { kind: "halted"; reason: AbortReason; haltedAfter: string }
    | { kind: "failed"; reason: Error; failedIn: string };

export interface DagResult {
    reports: PhaseReport[];
    totalCostUsd: number;
    totalUsage: AggregatedUsage;
    outcome: DagOutcome;
}

// ============================================================
// Public API: compileFlatToPlan + executeDag
// ============================================================

/**
 * v1 compiler — every phase becomes its own sequential group.
 * Matches today's `roles.json` semantics (flat ordered list,
 * no parallel markers). When Router (ROLES.md §6 #2) lands, the
 * compiler will read parallel-group markers in `roles.json` and
 * bundle phases into parallel groups.
 *
 * Phases that `shouldRunPhase()` returns false for are filtered
 * OUT here (rather than inside executeDag) so the DagPlan reflects
 * exactly what will be executed. Reads cleanly in logs.
 */
export function compileFlatToPlan(
    phases: PhaseConfig[],
    shouldRun: (p: PhaseConfig) => boolean,
): DagPlan {
    return {
        groups: phases
            .filter(shouldRun)
            .map((p) => ({ kind: "sequential", phases: [p] } as const)),
    };
}

/**
 * Run a DagPlan against a RunContext, dispatching each phase to its
 * registered handler. Returns a DagResult aggregating phase reports,
 * costs, and final outcome.
 *
 * Pure orchestration — no console.log, no process.exit, no file I/O.
 * The caller (main.mts) handles user-visible output and run.jsonl.
 *
 * Behavior summary:
 * - Sequential groups run in array order.
 * - Within a sequential group, phases run in array order.
 * - On phase success: report appended to ctx.reports, executor
 *   continues.
 * - On phase failure (returned `failure` OR thrown): the phase's
 *   onFailure policy decides whether the run halts.
 * - On halt signal (returned `success` with `report.halt`): the
 *   phase's onHaltSignal policy decides.
 * - On abort (ctx.abortReason set by handler): remaining groups
 *   are skipped.
 * - Parallel groups (PR E): siblings run concurrently via
 *   `runParallelGroup`. fail_fast resolves the group on first
 *   failure (pending siblings keep running but their results are
 *   dropped — sandcastle containers aren't cleanly cancellable).
 *   best_effort waits for every sibling, includes all results.
 *   Per-phase failure/halt policies apply identically to results
 *   in either mode.
 */
export async function executeDag(
    plan: DagPlan,
    ctx: RunContext,
    handlers: HandlerRegistry,
): Promise<DagResult> {
    for (const group of plan.groups) {
        if (ctx.abortReason) break;
        if (group.kind === "parallel") {
            // PR E — real parallel execution. Each phase in the group
            // runs concurrently; results are folded into ctx.reports
            // in completion order. failurePolicy determines what
            // happens when one sibling fails:
            //   - best_effort: wait for every sibling to settle, record
            //     all results, only abort if a halted phase or default
            //     halt-on-failure policy demands it
            //   - fail_fast: resolve the group as soon as one sibling
            //     fails. Pending siblings keep running (sandcastle
            //     containers can't be cleanly cancelled — see top-of-
            //     file note) but their results are dropped.
            const parallelOutcome = await runParallelGroup(
                group.phases,
                group.failurePolicy,
                ctx,
                handlers,
            );
            // Push reports in completion order (already collected by
            // runParallelGroup). Sequential-group code below pushes
            // one report at a time; we push the whole batch.
            for (const result of parallelOutcome.results) {
                ctx.reports.push(result.report);
            }
            // Apply per-phase failure/halt policies. First match wins
            // (matches the sequential code's early-return semantics).
            for (const result of parallelOutcome.results) {
                const phaseConfig = group.phases.find(
                    (p) => p.role === result.report.role,
                );
                if (!phaseConfig) continue;
                const policy = defaultFailurePolicy(phaseConfig.role);
                if (result.kind === "failure") {
                    const action = policy.onFailure ?? "halt";
                    if (action === "halt") {
                        ctx.abortReason = {
                            kind: "phase_failure",
                            role: phaseConfig.role,
                            message: result.error.message,
                            cause: result.error,
                        };
                        return {
                            reports: [...ctx.reports],
                            totalCostUsd: ctx.reports.reduce(
                                (s, r) => s + r.costUsd,
                                0,
                            ),
                            totalUsage: aggregateUsage(ctx.reports),
                            outcome: {
                                kind: "failed",
                                reason: result.error,
                                failedIn: phaseConfig.role,
                            },
                        };
                    }
                    continue;
                }
                if (result.kind === "success" && result.report.halt) {
                    const action = policy.onHaltSignal ?? "halt";
                    if (action === "halt") {
                        ctx.abortReason = {
                            kind: "halt_signal",
                            role: phaseConfig.role,
                            message: result.report.halt.reason,
                        };
                        return {
                            reports: [...ctx.reports],
                            totalCostUsd: ctx.reports.reduce(
                                (s, r) => s + r.costUsd,
                                0,
                            ),
                            totalUsage: aggregateUsage(ctx.reports),
                            outcome: {
                                kind: "halted",
                                reason: ctx.abortReason,
                                haltedAfter: phaseConfig.role,
                            },
                        };
                    }
                }
            }
            continue;
        }
        for (const phaseConfig of group.phases) {
            if (ctx.abortReason) break;
            const handler = handlers[phaseConfig.role];
            if (!handler) {
                const err = new Error(
                    `[dag-executor] no handler registered for role "${phaseConfig.role}". ` +
                        `Known roles: ${Object.keys(handlers).join(", ") || "(none)"}.`,
                );
                return {
                    reports: [...ctx.reports],
                    totalCostUsd: ctx.reports.reduce((s, r) => s + r.costUsd, 0),
                    totalUsage: aggregateUsage(ctx.reports),
                    outcome: {
                        kind: "failed",
                        reason: err,
                        failedIn: phaseConfig.role,
                    },
                };
            }
            const policy = defaultFailurePolicy(phaseConfig.role);
            const result = await invokeHandler(handler, phaseConfig, ctx);
            // Always record the report (success, failure, or skipped).
            ctx.reports.push(result.report);
            if (result.kind === "failure") {
                const action = policy.onFailure ?? "halt";
                if (action === "halt") {
                    ctx.abortReason = {
                        kind: "phase_failure",
                        role: phaseConfig.role,
                        message: result.error.message,
                        cause: result.error,
                    };
                    return {
                        reports: [...ctx.reports],
                        totalCostUsd: ctx.reports.reduce((s, r) => s + r.costUsd, 0),
                        totalUsage: aggregateUsage(ctx.reports),
                        outcome: {
                            kind: "failed",
                            reason: result.error,
                            failedIn: phaseConfig.role,
                        },
                    };
                }
                // "continue" or "continue_with_warning" — fall through.
                continue;
            }
            if (result.kind === "success" && result.report.halt) {
                const action = policy.onHaltSignal ?? "halt";
                if (action === "halt") {
                    ctx.abortReason = {
                        kind: "halt_signal",
                        role: phaseConfig.role,
                        message: result.report.halt.reason,
                    };
                    return {
                        reports: [...ctx.reports],
                        totalCostUsd: ctx.reports.reduce((s, r) => s + r.costUsd, 0),
                        totalUsage: aggregateUsage(ctx.reports),
                        outcome: {
                            kind: "halted",
                            reason: ctx.abortReason,
                            haltedAfter: phaseConfig.role,
                        },
                    };
                }
            }
            // Skipped results are already recorded above; nothing
            // else to do for them.
        }
    }
    if (ctx.abortReason) {
        return {
            reports: [...ctx.reports],
            totalCostUsd: ctx.reports.reduce((s, r) => s + r.costUsd, 0),
            totalUsage: aggregateUsage(ctx.reports),
            outcome: {
                kind: "halted",
                reason: ctx.abortReason,
                haltedAfter: ctx.abortReason.role,
            },
        };
    }
    return {
        reports: [...ctx.reports],
        totalCostUsd: ctx.reports.reduce((s, r) => s + r.costUsd, 0),
        totalUsage: aggregateUsage(ctx.reports),
        outcome: { kind: "complete" },
    };
}

// ============================================================
// Internal helpers
// ============================================================

/**
 * Wraps a handler call so an uncaught throw becomes a `failure`
 * result. Without this, the executor's main loop has to try/catch
 * around every call; this helper centralizes that discipline.
 *
 * The synthetic PhaseReport on a throw is intentionally minimal —
 * we can't know iteration counts or commits when the handler died
 * before reporting them.
 */
async function invokeHandler(
    handler: PhaseHandler,
    config: PhaseConfig,
    ctx: RunContext,
): Promise<PhaseResult> {
    const startedAt = new Date().toISOString();
    const t0 = Date.now();
    try {
        return await handler(config, ctx);
    } catch (err) {
        const finishedAt = new Date().toISOString();
        const e = err instanceof Error ? err : new Error(String(err));
        return {
            kind: "failure",
            error: e,
            report: {
                role: config.role,
                model: config.model,
                startedAt,
                finishedAt,
                durationMs: Date.now() - t0,
                iterations: [],
                usage: { input: 0, output: 0, cache_read: 0, cache_create: 0 },
                costUsd: 0,
                commits: [],
                outputs: { error: e.message },
            },
        };
    }
}

/**
 * Outcome of `runParallelGroup`. Returns whichever results were
 * collected before the group resolved — for best_effort this is
 * always all of them; for fail_fast it may be a subset (siblings
 * still running are orphaned).
 */
interface ParallelGroupOutcome {
    /** Results in completion order. */
    results: PhaseResult[];
    /** When fail_fast trips, the role whose failure ended the group.
     *  Useful for telemetry. Empty string when not applicable. */
    failedFastIn: string;
}

/**
 * Synthesize a `failure` PhaseResult for a phase whose role isn't
 * in the handler registry. Mirrors the sequential code's
 * "no handler registered" branch so parallel groups handle it the
 * same way (failed result + halt-on-failure policy fires).
 */
function synthesizeMissingHandlerResult(
    phaseConfig: PhaseConfig,
    handlers: HandlerRegistry,
): PhaseResult {
    const known = Object.keys(handlers).join(", ") || "(none)";
    const err = new Error(
        `[dag-executor] no handler registered for role "${phaseConfig.role}". Known roles: ${known}.`,
    );
    const now = new Date().toISOString();
    return {
        kind: "failure",
        error: err,
        report: {
            role: phaseConfig.role,
            model: phaseConfig.model,
            startedAt: now,
            finishedAt: now,
            durationMs: 0,
            iterations: [],
            usage: { input: 0, output: 0, cache_read: 0, cache_create: 0 },
            costUsd: 0,
            commits: [],
            outputs: { error: err.message },
        },
    };
}

/**
 * Run a parallel group's phases concurrently. Always returns —
 * never throws.
 *
 * Cancellation note: sandcastle's RunResult doesn't expose a kill
 * handle, so even with `fail_fast` we can't terminate in-flight
 * Docker containers. Orphans cost cents and a few minutes of
 * idle wall-time but don't affect correctness. See top-of-file
 * non-goal #1 + design doc §9 question #4.
 *
 * `fail_fast` semantics:
 *   - Resolve as soon as any sibling resolves with `failure`.
 *   - Pending siblings continue running in the background but
 *     their results are NOT included in `results`.
 *   - `failedFastIn` carries the role that triggered the
 *     fast-exit, for telemetry.
 *
 * `best_effort` semantics:
 *   - Wait for ALL siblings to settle.
 *   - Include every result regardless of success/failure.
 *   - `failedFastIn` is empty string.
 *
 * For both policies: results are recorded in COMPLETION order
 * (first to settle is first in the array), not input order.
 * Reports going into `runs.jsonl` reflect actual wall-clock
 * sequencing, which is useful for diagnosing slow-auditor cases.
 */
async function runParallelGroup(
    phases: PhaseConfig[],
    failurePolicy: ParallelFailurePolicy,
    ctx: RunContext,
    handlers: HandlerRegistry,
): Promise<ParallelGroupOutcome> {
    // Build per-phase promises. Missing-handler synthetic failures
    // resolve immediately so they participate in fail_fast too.
    const invocations: Array<{
        config: PhaseConfig;
        promise: Promise<PhaseResult>;
    }> = phases.map((phaseConfig) => {
        const handler = handlers[phaseConfig.role];
        if (!handler) {
            return {
                config: phaseConfig,
                promise: Promise.resolve(
                    synthesizeMissingHandlerResult(phaseConfig, handlers),
                ),
            };
        }
        return {
            config: phaseConfig,
            promise: invokeHandler(handler, phaseConfig, ctx),
        };
    });

    if (failurePolicy === "best_effort") {
        // Wait for everyone. Order by completion via Promise-trick:
        // race each against its index, collect in `results` as each
        // resolves.
        const results: PhaseResult[] = [];
        await Promise.all(
            invocations.map(({ promise }) =>
                promise.then((r) => {
                    results.push(r);
                }),
            ),
        );
        return { results, failedFastIn: "" };
    }

    // fail_fast: resolve as soon as the first failure lands.
    return await new Promise<ParallelGroupOutcome>((resolve) => {
        const results: PhaseResult[] = [];
        let resolved = false;
        let settled = 0;
        const total = invocations.length;
        if (total === 0) {
            resolve({ results, failedFastIn: "" });
            return;
        }
        for (const { config, promise } of invocations) {
            promise.then((r) => {
                if (resolved) return; // sibling failure already won
                results.push(r);
                settled++;
                if (r.kind === "failure") {
                    resolved = true;
                    resolve({ results, failedFastIn: config.role });
                    return;
                }
                if (settled === total) {
                    resolved = true;
                    resolve({ results, failedFastIn: "" });
                }
            });
        }
    });
}

function aggregateUsage(reports: PhaseReport[]): AggregatedUsage {
    const out: AggregatedUsage = {
        input: 0,
        output: 0,
        cache_read: 0,
        cache_create: 0,
    };
    for (const r of reports) {
        out.input += r.usage.input;
        out.output += r.usage.output;
        out.cache_read += r.usage.cache_read;
        out.cache_create += r.usage.cache_create;
    }
    return out;
}

// ============================================================
// Tiny helper for handler authors — build a PhaseReport skeleton
// ============================================================

/**
 * Convenience to build a PhaseReport with sane defaults. Handlers
 * call this at the start and overwrite the populated fields on
 * return. Reduces boilerplate vs constructing the literal.
 */
export function newPhaseReport(
    config: PhaseConfig,
    startedAt: string = new Date().toISOString(),
): PhaseReport {
    return {
        role: config.role,
        model: config.model,
        startedAt,
        finishedAt: startedAt, // overwritten on completion
        durationMs: 0,
        iterations: [],
        usage: { input: 0, output: 0, cache_read: 0, cache_create: 0 },
        costUsd: 0,
        commits: [],
        outputs: {},
    };
}
