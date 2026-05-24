// YuktiCastle run-history report — `npm run agents:report`.
//
// Reads `.yukticastle/runs.jsonl` (appended by main.mts) and prints
// an aggregated summary: cost trends, fallback rate, result
// distribution, top tasks. Composes with future telemetry items
// (#14 auth-path telemetry, #11 model-routing analytics).
//
// Usage:
//   npm run agents:report                       # last 30 days, summary
//   npm run agents:report -- --since=7          # last 7 days
//   npm run agents:report -- --since=all        # everything
//   npm run agents:report -- --task='hospitality'  # filter title (substring, case-insensitive)
//   npm run agents:report -- --json             # machine-readable
//   npm run agents:report -- --raw              # one line per run
//
// Exit 0 always (zero entries handled cleanly).
//
// No new deps — pure Node built-ins.

import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath, basename } from "node:path";

// ============================================================
// CLI flags
// ============================================================

const argv = process.argv.slice(2);
const JSON_MODE = argv.includes("--json");
const RAW_MODE = argv.includes("--raw");
const NO_COLOR =
    argv.includes("--no-color") ||
    !process.stdout.isTTY ||
    process.env.NO_COLOR !== undefined;

function flagValue(name: string): string | undefined {
    const prefix = `--${name}=`;
    const found = argv.find((a) => a.startsWith(prefix));
    return found?.slice(prefix.length);
}

const sinceArg = flagValue("since") ?? "30";
const sinceDays = sinceArg === "all" ? Infinity : Number(sinceArg);
if (!Number.isFinite(sinceDays) && sinceDays !== Infinity) {
    console.error(`Invalid --since=${sinceArg} (expect number of days or "all")`);
    process.exit(2);
}
const taskFilter = flagValue("task")?.toLowerCase();

// ============================================================
// ANSI helpers (mirror doctor / lint / introspect)
// ============================================================

const c = {
    green: (s: string) => (NO_COLOR ? s : `\x1b[32m${s}\x1b[0m`),
    yellow: (s: string) => (NO_COLOR ? s : `\x1b[33m${s}\x1b[0m`),
    red: (s: string) => (NO_COLOR ? s : `\x1b[31m${s}\x1b[0m`),
    blue: (s: string) => (NO_COLOR ? s : `\x1b[34m${s}\x1b[0m`),
    dim: (s: string) => (NO_COLOR ? s : `\x1b[2m${s}\x1b[0m`),
    bold: (s: string) => (NO_COLOR ? s : `\x1b[1m${s}\x1b[0m`),
};

// ============================================================
// Schema (loose — operators may have older records from earlier
// schema versions; we tolerate missing fields)
// ============================================================

interface PhaseRecord {
    iterations?: number;
    commits?: number;
    completed?: boolean;
    tokens?: {
        input?: number;
        output?: number;
        cache_read?: number;
        cache_create?: number;
    };
    cost_usd?: number;
}

interface RunRecord {
    schema_version?: number;
    started_at?: string;
    finished_at?: string;
    task?: { path?: string | null; title?: string };
    branch?: string;
    models?: { implementer?: string | null; reviewer?: string | null };
    reviewer_kind?: "claude" | "codex" | null;
    auth?: { anthropic?: "macos-keychain-oauth" | "api-key" | "none" };
    phases?: { implementer?: PhaseRecord | null; reviewer?: PhaseRecord | null };
    wall_time_s?: number | null;
    files_changed?: number | null;
    total_cost_usd?: number;
    total_cost_billed_usd?: number;
    result?: string;
}

// ============================================================
// Load + filter
// ============================================================

function loadRecords(): RunRecord[] {
    const path = resolvePath(process.cwd(), ".yukticastle/runs.jsonl");
    if (!existsSync(path)) {
        return [];
    }
    const raw = readFileSync(path, "utf8");
    const out: RunRecord[] = [];
    for (const line of raw.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
            out.push(JSON.parse(trimmed));
        } catch {
            // Skip malformed lines — don't crash the report.
        }
    }
    return out;
}

function withinSince(rec: RunRecord, sinceMs: number): boolean {
    if (sinceDays === Infinity) return true;
    if (!rec.started_at) return false;
    const t = Date.parse(rec.started_at);
    if (!Number.isFinite(t)) return false;
    return t >= sinceMs;
}

// ============================================================
// Aggregations
// ============================================================

interface Aggregate {
    runs: number;
    total_cost_billed: number;
    total_cost_hypothetical: number;
    runs_via_oauth: number;
    runs_via_api_key: number;
    runs_with_no_auth: number;
    by_result: Record<string, number>;
    by_implementer: Record<string, number>;
    by_reviewer_kind: Record<string, number>;
    silent_failures: Array<{
        date: string;
        branch: string;
        result: string;
        title: string;
    }>;
    top_costly_runs: Array<{
        date: string;
        title: string;
        cost: number;
        oauth: boolean;
    }>;
    median_wall_time_s: number;
    mean_wall_time_s: number;
}

function aggregate(records: RunRecord[]): Aggregate {
    const wallTimes: number[] = [];
    const a: Aggregate = {
        runs: records.length,
        total_cost_billed: 0,
        total_cost_hypothetical: 0,
        runs_via_oauth: 0,
        runs_via_api_key: 0,
        runs_with_no_auth: 0,
        by_result: {},
        by_implementer: {},
        by_reviewer_kind: {},
        silent_failures: [],
        top_costly_runs: [],
        median_wall_time_s: 0,
        mean_wall_time_s: 0,
    };

    for (const r of records) {
        a.total_cost_billed += r.total_cost_billed_usd ?? 0;
        a.total_cost_hypothetical += r.total_cost_usd ?? 0;
        const auth = r.auth?.anthropic ?? "none";
        if (auth === "macos-keychain-oauth") a.runs_via_oauth += 1;
        else if (auth === "api-key") a.runs_via_api_key += 1;
        else a.runs_with_no_auth += 1;
        const result = r.result ?? "(unknown)";
        a.by_result[result] = (a.by_result[result] ?? 0) + 1;
        const impl = r.models?.implementer ?? "(unknown)";
        a.by_implementer[impl] = (a.by_implementer[impl] ?? 0) + 1;
        const revKind = r.reviewer_kind ?? "(skipped)";
        a.by_reviewer_kind[revKind] =
            (a.by_reviewer_kind[revKind] ?? 0) + 1;

        if (
            result === "implementer_silent_failure" ||
            result === "reviewer_silent_failure"
        ) {
            a.silent_failures.push({
                date: r.started_at?.slice(0, 10) ?? "?",
                branch: r.branch ?? "?",
                result,
                title: r.task?.title ?? "?",
            });
        }
        if (typeof r.wall_time_s === "number") wallTimes.push(r.wall_time_s);
    }

    // Top 5 most expensive runs (by hypothetical cost so OAuth runs
    // also surface — operators care about token usage trends even
    // when the bill is $0).
    a.top_costly_runs = [...records]
        .filter((r) => (r.total_cost_usd ?? 0) > 0)
        .sort((x, y) => (y.total_cost_usd ?? 0) - (x.total_cost_usd ?? 0))
        .slice(0, 5)
        .map((r) => ({
            date: r.started_at?.slice(0, 10) ?? "?",
            title: r.task?.title ?? "?",
            cost: r.total_cost_usd ?? 0,
            oauth: r.auth?.anthropic === "macos-keychain-oauth",
        }));

    if (wallTimes.length > 0) {
        const sorted = [...wallTimes].sort((x, y) => x - y);
        a.median_wall_time_s =
            sorted.length % 2 === 1
                ? sorted[(sorted.length - 1) / 2]
                : Math.round(
                      (sorted[sorted.length / 2 - 1] +
                          sorted[sorted.length / 2]) /
                          2,
                  );
        a.mean_wall_time_s = Math.round(
            wallTimes.reduce((s, n) => s + n, 0) / wallTimes.length,
        );
    }

    return a;
}

// ============================================================
// Output — human
// ============================================================

function fmtCost(n: number): string {
    if (n === 0) return "$0";
    if (n < 0.01) return `<$0.01`;
    return `$${n.toFixed(2)}`;
}

function fmtPct(num: number, denom: number): string {
    if (denom === 0) return "—";
    return `${Math.round((num / denom) * 100)}%`;
}

function fmtDuration(s: number): string {
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}m${sec.toString().padStart(2, "0")}s`;
}

function printHuman(records: RunRecord[], a: Aggregate): void {
    const projectName = basename(resolvePath(process.cwd()));
    const sinceLabel =
        sinceDays === Infinity ? "all time" : `last ${sinceDays}d`;
    const filterLabel = taskFilter ? ` (filter: title~="${taskFilter}")` : "";
    console.log(
        c.bold(`YuktiCastle runs — ${projectName} (${sinceLabel}${filterLabel})`),
    );
    console.log(c.dim("─".repeat(60)));

    if (a.runs === 0) {
        console.log(c.yellow(`No runs recorded in this window.`));
        const path = resolvePath(process.cwd(), ".yukticastle/runs.jsonl");
        if (!existsSync(path)) {
            console.log(
                c.dim(
                    `(${path} does not exist — first agents:run on this branch will create it.)`,
                ),
            );
        }
        return;
    }

    console.log(`runs:               ${a.runs}`);
    if (a.runs_via_oauth > 0 || a.runs_via_api_key > 0) {
        console.log(
            `auth:               ${c.green(`${a.runs_via_oauth} OAuth`)} / ${c.yellow(`${a.runs_via_api_key} API-key`)}` +
                (a.runs_with_no_auth > 0
                    ? ` / ${c.red(`${a.runs_with_no_auth} no-auth`)}`
                    : "") +
                c.dim(
                    `   (api-key fallback rate: ${fmtPct(a.runs_via_api_key, a.runs)})`,
                ),
        );
    }
    console.log(
        `total cost (billed): ${c.bold(fmtCost(a.total_cost_billed))}` +
            (a.total_cost_hypothetical > a.total_cost_billed
                ? c.dim(
                      `   (hypothetical at API-key rates: ${fmtCost(a.total_cost_hypothetical)})`,
                  )
                : ""),
    );
    console.log(
        `mean per run:       ${fmtCost(a.total_cost_billed / a.runs)}` +
            (a.runs_via_oauth > 0
                ? c.dim(
                      `   (hypothetical mean: ${fmtCost(a.total_cost_hypothetical / a.runs)})`,
                  )
                : ""),
    );
    console.log(
        `wall time:          median ${fmtDuration(a.median_wall_time_s)} / mean ${fmtDuration(a.mean_wall_time_s)}`,
    );

    console.log("");
    console.log(c.bold("by result:"));
    for (const [result, n] of Object.entries(a.by_result).sort(
        (x, y) => y[1] - x[1],
    )) {
        const color =
            result === "ok"
                ? c.green
                : result.endsWith("silent_failure") || result === "error"
                  ? c.red
                  : c.yellow;
        console.log(
            `  ${color(result.padEnd(28, " "))} ${n}  ${c.dim(fmtPct(n, a.runs))}`,
        );
    }

    console.log("");
    console.log(c.bold("by implementer model:"));
    for (const [model, n] of Object.entries(a.by_implementer).sort(
        (x, y) => y[1] - x[1],
    )) {
        console.log(`  ${model.padEnd(28, " ")} ${n}`);
    }

    console.log("");
    console.log(c.bold("by reviewer:"));
    for (const [kind, n] of Object.entries(a.by_reviewer_kind).sort(
        (x, y) => y[1] - x[1],
    )) {
        console.log(`  ${kind.padEnd(28, " ")} ${n}`);
    }

    if (a.silent_failures.length > 0) {
        console.log("");
        console.log(c.bold("silent failures:"));
        for (const f of a.silent_failures.slice(0, 10)) {
            console.log(
                `  ${c.red(f.date)}  ${c.dim(f.result.padEnd(28, " "))} ${c.bold(f.title)}` +
                    c.dim(`   (${f.branch})`),
            );
        }
    }

    if (a.top_costly_runs.length > 0) {
        console.log("");
        console.log(c.bold("top costly runs:"));
        for (const r of a.top_costly_runs) {
            console.log(
                `  ${fmtCost(r.cost).padStart(7, " ")}  ${r.date}  ${r.title}` +
                    (r.oauth ? c.dim("   (OAuth — billed $0)") : ""),
            );
        }
    }
}

// ============================================================
// Main
// ============================================================

const all = loadRecords();
const cutoff = Date.now() - sinceDays * 24 * 60 * 60 * 1000;
const filtered = all
    .filter((r) => withinSince(r, cutoff))
    .filter(
        (r) =>
            !taskFilter ||
            (r.task?.title ?? "").toLowerCase().includes(taskFilter),
    );

if (RAW_MODE) {
    for (const r of filtered) {
        console.log(JSON.stringify(r));
    }
    process.exit(0);
}

if (JSON_MODE) {
    const a = aggregate(filtered);
    console.log(
        JSON.stringify(
            {
                project: basename(resolvePath(process.cwd())),
                window: { since_days: sinceArg, task_filter: taskFilter ?? null },
                aggregate: a,
                runs: filtered,
            },
            null,
            2,
        ),
    );
    process.exit(0);
}

printHuman(filtered, aggregate(filtered));
process.exit(0);
