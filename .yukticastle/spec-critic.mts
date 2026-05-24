// YuktiCastle Spec Critic — first-cut role from the multi-role
// architecture (ROLES.md / ENHANCEMENTS.md #15).
//
// Runs BEFORE the implementer phase. Reads the task spec, sends it
// to a free OpenRouter model (Llama 3.3 70b by default), and surfaces
// a critique: ambiguity flags, missing acceptance criteria, hidden
// assumptions, scope concerns. The operator reads the critique
// alongside their original spec and decides whether to refine or
// proceed.
//
// Why this role is first per ROLES.md adoption order:
//   - Cheapest (free model, ~5 sec, no tool use)
//   - Highest catch rate (the most common YuktiCastle failure mode
//     is "vague spec costs 2-3 implementer iterations")
//   - Doesn't require the full DAG executor — slots in as a
//     pre-Phase-1 hook in main.mts via an env-var opt-in
//
// Two invocation modes:
//
//   1. Standalone CLI: `npm run agents:spec-critic -- tasks/foo.md`
//      One-shot critique. Operator runs it before launching the
//      agent, reads, refines.
//
//   2. Auto-invoked from main.mts when YUKTICASTLE_SPEC_CRITIC=true.
//      Runs before Phase 1. Output streams to operator + run log.
//      Does NOT block the run (operator can refine task and re-run
//      if the critique surfaces real issues).
//
// Roadmap: docs/ENHANCEMENTS.md item #15 (DAG-driven role expansion)
// — Phase 1 of N. Future PRs add Security Auditor, Migration
// Auditor, Doc Writer, etc. plus the rule-based Router.
//
// No new deps — uses native fetch, reads .env if present.

import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// ============================================================
// Types — exported so main.mts can import + use programmatically
// ============================================================

export interface CritiqueResult {
    critique: string;            // markdown body
    model_used: string;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;            // estimated; 0 for free models
    elapsed_ms: number;
    error?: string;              // populated when the critique failed
}

export interface CritiqueOptions {
    model?: string;
    apiKey?: string;
    timeoutMs?: number;
    quiet?: boolean;
}

const DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free";
const SYSTEM_PROMPT = `You are a senior engineer reviewing a task spec that another engineer is about to hand to an AI coding agent. Your job is to flag issues with the SPEC itself, not the implementation. Focus on:

- **Ambiguity** — phrases the agent could interpret multiple ways
- **Missing acceptance criteria** — what would the reviewer check?
- **Hidden assumptions** — things the agent would need to guess
- **Scope concerns** — too big to be one task, or too small to need a spec
- **Risky omissions** — security, migration safety, error paths
- **Naming / file-path concerns** — vague pointers like "the storage layer" vs explicit paths

Be concise. Lead with the most actionable issue. If the spec is solid, say so in one line and stop. If you have nothing to add, output exactly: "No issues found."

Output format: markdown bullet list, max ~10 bullets. Each bullet is 1-2 sentences.`;

// ============================================================
// Minimal .env loader (mirrors the pattern in ask.mts so this
// file is self-contained — when the OpenRouter provider lands as
// a shared module, refactor to use it)
// ============================================================

function loadProjectEnv(): void {
    const envPath = resolvePath(process.cwd(), ".yukticastle/.env");
    if (!existsSync(envPath)) return;
    try {
        const raw = readFileSync(envPath, "utf8");
        for (const rawLine of raw.split(/\r?\n/)) {
            const line = rawLine.trim();
            if (!line || line.startsWith("#")) continue;
            const eq = line.indexOf("=");
            if (eq === -1) continue;
            const key = line.slice(0, eq).trim();
            let value = line.slice(eq + 1).trim();
            if (
                (value.startsWith('"') && value.endsWith('"')) ||
                (value.startsWith("'") && value.endsWith("'"))
            ) {
                value = value.slice(1, -1);
            }
            if (process.env[key] === undefined) process.env[key] = value;
        }
    } catch {
        // best-effort
    }
}

// ============================================================
// Public API — called by main.mts when YUKTICASTLE_SPEC_CRITIC=true
// ============================================================

export async function critiqueSpec(
    taskContent: string,
    opts: CritiqueOptions = {},
): Promise<CritiqueResult> {
    const start = Date.now();
    const model =
        opts.model ??
        process.env.YUKTICASTLE_SPEC_CRITIC_MODEL ??
        process.env.YUKTICASTLE_OPENROUTER_MODEL ??
        DEFAULT_MODEL;
    const apiKey = opts.apiKey ?? process.env.OPENROUTER_API_KEY ?? "";

    const baseResult: Omit<CritiqueResult, "critique" | "error"> = {
        model_used: model,
        tokens_in: 0,
        tokens_out: 0,
        cost_usd: 0,
        elapsed_ms: 0,
    };

    if (!apiKey) {
        return {
            ...baseResult,
            critique: "",
            error: "OPENROUTER_API_KEY not set — spec critic skipped (set the key in .yukticastle/.env to enable)",
            elapsed_ms: Date.now() - start,
        };
    }

    const controller = new AbortController();
    const timeoutMs = opts.timeoutMs ?? 30_000;
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    let res: Response;
    try {
        res = await fetch(
            "https://openrouter.ai/api/v1/chat/completions",
            {
                method: "POST",
                headers: {
                    Authorization: `Bearer ${apiKey}`,
                    "Content-Type": "application/json",
                    "HTTP-Referer":
                        "https://github.com/sandhipveera/yukticastle",
                    "X-Title": "yukticastle-spec-critic",
                },
                body: JSON.stringify({
                    model,
                    messages: [
                        { role: "system", content: SYSTEM_PROMPT },
                        {
                            role: "user",
                            content: `Review this task spec:\n\n---\n${taskContent}\n---`,
                        },
                    ],
                    max_tokens: 1500,
                    temperature: 0.2,
                }),
                signal: controller.signal,
            },
        );
    } catch (err) {
        clearTimeout(timer);
        return {
            ...baseResult,
            critique: "",
            error: `Network error reaching OpenRouter: ${(err as Error).message}`,
            elapsed_ms: Date.now() - start,
        };
    }
    clearTimeout(timer);

    if (!res.ok) {
        const body = (await res.text()).slice(0, 500);
        return {
            ...baseResult,
            critique: "",
            error: `OpenRouter ${res.status}: ${body}`,
            elapsed_ms: Date.now() - start,
        };
    }

    const json: any = await res.json().catch(() => ({}));
    const content = json?.choices?.[0]?.message?.content ?? "";
    const usage = json?.usage ?? {};

    return {
        ...baseResult,
        critique: typeof content === "string" ? content.trim() : String(content),
        model_used: json?.model ?? model,
        tokens_in: Number(usage.prompt_tokens ?? 0),
        tokens_out: Number(usage.completion_tokens ?? 0),
        cost_usd: model.endsWith(":free") ? 0 : 0, // OpenRouter doesn't expose
        // per-call billing in the chat-completion response; track 0
        // for free models, defer real cost tracking to runs.jsonl
        // when paid models are routed (#11 follow-up).
        elapsed_ms: Date.now() - start,
    };
}

// ============================================================
// CLI entry — standalone usage: `npm run agents:spec-critic -- tasks/foo.md`
// ============================================================

const isMain = import.meta.url === `file://${process.argv[1]}`;

if (isMain) {
    loadProjectEnv();

    const argv = process.argv.slice(2);
    const QUIET = argv.includes("--quiet");
    const JSON_MODE = argv.includes("--json");
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

    const positional = argv.find((a) => !a.startsWith("--"));
    if (!positional) {
        console.error(
            c.red(
                `Usage: npm run agents:spec-critic -- tasks/foo.md [--quiet] [--json]`,
            ),
        );
        process.exit(2);
    }

    const taskPath = resolvePath(positional);
    if (!existsSync(taskPath)) {
        console.error(c.red(`Task file not found: ${positional}`));
        process.exit(2);
    }

    const taskContent = readFileSync(taskPath, "utf8");

    if (!QUIET && !JSON_MODE) {
        console.log(
            c.bold(`Spec Critic:`) + ` ${positional}` + c.dim(` (model selecting…)`),
        );
    }

    const result = await critiqueSpec(taskContent, { quiet: QUIET });

    if (JSON_MODE) {
        console.log(JSON.stringify(result, null, 2));
        process.exit(result.error ? 1 : 0);
    }

    if (result.error) {
        console.error(c.yellow(`⚠ ${result.error}`));
        process.exit(1);
    }

    if (!QUIET) {
        console.log(
            c.dim(
                `[spec-critic] model=${result.model_used} tokens=${result.tokens_in}→${result.tokens_out} elapsed=${result.elapsed_ms}ms`,
            ),
        );
        console.log("");
    }
    console.log(result.critique);
    process.exit(0);
}
