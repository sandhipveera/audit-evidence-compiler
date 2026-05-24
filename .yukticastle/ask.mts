// YuktiCastle ask — `npm run agents:ask -- "your prompt"`.
//
// One-shot OpenRouter chat completion. Useful for:
//   - Summarizing a code review or PR diff
//   - Extracting patterns from learnings.jsonl entries
//   - Generating ad-hoc content (release notes, commit-message
//     polish, etc.)
//   - Testing the OpenRouter integration before more complex flows
//     (ENHANCEMENTS.md #15 role DAG) wire it in
//
// Flags:
//   --model=<id>      OpenRouter model id; default: free Llama 3.3 70b
//                     (or whatever YUKTICASTLE_OPENROUTER_MODEL env is set to)
//   --system=<text>   System message (sets behavior / role for the model)
//   --json            Print the full ChatResponse as JSON (for tooling)
//   --max-tokens=<n>  Cap output length; default 2000
//   --temperature=<n> Sampling temperature; default 0.3 (deterministic-ish)
//   --quiet           Suppress the diagnostic header line
//
// Reads prompt from positional args (joined with spaces) or stdin.
//
// Exit 0 on success, 1 on OpenRouter error, 2 on usage error.

// `./openrouter.js` is the TS-NodeNext convention: source is
// `openrouter.ts`, import path uses `.js` (post-compile extension).
// tsx resolves at runtime; tsc accepts in strict mode.
import { chat, FREE_MODELS, OpenRouterError } from "./openrouter.js";
import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// Minimal .env loader — reads `.yukticastle/.env` if present and
// applies any KEY=VALUE entries to process.env unless already set.
// Avoids requiring the npm script to use `tsx --env-file=...` (which
// hard-fails when the file is missing on Node 20).
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
            // Existing process.env values win (so shell exports
            // override file values — same precedence as Node's
            // built-in --env-file behavior).
            if (process.env[key] === undefined) process.env[key] = value;
        }
    } catch {
        // Best-effort — unreadable file is not fatal here.
    }
}
loadProjectEnv();

const argv = process.argv.slice(2);
const QUIET = argv.includes("--quiet");
const JSON_MODE = argv.includes("--json");
const NO_COLOR =
    argv.includes("--no-color") ||
    !process.stdout.isTTY ||
    process.env.NO_COLOR !== undefined;

function flagValue(name: string): string | undefined {
    const prefix = `--${name}=`;
    return argv.find((a) => a.startsWith(prefix))?.slice(prefix.length);
}

const model =
    flagValue("model") ??
    process.env.YUKTICASTLE_OPENROUTER_MODEL ??
    FREE_MODELS.llama;
const system = flagValue("system");
const maxTokens = Number(flagValue("max-tokens") ?? "2000");
const temperature = Number(flagValue("temperature") ?? "0.3");

// Positional args are the user prompt (joined with spaces) — but
// only those NOT starting with `--`.
const positional = argv.filter((a) => !a.startsWith("--"));

const c = {
    green: (s: string) => (NO_COLOR ? s : `\x1b[32m${s}\x1b[0m`),
    yellow: (s: string) => (NO_COLOR ? s : `\x1b[33m${s}\x1b[0m`),
    red: (s: string) => (NO_COLOR ? s : `\x1b[31m${s}\x1b[0m`),
    dim: (s: string) => (NO_COLOR ? s : `\x1b[2m${s}\x1b[0m`),
    bold: (s: string) => (NO_COLOR ? s : `\x1b[1m${s}\x1b[0m`),
};

let userPrompt: string;
if (positional.length > 0) {
    userPrompt = positional.join(" ");
} else if (!process.stdin.isTTY) {
    // Read from stdin (piped input).
    try {
        userPrompt = readFileSync(0, "utf8").trim();
    } catch {
        userPrompt = "";
    }
} else {
    console.error(
        c.red(
            `Usage: npm run agents:ask -- "your prompt" [--model=<id>] [--system=<text>] [--json]`,
        ),
    );
    console.error(
        c.dim(
            `Free model rotation: ${Object.entries(FREE_MODELS).map(([k, v]) => `${k}=${v}`).join(", ")}`,
        ),
    );
    process.exit(2);
}

if (!userPrompt) {
    console.error(c.red(`Empty prompt — provide one as args or via stdin.`));
    process.exit(2);
}

if (!QUIET && !JSON_MODE) {
    console.log(
        c.dim(
            `[ask] model=${model} max_tokens=${maxTokens} temperature=${temperature}`,
        ),
    );
}

const messages: { role: "system" | "user"; content: string }[] = [];
if (system) messages.push({ role: "system", content: system });
messages.push({ role: "user", content: userPrompt });

try {
    const resp = await chat({
        model,
        messages,
        maxTokens: Number.isFinite(maxTokens) ? maxTokens : undefined,
        temperature: Number.isFinite(temperature) ? temperature : undefined,
    });
    if (JSON_MODE) {
        console.log(JSON.stringify(resp, null, 2));
    } else {
        console.log(resp.content);
        if (!QUIET) {
            console.log("");
            console.log(
                c.dim(
                    `[ask] tokens: in=${resp.usage.prompt_tokens} out=${resp.usage.completion_tokens} total=${resp.usage.total_tokens} · finish=${resp.finish_reason ?? "?"} · model=${resp.model}`,
                ),
            );
        }
    }
    process.exit(0);
} catch (err) {
    if (err instanceof OpenRouterError) {
        console.error(c.red(`[ask] OpenRouter error: ${err.message}`));
        if (err.status === 401) {
            console.error(
                c.dim(
                    `Hint: OPENROUTER_API_KEY missing or invalid. Get a key at https://openrouter.ai/keys`,
                ),
            );
        } else if (err.status === 402) {
            console.error(
                c.dim(
                    `Hint: model requires credit. Either use a :free model (${Object.values(FREE_MODELS).join(", ")}) or top up your OpenRouter account.`,
                ),
            );
        } else if (err.status === 429) {
            console.error(
                c.dim(
                    `Hint: rate-limited. Free tier is ~20 req/min and 200 req/day per model — try a different :free model or wait.`,
                ),
            );
        }
        if (err.body) {
            console.error(c.dim(`Body: ${err.body.slice(0, 500)}`));
        }
        process.exit(1);
    }
    console.error(c.red(`[ask] unexpected error: ${(err as Error).message}`));
    process.exit(1);
}
