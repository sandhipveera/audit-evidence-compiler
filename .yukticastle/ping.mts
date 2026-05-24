// YuktiCastle agents:ping — provider-health smoke test.
//
// What it does
// ────────────
// For each configured provider/model pair, fires the cheapest
// possible liveness probe and reports green/red + round-trip ms.
// Pairs come from two sources:
//
//   1. `.yukticastle/roles.json` phases that are not gated off
//      (loaded via `loadDagConfig()` + `shouldRunPhase()`).
//   2. Any provider whose API key is present in env, even if no
//      phase uses it. OpenRouter is the common case — operators
//      keep `OPENROUTER_API_KEY` set for `agents:ask` (one-shot
//      prompts) but might have spec_critic gated off.
//
// Per-provider probe strategy
// ───────────────────────────
//
//   Anthropic (OAuth)         — verify the local credential is
//                                 present + not expired. No API
//                                 call: token validity is locally
//                                 checkable from the Keychain JSON
//                                 (expiresAt field) or env-token
//                                 prefix sanity.
//   Anthropic (API key)       — POST /v1/messages with max_tokens=1.
//   OpenAI/codex (OAuth)      — shell `codex exec --model X ok`.
//                                 Same pattern as PR #48's refresh
//                                 helper. Side-benefit: triggers an
//                                 access_token refresh if stale.
//   OpenAI/codex (API key)    — POST /v1/chat/completions max_tokens=1.
//   OpenRouter (API key)      — POST /api/v1/chat/completions
//                                 max_tokens=1 to the configured model.
//
// Exit codes
// ──────────
//   0  All probes green.
//   1  At least one probe red.
//   2  Couldn't determine what to probe (no providers configured).
//
// Flags
// ─────
//   --json       Machine-readable output instead of pretty table.
//   --quiet      Hide green rows; only print red/yellow.
//   --no-color   Disable ANSI (auto-off when stdout is not a TTY).
//   --timeout=N  Per-probe timeout in ms. Default 30000.
//
// Cost
// ────
// Each probe is 1 token. Free on ChatGPT/Claude MAX quotas. Sub-cent
// on API-key paths. Negligible.
//
// Composition with other tools
// ────────────────────────────
//   agents:doctor — broader preflight: Docker, image, env-var
//                   presence, OAuth expiry warnings. Doesn't make
//                   API calls today (ENHANCEMENTS.md #17 will add
//                   that via --auto-refresh).
//   agents:ping   — narrower: assumes setup is correct, just
//                   verifies the wire works end-to-end RIGHT NOW.
//                   Use before kicking off a long YC run if you're
//                   not sure auth is fresh.
//   PR #48        — pre-run codex refresh fires automatically from
//                   main.mts. agents:ping is the manual-verification
//                   complement.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { platform } from "node:os";
import { resolve as resolvePath } from "node:path";

// ============================================================
// CLI flags
// ============================================================

const argv = process.argv.slice(2);
const QUIET = argv.includes("--quiet");
const JSON_MODE = argv.includes("--json");
const NO_COLOR =
    argv.includes("--no-color") || !process.stdout.isTTY || JSON_MODE;
const TIMEOUT_MS = (() => {
    const flag = argv.find((a) => a.startsWith("--timeout="));
    if (!flag) return 30_000;
    const n = Number(flag.slice("--timeout=".length));
    return Number.isFinite(n) && n > 0 ? n : 30_000;
})();

// Color helpers
const C_GREEN = NO_COLOR ? "" : "[32m";
const C_RED = NO_COLOR ? "" : "[31m";
const C_YELLOW = NO_COLOR ? "" : "[33m";
const C_DIM = NO_COLOR ? "" : "[2m";
const C_BOLD = NO_COLOR ? "" : "[1m";
const C_RESET = NO_COLOR ? "" : "[0m";

// ============================================================
// Types
// ============================================================

interface PingTarget {
    provider: "anthropic" | "openai" | "openrouter";
    model: string;
    /** Role label for context — "implementer", "reviewer", "ask", etc. */
    role: string;
}

interface PingResult {
    provider: string;
    model: string;
    role: string;
    auth_source: string;
    success: boolean;
    duration_ms: number;
    /** Short success/skip context, e.g. "token expires in 4h 22min". */
    detail?: string;
    /** Populated only on failure. */
    error?: string;
    /** Suggested remediation, shown under the row on failure. */
    remediation?: string;
}

// ============================================================
// Auth resolvers — duplicate of main.mts patterns. ENHANCEMENTS.md
// #12 (provider-pluggable auth abstraction) will deduplicate these.
// ============================================================

function tryKeychainEntry(service: string): string | undefined {
    if (platform() !== "darwin") return undefined;
    try {
        const r = spawnSync(
            "security",
            ["find-generic-password", "-s", service, "-w"],
            { encoding: "utf8", timeout: 3000 },
        );
        if (r.status !== 0 || !r.stdout) return undefined;
        return r.stdout.trim();
    } catch {
        return undefined;
    }
}

interface AnthropicAuth {
    kind:
        | "macos-keychain-oauth"
        | "env-oauth"
        | "api-key"
        | "keychain-api-key";
    /** OAuth access_token, OR API key. */
    token: string;
    /** Unix ms — only populated for Keychain OAuth where it's known. */
    expiresAtMs?: number;
}

function resolveAnthropicAuth(): AnthropicAuth | undefined {
    // 1. macOS Keychain OAuth (the free MAX-billed path)
    const keychainRaw = tryKeychainEntry("Claude Code-credentials");
    if (keychainRaw) {
        try {
            const json = JSON.parse(keychainRaw);
            const token = json?.claudeAiOauth?.accessToken;
            const expiresAt = json?.claudeAiOauth?.expiresAt;
            if (typeof token === "string" && token.length > 0) {
                return {
                    kind: "macos-keychain-oauth",
                    token,
                    expiresAtMs:
                        typeof expiresAt === "number" ? expiresAt : undefined,
                };
            }
        } catch {
            /* malformed Keychain entry — fall through */
        }
    }
    // 2. Env-var OAuth (Linux / Codespace path)
    const envOAuth = process.env.CLAUDE_CODE_OAUTH_TOKEN?.trim();
    if (envOAuth?.startsWith("sk-ant-oat")) {
        return { kind: "env-oauth", token: envOAuth };
    }
    // 3. API key
    const apiKey = process.env.ANTHROPIC_API_KEY?.trim();
    if (apiKey?.startsWith("sk-ant-api")) {
        return { kind: "api-key", token: apiKey };
    }
    // 4. Keychain API key (macOS)
    const keychainApi = tryKeychainEntry("yukticastle-anthropic-api-key");
    if (keychainApi?.startsWith("sk-ant-api")) {
        return { kind: "keychain-api-key", token: keychainApi };
    }
    return undefined;
}

function hasCodexOAuth(): boolean {
    const home = process.env.HOME;
    if (!home) return false;
    const path = resolvePath(home, ".codex", "auth.json");
    if (!existsSync(path)) return false;
    try {
        const raw = readFileSync(path, "utf8");
        const parsed = JSON.parse(raw);
        return (
            parsed?.auth_mode === "chatgpt" &&
            typeof parsed?.tokens?.access_token === "string" &&
            typeof parsed?.tokens?.refresh_token === "string"
        );
    } catch {
        return false;
    }
}

// ============================================================
// Per-provider probes
// ============================================================

async function fetchWithTimeout(
    url: string,
    init: RequestInit,
    timeoutMs: number,
): Promise<Response> {
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...init, signal: controller.signal });
    } finally {
        clearTimeout(t);
    }
}

async function pingAnthropic(target: PingTarget): Promise<PingResult> {
    const startedAt = Date.now();
    const auth = resolveAnthropicAuth();
    if (!auth) {
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "none",
            success: false,
            duration_ms: 0,
            error: "no anthropic auth resolved",
            remediation:
                "run `claude` to set up Keychain OAuth (macOS) OR `claude setup-token` for env OAuth OR set ANTHROPIC_API_KEY",
        };
    }
    // OAuth paths: we don't fire an API call. Both Keychain and env OAuth
    // route through Anthropic's Claude Code internal API (not the public
    // /v1/messages endpoint), and we don't want to bake the undocumented
    // OAuth-API contract into ping.mts. Instead: verify the local
    // credential is intact and unexpired. main.mts catches actual wire
    // problems at run-start anyway.
    if (auth.kind === "macos-keychain-oauth") {
        const duration_ms = Date.now() - startedAt;
        if (auth.expiresAtMs === undefined) {
            return {
                provider: target.provider,
                model: target.model,
                role: target.role,
                auth_source: auth.kind,
                success: true,
                duration_ms,
                detail: "Keychain entry present (no expiresAt — can't verify freshness)",
            };
        }
        const remainingMs = auth.expiresAtMs - Date.now();
        if (remainingMs <= 0) {
            return {
                provider: target.provider,
                model: target.model,
                role: target.role,
                auth_source: auth.kind,
                success: false,
                duration_ms,
                error: `Keychain OAuth token ${formatRemaining(remainingMs)}`,
                remediation: "run `npm run claude:login` or any `claude` invocation to refresh",
            };
        }
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: auth.kind,
            success: true,
            duration_ms,
            detail: `token expires in ${formatRemaining(remainingMs)}`,
        };
    }
    if (auth.kind === "env-oauth") {
        // No exp embedded in sk-ant-oat tokens (they're long-lived).
        // Presence + prefix is the strongest local signal.
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: auth.kind,
            success: true,
            duration_ms: Date.now() - startedAt,
            detail: "CLAUDE_CODE_OAUTH_TOKEN present (long-lived token, no local expiry check)",
        };
    }
    // API-key paths — actually fire the request.
    try {
        const res = await fetchWithTimeout(
            "https://api.anthropic.com/v1/messages",
            {
                method: "POST",
                headers: {
                    "content-type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": auth.token,
                },
                body: JSON.stringify({
                    model: target.model,
                    max_tokens: 1,
                    messages: [{ role: "user", content: "ok" }],
                }),
            },
            TIMEOUT_MS,
        );
        const duration_ms = Date.now() - startedAt;
        if (!res.ok) {
            const body = (await res.text()).slice(0, 200);
            return {
                provider: target.provider,
                model: target.model,
                role: target.role,
                auth_source: auth.kind,
                success: false,
                duration_ms,
                error: `HTTP ${res.status}: ${body}`,
                remediation:
                    res.status === 401
                        ? "API key rejected — verify ANTHROPIC_API_KEY"
                        : res.status === 404
                          ? `model "${target.model}" not found — check roles.json`
                          : undefined,
            };
        }
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: auth.kind,
            success: true,
            duration_ms,
            detail: `HTTP 200`,
        };
    } catch (err) {
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: auth.kind,
            success: false,
            duration_ms: Date.now() - startedAt,
            error: (err as Error).message,
        };
    }
}

async function pingOpenAI(target: PingTarget): Promise<PingResult> {
    const startedAt = Date.now();
    // Two paths — codex OAuth (free on ChatGPT subscription) vs API key.
    // Prefer OAuth if present because that's what main.mts will actually
    // use at reviewer time.
    if (hasCodexOAuth()) {
        // `-codex` models 400 on ChatGPT accounts (PR #48's pickRefreshModel
        // documents this). Substitute a broadly-available default for the
        // probe so the ping itself doesn't fail for a known-bad reason.
        const probeModel =
            target.model.endsWith("-codex") || target.model.includes("-codex-")
                ? "gpt-5.4-mini"
                : target.model;
        const r = spawnSync("codex", ["exec", "--model", probeModel, "ok"], {
            timeout: TIMEOUT_MS,
            encoding: "utf8",
            stdio: ["ignore", "pipe", "pipe"],
        });
        const duration_ms = Date.now() - startedAt;
        if (r.error || r.status !== 0) {
            const stderr =
                (typeof r.stderr === "string" && r.stderr) ||
                r.error?.message ||
                `exit ${r.status ?? "?"}`;
            return {
                provider: target.provider,
                model: target.model,
                role: target.role,
                auth_source: "codex-oauth",
                success: false,
                duration_ms,
                error: stderr.slice(0, 300),
                remediation:
                    stderr.includes("401") || stderr.includes("Unauthorized")
                        ? "run `npm run codex:login` to refresh ~/.codex/auth.json"
                        : stderr.includes("not supported when using Codex with a ChatGPT account")
                          ? `model "${target.model}" requires OPENAI_API_KEY (not ChatGPT OAuth)`
                          : undefined,
            };
        }
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "codex-oauth",
            success: true,
            duration_ms,
            detail:
                probeModel === target.model
                    ? "codex exec exit 0"
                    : `codex exec exit 0 (probed via ${probeModel} since ${target.model} is OAuth-incompatible)`,
        };
    }
    const apiKey = process.env.OPENAI_API_KEY?.trim();
    if (!apiKey?.startsWith("sk-")) {
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "none",
            success: false,
            duration_ms: 0,
            error: "no openai auth resolved (no ~/.codex/auth.json AND no OPENAI_API_KEY)",
            remediation: "run `codex login` for free OAuth, or set OPENAI_API_KEY in .yukticastle/.env",
        };
    }
    try {
        const res = await fetchWithTimeout(
            "https://api.openai.com/v1/chat/completions",
            {
                method: "POST",
                headers: {
                    "content-type": "application/json",
                    authorization: `Bearer ${apiKey}`,
                },
                body: JSON.stringify({
                    model: target.model,
                    max_tokens: 1,
                    messages: [{ role: "user", content: "ok" }],
                }),
            },
            TIMEOUT_MS,
        );
        const duration_ms = Date.now() - startedAt;
        if (!res.ok) {
            const body = (await res.text()).slice(0, 200);
            return {
                provider: target.provider,
                model: target.model,
                role: target.role,
                auth_source: "api-key",
                success: false,
                duration_ms,
                error: `HTTP ${res.status}: ${body}`,
                remediation:
                    res.status === 401
                        ? "API key rejected — verify OPENAI_API_KEY"
                        : res.status === 404
                          ? `model "${target.model}" not found — check roles.json`
                          : undefined,
            };
        }
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "api-key",
            success: true,
            duration_ms,
            detail: "HTTP 200",
        };
    } catch (err) {
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "api-key",
            success: false,
            duration_ms: Date.now() - startedAt,
            error: (err as Error).message,
        };
    }
}

async function pingOpenRouter(target: PingTarget): Promise<PingResult> {
    const startedAt = Date.now();
    const apiKey = process.env.OPENROUTER_API_KEY?.trim();
    if (!apiKey) {
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "none",
            success: false,
            duration_ms: 0,
            error: "OPENROUTER_API_KEY not set",
            remediation: "https://openrouter.ai/keys → add to .yukticastle/.env",
        };
    }
    try {
        const res = await fetchWithTimeout(
            "https://openrouter.ai/api/v1/chat/completions",
            {
                method: "POST",
                headers: {
                    "content-type": "application/json",
                    authorization: `Bearer ${apiKey}`,
                    "http-referer": "https://github.com/sandhipveera/sandcastle-template",
                    "x-title": "yukticastle-ping",
                },
                body: JSON.stringify({
                    model: target.model,
                    max_tokens: 1,
                    messages: [{ role: "user", content: "ok" }],
                }),
            },
            TIMEOUT_MS,
        );
        const duration_ms = Date.now() - startedAt;
        if (!res.ok) {
            const body = (await res.text()).slice(0, 200);
            return {
                provider: target.provider,
                model: target.model,
                role: target.role,
                auth_source: "api-key",
                success: false,
                duration_ms,
                error: `HTTP ${res.status}: ${body}`,
                remediation:
                    res.status === 401
                        ? "API key rejected — verify OPENROUTER_API_KEY"
                        : res.status === 402
                          ? `model "${target.model}" requires OpenRouter credits (top up or pick a :free variant)`
                          : undefined,
            };
        }
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "api-key",
            success: true,
            duration_ms,
            detail: "HTTP 200",
        };
    } catch (err) {
        return {
            provider: target.provider,
            model: target.model,
            role: target.role,
            auth_source: "api-key",
            success: false,
            duration_ms: Date.now() - startedAt,
            error: (err as Error).message,
        };
    }
}

// ============================================================
// Target discovery
// ============================================================

async function discoverTargets(): Promise<PingTarget[]> {
    const targets: PingTarget[] = [];
    const seen = new Set<string>();
    function add(provider: PingTarget["provider"], model: string, role: string) {
        const key = `${provider}::${model}`;
        if (seen.has(key)) return;
        seen.add(key);
        targets.push({ provider, model, role });
    }

    // 1. Phases from roles.json that are not gated off.
    try {
        const { loadDagConfig, shouldRunPhase } = await import("./dag.js");
        const config = loadDagConfig();
        for (const phase of config.phases) {
            if (!shouldRunPhase(phase)) continue;
            add(
                phase.provider as PingTarget["provider"],
                phase.model,
                phase.role,
            );
        }
    } catch (err) {
        console.warn(
            `${C_YELLOW}[ping] could not load .yukticastle/roles.json — falling back to env-var-only discovery${C_RESET}`,
        );
        console.warn(`${C_DIM}        ${(err as Error).message}${C_RESET}`);
    }

    // 2. OpenRouter for `agents:ask`, even if no phase uses it. The
    //    default ask model is the free Llama 3.3 unless
    //    YUKTICASTLE_OPENROUTER_MODEL is set.
    if (process.env.OPENROUTER_API_KEY?.trim()) {
        const askModel =
            process.env.YUKTICASTLE_OPENROUTER_MODEL?.trim() ||
            "meta-llama/llama-3.3-70b-instruct:free";
        add("openrouter", askModel, "ask");
    }

    return targets;
}

// ============================================================
// Output rendering
// ============================================================

function formatRemaining(ms: number): string {
    const past = ms < 0;
    const absMin = Math.abs(Math.round(ms / 60_000));
    const formatted = (() => {
        if (absMin < 60) return `${absMin} min`;
        const h = Math.floor(absMin / 60);
        const m = absMin % 60;
        if (h < 24) return `${h}h ${m}min`;
        const d = Math.floor(h / 24);
        const remH = h % 24;
        return `${d}d ${remH}h`;
    })();
    return past ? `expired ${formatted} ago` : formatted;
}

function statusGlyph(r: PingResult): string {
    if (r.success) return `${C_GREEN}✓${C_RESET}`;
    return `${C_RED}✗${C_RESET}`;
}

function renderPretty(results: PingResult[]): void {
    console.log(``);
    console.log(
        `${C_BOLD}[yukticastle] agents:ping — provider health smoke test${C_RESET}`,
    );
    console.log(``);
    if (results.length === 0) {
        console.log(
            `${C_YELLOW}  no providers to probe — empty roles.json and no env-var auth.${C_RESET}`,
        );
        return;
    }
    // Column widths
    const wProvider = Math.max(...results.map((r) => r.provider.length), 8);
    const wModel = Math.max(...results.map((r) => r.model.length), 12);
    const wAuth = Math.max(...results.map((r) => r.auth_source.length), 10);
    for (const r of results) {
        if (QUIET && r.success) continue;
        const glyph = statusGlyph(r);
        const ms = r.success ? `${r.duration_ms}ms` : "FAILED";
        const msCol = r.success ? `${C_DIM}${ms}${C_RESET}` : `${C_RED}${ms}${C_RESET}`;
        console.log(
            `  ${glyph} ${r.provider.padEnd(wProvider)} ${r.model.padEnd(wModel)} ${C_DIM}${r.auth_source.padEnd(wAuth)}${C_RESET} ${msCol}`,
        );
        if (r.detail && r.success) {
            console.log(`      ${C_DIM}${r.detail}${C_RESET}`);
        }
        if (r.error) {
            console.log(`      ${C_RED}error: ${r.error}${C_RESET}`);
            if (r.remediation) {
                console.log(`      ${C_YELLOW}→ ${r.remediation}${C_RESET}`);
            }
        }
    }
    console.log(``);
    const green = results.filter((r) => r.success).length;
    const total = results.length;
    if (green === total) {
        console.log(
            `${C_GREEN}All providers healthy (${green}/${total} green).${C_RESET}`,
        );
    } else {
        console.log(
            `${C_RED}${green}/${total} providers healthy. ${total - green} failed.${C_RESET}`,
        );
    }
    console.log(``);
}

function renderJson(results: PingResult[]): void {
    const summary = {
        total: results.length,
        green: results.filter((r) => r.success).length,
        red: results.filter((r) => !r.success).length,
        timeout_ms: TIMEOUT_MS,
        results,
    };
    console.log(JSON.stringify(summary, null, 2));
}

// ============================================================
// Main
// ============================================================

async function main(): Promise<number> {
    const targets = await discoverTargets();
    if (targets.length === 0) {
        if (JSON_MODE) {
            renderJson([]);
        } else {
            renderPretty([]);
        }
        return 2;
    }
    // Parallel for wall-time, but each probe is independent. Promise.allSettled
    // so one network hiccup doesn't tank the whole report.
    const promises = targets.map((t) => {
        switch (t.provider) {
            case "anthropic":
                return pingAnthropic(t);
            case "openai":
                return pingOpenAI(t);
            case "openrouter":
                return pingOpenRouter(t);
        }
    });
    const settled = await Promise.allSettled(promises);
    const results: PingResult[] = settled.map((s, i) => {
        const t = targets[i]!;
        if (s.status === "fulfilled") return s.value;
        return {
            provider: t.provider,
            model: t.model,
            role: t.role,
            auth_source: "unknown",
            success: false,
            duration_ms: 0,
            error: `unhandled exception: ${(s.reason as Error).message}`,
        };
    });
    if (JSON_MODE) {
        renderJson(results);
    } else {
        renderPretty(results);
    }
    return results.every((r) => r.success) ? 0 : 1;
}

main()
    .then((code) => process.exit(code))
    .catch((err) => {
        console.error(`${C_RED}[ping] fatal: ${(err as Error).message}${C_RESET}`);
        process.exit(1);
    });
