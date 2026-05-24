// YuktiCastle OpenRouter provider — text-in-text-out HTTP client.
//
// Provides chat-completion access to OpenRouter's catalog (free
// models like Llama 3.3 70b, Gemini 2.0 Flash, DeepSeek R1, Qwen
// 2.5 72b, plus paid models from every major lab). Used by:
//   - .yukticastle/ask.mts (one-shot CLI prompts; today)
//   - Future role-DAG executor (ENHANCEMENTS.md #15) for tool-free
//     roles like Spec Critic, Doc Writer, Migration Auditor analysis,
//     Release Manager PR-body generation
//
// Free models often don't support tool use reliably. This provider
// is intentionally text-in-text-out only — anything that needs to
// edit files / run commands stays on Claude Code or Codex CLI.
//
// Roadmap: docs/ENHANCEMENTS.md item #11. Standalone usable today;
// ENHANCEMENTS.md #15 will wire this into the role DAG.
//
// No new deps — pure Node `fetch` (stable in 20+).

interface ChatMessage {
    role: "system" | "user" | "assistant";
    content: string;
}

export interface ChatRequest {
    model: string;
    messages: ChatMessage[];
    maxTokens?: number;
    temperature?: number;
    // OpenRouter passes these through to the underlying provider;
    // the model itself decides what's honored.
}

export interface ChatResponse {
    content: string;
    model: string;        // the model that actually served the call
                         //   (sometimes different from request.model
                         //    when OpenRouter falls back)
    usage: {
        prompt_tokens: number;
        completion_tokens: number;
        total_tokens: number;
    };
    finish_reason: string | null;
}

export interface ChatOptions {
    apiKey?: string;       // defaults to process.env.OPENROUTER_API_KEY
    timeoutMs?: number;    // default 60s; free models can be slow
    referer?: string;      // OpenRouter recommends a Referer header
                          // for analytics / rate-limit attribution
}

const DEFAULT_BASE_URL = "https://openrouter.ai/api/v1";

export class OpenRouterError extends Error {
    constructor(
        message: string,
        public readonly status: number | null,
        public readonly body: string | null,
    ) {
        super(message);
        this.name = "OpenRouterError";
    }
}

export async function chat(
    req: ChatRequest,
    opts: ChatOptions = {},
): Promise<ChatResponse> {
    const apiKey = opts.apiKey ?? process.env.OPENROUTER_API_KEY ?? "";
    if (!apiKey) {
        throw new OpenRouterError(
            "OPENROUTER_API_KEY not set. Add it to .yukticastle/.env or pass via opts.apiKey.",
            null,
            null,
        );
    }

    const url = `${DEFAULT_BASE_URL}/chat/completions`;
    const body = JSON.stringify({
        model: req.model,
        messages: req.messages,
        max_tokens: req.maxTokens,
        temperature: req.temperature,
    });

    const headers: Record<string, string> = {
        Authorization: `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        // OpenRouter recommends these for analytics + rate-limit tier
        // attribution. Operator projects should pass a real referer
        // when they wire this in; default is generic.
        "HTTP-Referer":
            opts.referer ?? "https://github.com/sandhipveera/yukticastle",
        "X-Title": "yukticastle",
    };

    const controller = new AbortController();
    const timeoutMs = opts.timeoutMs ?? 60_000;
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    let res: Response;
    try {
        res = await fetch(url, {
            method: "POST",
            headers,
            body,
            signal: controller.signal,
        });
    } catch (err) {
        clearTimeout(timer);
        const msg = (err as Error)?.message ?? String(err);
        if (msg.includes("aborted")) {
            throw new OpenRouterError(
                `Request timed out after ${timeoutMs}ms (free models can be slow — try a paid model or raise opts.timeoutMs).`,
                null,
                null,
            );
        }
        throw new OpenRouterError(
            `Network error: ${msg}`,
            null,
            null,
        );
    }
    clearTimeout(timer);

    const text = await res.text();
    if (!res.ok) {
        throw new OpenRouterError(
            `OpenRouter ${res.status}: ${res.statusText}`,
            res.status,
            text.slice(0, 2000),
        );
    }

    let json: any;
    try {
        json = JSON.parse(text);
    } catch {
        throw new OpenRouterError(
            "Response was not valid JSON",
            res.status,
            text.slice(0, 500),
        );
    }

    const choice = json?.choices?.[0];
    const content = choice?.message?.content ?? "";
    const usage = json?.usage ?? {};
    return {
        content: typeof content === "string" ? content : String(content),
        model: json?.model ?? req.model,
        usage: {
            prompt_tokens: Number(usage.prompt_tokens ?? 0),
            completion_tokens: Number(usage.completion_tokens ?? 0),
            total_tokens: Number(usage.total_tokens ?? 0),
        },
        finish_reason: choice?.finish_reason ?? null,
    };
}

// Suggested free models — rotate as the catalog churns. Picked for
// reliability, instruction-following, and large context windows.
// Re-verify availability when you actually use these (free tier
// shifts quarterly).
export const FREE_MODELS = {
    llama: "meta-llama/llama-3.3-70b-instruct:free",
    gemini: "google/gemini-2.0-flash-exp:free",
    deepseek: "deepseek/deepseek-r1:free",
    qwen: "qwen/qwen-2.5-72b-instruct:free",
} as const;
