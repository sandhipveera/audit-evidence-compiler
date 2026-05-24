// Sandcastle pre-flight diagnostics — `npm run agents:doctor`.
//
// Runs eight environment checks and prints a numbered remediation
// list for any failures. Designed so the operator can replace the
// "three lines you must remember every time" discipline (per
// CHEATSHEET.md) with a single command:
//
//     npm run agents:doctor
//
// Exit 0 if all checks pass or warn; exit 1 if any check fails.
//
// Flags:
//   --quiet         Print only failing/warning checks
//   --json          Machine-readable output (for tooling / CI)
//   --no-color      Disable ANSI colors (auto-disabled when not a TTY)
//   --auto-refresh  When the OAuth-expiry check fails, invoke
//                   `.yukticastle/claude-login.sh` to refresh the
//                   keychain token, then re-run the check. Default
//                   off (preserves explicit confirmation).
//
// The intent is "manual-only invocation initially." A follow-up
// commit can wire auto-run into main.mts behind --skip-doctor once
// the UX is proven; doing it now would add a pre-flight blocker to
// every project that pulls a new template version.
//
// No new deps — pure Node built-ins.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, statSync } from "node:fs";
import { platform } from "node:os";
import { resolve as resolvePath } from "node:path";

// Inline keychain peek (parallel to main.mts's tryKeychainApiKey).
// Kept local to avoid a cross-file import — when the keychain.ts
// shared module lands (force-pushed v2 of PR #27 needs a re-ship),
// this can be replaced with `import { tryKeychainProviderKey } from
// "./keychain.js"`.
//
// Returns undefined when no entry / bad prefix / non-macOS.
function tryKeychainApiKey(
    service: string,
    expectedPrefix: string,
): string | undefined {
    try {
        const r = spawnSync(
            "security",
            ["find-generic-password", "-s", service, "-w"],
            { encoding: "utf8", timeout: 3000 },
        );
        if (r.status !== 0 || !r.stdout) return undefined;
        const key = r.stdout.trim();
        if (!key.startsWith(expectedPrefix)) return undefined;
        return key;
    } catch {
        return undefined;
    }
}

// ============================================================
// Types
// ============================================================

type Status = "pass" | "warn" | "fail" | "skip";

interface CheckResult {
    name: string;
    status: Status;
    detail?: string;       // one-line context (e.g. "docker 29.4.2")
    remediation?: string[]; // numbered fix lines, only shown on fail/warn
}

// ============================================================
// CLI flags
// ============================================================

const argv = process.argv.slice(2);
const QUIET = argv.includes("--quiet");
const JSON_MODE = argv.includes("--json");
// --auto-refresh: when the OAuth-expiry check fails (token expired
// or below buffer), spawn the project's claude-login.sh helper to
// trigger a keychain refresh, then re-run JUST the OAuth check.
// Without this flag, doctor reports the failure with manual remediation
// (default — preserves explicit confirmation). Roadmap: ENHANCEMENTS.md #17.
const AUTO_REFRESH = argv.includes("--auto-refresh");
const NO_COLOR =
    argv.includes("--no-color") ||
    !process.stdout.isTTY ||
    process.env.NO_COLOR !== undefined;

// ============================================================
// Tiny ANSI helpers (no chalk dep)
// ============================================================

const c = {
    green: (s: string) => (NO_COLOR ? s : `\x1b[32m${s}\x1b[0m`),
    yellow: (s: string) => (NO_COLOR ? s : `\x1b[33m${s}\x1b[0m`),
    red: (s: string) => (NO_COLOR ? s : `\x1b[31m${s}\x1b[0m`),
    dim: (s: string) => (NO_COLOR ? s : `\x1b[2m${s}\x1b[0m`),
    bold: (s: string) => (NO_COLOR ? s : `\x1b[1m${s}\x1b[0m`),
};

const ICON: Record<Status, string> = {
    pass: c.green("✓"),
    warn: c.yellow("⚠"),
    fail: c.red("✗"),
    skip: c.dim("⊘"),
};

// ============================================================
// Helpers
// ============================================================

function run(
    cmd: string,
    args: string[],
    timeoutMs = 5000,
): { code: number; stdout: string; stderr: string } {
    const r = spawnSync(cmd, args, {
        encoding: "utf8",
        timeout: timeoutMs,
    });
    return {
        code: r.status ?? -1,
        stdout: (r.stdout ?? "").trim(),
        stderr: (r.stderr ?? "").trim(),
    };
}

// Parse a .env file into a plain key→value map. Lightweight: handles
// KEY=value and KEY="value" / KEY='value', skips comments and blanks.
// Doesn't try to resolve variable expansion — operators don't put
// `${VAR}` syntax in .yukticastle/.env in practice.
function parseEnvFile(path: string): Record<string, string> {
    const out: Record<string, string> = {};
    if (!existsSync(path)) return out;
    const content = readFileSync(path, "utf8");
    for (const rawLine of content.split(/\r?\n/)) {
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
        out[key] = value;
    }
    return out;
}

// ============================================================
// Checks
// ============================================================

function checkDockerOnPath(): CheckResult {
    const r = run("docker", ["--version"], 5000);
    if (r.code !== 0) {
        return {
            name: "Docker on PATH",
            status: "fail",
            detail: "docker command not found",
            remediation: [
                `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`,
                `If Docker isn't installed: https://www.docker.com/products/docker-desktop`,
            ],
        };
    }
    return {
        name: "Docker on PATH",
        status: "pass",
        detail: r.stdout.replace(/^Docker version /, "docker "),
    };
}

function checkDockerDaemon(): CheckResult {
    // 130s tolerates Docker Desktop cold-start after macOS sleep — same
    // budget as main.mts's preflight.
    const r = run(
        "docker",
        ["info", "--format", "{{.ServerVersion}}"],
        130_000,
    );
    if (r.code !== 0 || !r.stdout) {
        return {
            name: "Docker daemon",
            status: "fail",
            detail:
                r.stderr.split("\n")[0]?.slice(0, 80) ||
                "daemon not responding",
            remediation: [
                `osascript -e 'quit app "Docker"' && sleep 5 && open -a Docker`,
                `Wait ~30s, then re-run \`npm run agents:doctor\``,
            ],
        };
    }
    return {
        name: "Docker daemon",
        status: "pass",
        detail: `Server v${r.stdout}`,
    };
}

function checkImagePresent(): CheckResult {
    const r = run(
        "docker",
        [
            "image",
            "inspect",
            "yukticastle:local",
            "--format",
            "{{.Id}}",
        ],
        5000,
    );
    if (r.code !== 0) {
        return {
            name: "Image yukticastle:local",
            status: "fail",
            detail: "not built",
            remediation: [
                `cd ${process.cwd()}`,
                `docker build -t yukticastle:local -f .yukticastle/Dockerfile .yukticastle`,
                `(first build ~3 min; cached after)`,
            ],
        };
    }
    return {
        name: "Image yukticastle:local",
        status: "pass",
        detail: r.stdout.replace(/^sha256:/, "").slice(0, 12),
    };
}

interface EnvCheckPayload {
    result: CheckResult;
    envFromFile: Record<string, string>;
}

function checkEnvFile(): EnvCheckPayload {
    const envPath = resolvePath(process.cwd(), ".yukticastle/.env");
    const legacyPath = resolvePath(process.cwd(), ".sandcastle/.env");
    if (!existsSync(envPath)) {
        // Legacy-layout detection: a project that hasn't migrated yet
        // will have `.sandcastle/.env` but no `.yukticastle/.env`. Tell
        // the operator how to migrate instead of pretending nothing
        // exists.
        if (existsSync(legacyPath)) {
            return {
                envFromFile: parseEnvFile(legacyPath),
                result: {
                    name: ".yukticastle/.env",
                    status: "fail",
                    detail:
                        "missing — but legacy `.sandcastle/.env` is present",
                    remediation: [
                        `bash <template-clone>/scripts/migrate-to-yukticastle.sh ${process.cwd()}`,
                        `(One-shot migration; preserves prompt customizations.)`,
                    ],
                },
            };
        }
        return {
            envFromFile: {},
            result: {
                name: ".yukticastle/.env",
                status: "fail",
                detail: "missing",
                remediation: [
                    `cp .yukticastle/.env.example .yukticastle/.env`,
                    `Edit and set ANTHROPIC_API_KEY (or sign in via \`claude\` for free MAX-billed runs)`,
                ],
            },
        };
    }
    const envFromFile = parseEnvFile(envPath);
    const haveAnthropic = !!envFromFile.ANTHROPIC_API_KEY;
    const haveOpenAI = !!envFromFile.OPENAI_API_KEY;
    if (!haveAnthropic && !haveOpenAI) {
        // Before warning, peek at the macOS Keychain failover entry
        // (introduced in PR #27). When the host has a valid Keychain-
        // stored key, the run path is fine and a blank .env is the
        // *expected* state — don't warn for what's actually the
        // recommended setup. See FEEDBACK-accessquint-first-agent-run.md
        // Issue #3.
        //
        // Service names match what main.mts's tryKeychainApiKey reads
        // and what .yukticastle/api-key.sh manages. We probe Anthropic
        // (definitely used) and OpenAI (used when REVIEWER=codex). The
        // pre-v2 PR #27 only ships the anthropic entry, but we probe
        // openai too so this code keeps working once the multi-provider
        // refactor lands (no future changes needed here).
        const keychainAnthropic = tryKeychainApiKey(
            "yukticastle-anthropic-api-key",
            "sk-ant-",
        );
        const keychainOpenAI = tryKeychainApiKey(
            "yukticastle-openai-api-key",
            "sk-",
        );
        const keychainHits: string[] = [];
        if (keychainAnthropic) {
            keychainHits.push(
                `ANTHROPIC_API_KEY (${keychainAnthropic.slice(0, 8)}…${keychainAnthropic.slice(-4)})`,
            );
        }
        if (keychainOpenAI) {
            keychainHits.push(
                `OPENAI_API_KEY (${keychainOpenAI.slice(0, 8)}…${keychainOpenAI.slice(-4)})`,
            );
        }
        if (keychainHits.length > 0) {
            return {
                envFromFile,
                result: {
                    name: ".yukticastle/.env",
                    status: "pass",
                    detail: `blank — using Keychain-stored ${keychainHits.join(" + ")}`,
                },
            };
        }
        // Neither file nor Keychain has a key. This is the legitimate
        // warn case — the operator either needs OAuth or to set a key
        // somewhere.
        return {
            envFromFile,
            result: {
                name: ".yukticastle/.env",
                status: "warn",
                detail:
                    "no ANTHROPIC_API_KEY or OPENAI_API_KEY set in file or Keychain",
                remediation: [
                    `If using Keychain OAuth (free), this is fine — main.mts will use the OAuth path.`,
                    `Otherwise pick one:`,
                    `  - npm run yukticastle:api-key:set   (host-wide Keychain, recommended)`,
                    `  - or add ANTHROPIC_API_KEY=sk-ant-... to .yukticastle/.env`,
                ],
            },
        };
    }
    const flags: string[] = [];
    if (haveAnthropic) flags.push("ANTHROPIC_API_KEY");
    if (haveOpenAI) flags.push("OPENAI_API_KEY");
    return {
        envFromFile,
        result: {
            name: ".yukticastle/.env",
            status: "pass",
            detail: flags.join(", "),
        },
    };
}

function checkShellAnthropicHygiene(
    envFromFile: Record<string, string>,
): CheckResult {
    const shellValue = process.env.ANTHROPIC_API_KEY;
    // Three failure shapes, one warn shape, one pass shape:
    if (shellValue === undefined) {
        return {
            name: "Shell ANTHROPIC_API_KEY hygiene",
            status: "pass",
            detail: "unset (will use .env or OAuth)",
        };
    }
    if (shellValue === "") {
        // The infamous "set-but-empty" bug from CHEATSHEET.md.
        // Node's --env-file does not override empty-string values, so
        // the agent inside the container sees ANTHROPIC_API_KEY="" and
        // Claude CLI rejects it as "Not logged in."
        return {
            name: "Shell ANTHROPIC_API_KEY hygiene",
            status: "fail",
            detail: 'set but empty (the "Not logged in" bug)',
            remediation: [
                `unset ANTHROPIC_API_KEY CLAUDE_AUTH_TOKEN`,
                `Then re-run agents:doctor`,
            ],
        };
    }
    const fileValue = envFromFile.ANTHROPIC_API_KEY;
    if (fileValue && shellValue === fileValue) {
        return {
            name: "Shell ANTHROPIC_API_KEY hygiene",
            status: "pass",
            detail: "matches .yukticastle/.env",
        };
    }
    if (fileValue && shellValue !== fileValue) {
        return {
            name: "Shell ANTHROPIC_API_KEY hygiene",
            status: "warn",
            detail:
                "shell value differs from .env (shell will win — confirm intentional)",
            remediation: [
                `If unintentional: \`unset ANTHROPIC_API_KEY\` then re-run`,
            ],
        };
    }
    // Shell set, file doesn't have one — fine, shell value is what main.mts will use
    return {
        name: "Shell ANTHROPIC_API_KEY hygiene",
        status: "pass",
        detail: "set in shell (no .env override)",
    };
}

function checkOAuthExpiry(): CheckResult {
    if (platform() !== "darwin") {
        return {
            name: "OAuth token expiry (Keychain)",
            status: "skip",
            detail: "non-macOS host",
        };
    }
    // Mirror tryKeychainOAuth from main.mts. Don't extract — that's
    // ENHANCEMENTS.md #12 territory, deferred until 3rd provider lands.
    const r = run(
        "security",
        [
            "find-generic-password",
            "-s",
            "Claude Code-credentials",
            "-w",
        ],
        3000,
    );
    if (r.code !== 0 || !r.stdout) {
        return {
            name: "OAuth token expiry (Keychain)",
            status: "skip",
            detail: "no Keychain entry (using API-key path)",
        };
    }
    let expiresAt: number | undefined;
    try {
        const json = JSON.parse(r.stdout);
        expiresAt = json?.claudeAiOauth?.expiresAt;
    } catch {
        return {
            name: "OAuth token expiry (Keychain)",
            status: "warn",
            detail: "Keychain entry malformed — falling back to API key",
        };
    }
    if (typeof expiresAt !== "number") {
        return {
            name: "OAuth token expiry (Keychain)",
            status: "warn",
            detail: "no expiresAt in Keychain entry",
        };
    }
    const remainingMs = expiresAt - Date.now();
    const remainingMin = Math.round(remainingMs / 60_000);
    // Match main.mts's default buffer + its legacy-env fallback.
    const bufferMs =
        Number(
            process.env.YUKTICASTLE_OAUTH_MIN_REMAINING_MS ||
                process.env.SANDCASTLE_OAUTH_MIN_REMAINING_MS ||
                "",
        ) || 60 * 60_000;
    const bufferMin = Math.round(bufferMs / 60_000);
    if (remainingMs <= 0) {
        return {
            name: "OAuth token expiry (Keychain)",
            status: "fail",
            detail: `expired ${-remainingMin} min ago`,
            remediation: [
                `npm run agents:doctor -- --auto-refresh   # refresh + re-check in one command`,
                `Or manually: \`npm run claude:login\`, then re-run agents:doctor`,
            ],
        };
    }
    if (remainingMs < bufferMs) {
        return {
            name: "OAuth token expiry (Keychain)",
            status: "fail",
            detail: `${remainingMin} min remaining (buffer ${bufferMin} min)`,
            remediation: [
                `npm run agents:doctor -- --auto-refresh   # refresh + re-check in one command`,
                `Or manually: \`npm run claude:login\`, then re-run agents:doctor`,
                `Or override the buffer: YUKTICASTLE_OAUTH_MIN_REMAINING_MS=900000  # 15 min`,
            ],
        };
    }
    return {
        name: "OAuth token expiry (Keychain)",
        status: "pass",
        detail: `${remainingMin} min remaining`,
    };
}

function checkDiskSpace(): CheckResult {
    // Use df -Pk for POSIX-portable 1K-block output. Column 4 = available.
    const r = run("df", ["-Pk", process.cwd()], 5000);
    if (r.code !== 0) {
        return {
            name: "Disk space",
            status: "warn",
            detail: "df command failed — skipping",
        };
    }
    const lines = r.stdout.split("\n");
    if (lines.length < 2) {
        return {
            name: "Disk space",
            status: "warn",
            detail: "unexpected df output",
        };
    }
    const cols = lines[1].split(/\s+/);
    const availKb = Number(cols[3]);
    if (!Number.isFinite(availKb)) {
        return {
            name: "Disk space",
            status: "warn",
            detail: "could not parse df output",
        };
    }
    const availGb = availKb / (1024 * 1024);
    const MIN_GB = 2;
    if (availGb < MIN_GB) {
        return {
            name: "Disk space",
            status: "fail",
            detail: `${availGb.toFixed(1)}GB free (min ${MIN_GB}GB)`,
            remediation: [
                `npm run agents:clean -- --force                  # prune stale agent branches/worktrees`,
                `docker system prune -af --volumes                # reclaim Docker layers (large)`,
            ],
        };
    }
    return {
        name: "Disk space",
        status: "pass",
        detail: `${availGb.toFixed(1)}GB free`,
    };
}

function checkPackageScripts(): CheckResult {
    const pkgPath = resolvePath(process.cwd(), "package.json");
    if (!existsSync(pkgPath)) {
        return {
            name: "package.json hooks",
            status: "fail",
            detail: "package.json not found",
            remediation: [
                `Run init-yukticastle.sh against this directory first`,
            ],
        };
    }
    let pkg: { scripts?: Record<string, string> };
    try {
        pkg = JSON.parse(readFileSync(pkgPath, "utf8"));
    } catch {
        return {
            name: "package.json hooks",
            status: "fail",
            detail: "package.json is not valid JSON",
            remediation: [`Fix package.json before re-running`],
        };
    }
    const have = new Set(Object.keys(pkg.scripts ?? {}));
    const required = ["agents:run", "agents:clean", "agents:doctor"];
    const missing = required.filter((s) => !have.has(s));
    if (missing.length > 0) {
        return {
            name: "package.json hooks",
            status: "fail",
            detail: `missing: ${missing.join(", ")}`,
            remediation: [
                `bash ~/Documents/AI\\ Projects/sandcastle-template/scripts/init-yukticastle.sh ${process.cwd()}`,
                `(idempotent — preserves your prompt customizations)`,
            ],
        };
    }
    return {
        name: "package.json hooks",
        status: "pass",
        detail: required.join(" / "),
    };
}

// ============================================================
// Runner
// ============================================================

function run_all(): CheckResult[] {
    const results: CheckResult[] = [];
    results.push(checkDockerOnPath());
    // If docker isn't on PATH, daemon + image checks would just be
    // duplicate failures with the same fix. Skip them.
    if (results[0].status === "pass") {
        results.push(checkDockerDaemon());
        if (results[1].status === "pass") {
            results.push(checkImagePresent());
        } else {
            results.push({
                name: "Image yukticastle:local",
                status: "skip",
                detail: "skipped — daemon not responding",
            });
        }
    } else {
        results.push({
            name: "Docker daemon",
            status: "skip",
            detail: "skipped — docker not on PATH",
        });
        results.push({
            name: "Image yukticastle:local",
            status: "skip",
            detail: "skipped — docker not on PATH",
        });
    }
    const envCheck = checkEnvFile();
    results.push(envCheck.result);
    results.push(checkShellAnthropicHygiene(envCheck.envFromFile));

    // OAuth check + optional auto-refresh.
    let oauthResult = checkOAuthExpiry();
    if (
        AUTO_REFRESH &&
        oauthResult.status === "fail" &&
        platform() === "darwin"
    ) {
        const refreshed = tryAutoRefreshKeychain();
        if (refreshed) {
            // Re-run the check; reflect the new state in the report.
            const after = checkOAuthExpiry();
            // Note in the detail that auto-refresh was applied.
            after.detail = `${after.detail ?? ""} (auto-refresh applied)`.trim();
            oauthResult = after;
        } else {
            // Refresh attempt failed; keep the original fail result but
            // append a note so the operator knows we tried.
            oauthResult.detail =
                `${oauthResult.detail ?? ""} (auto-refresh attempted, failed — see warnings above)`.trim();
        }
    }
    results.push(oauthResult);

    results.push(checkDiskSpace());
    results.push(checkPackageScripts());
    return results;
}

// Synchronously invoke `bash .yukticastle/claude-login.sh` to trigger a
// keychain OAuth refresh. Returns true on success (exit 0), false
// otherwise. Stdout/stderr stream to the operator so they see the
// refresh script's own output.
function tryAutoRefreshKeychain(): boolean {
    const scriptPath = resolvePath(
        process.cwd(),
        ".yukticastle/claude-login.sh",
    );
    if (!existsSync(scriptPath)) {
        if (!QUIET && !JSON_MODE) {
            console.warn(
                c.yellow(
                    `⚠ --auto-refresh requested but ${scriptPath.replace(process.cwd() + "/", "")} not found — skipping`,
                ),
            );
        }
        return false;
    }
    if (!QUIET && !JSON_MODE) {
        console.log(c.dim(`→ --auto-refresh: invoking claude-login.sh ...`));
    }
    const r = spawnSync("bash", [scriptPath], {
        stdio: JSON_MODE ? "ignore" : "inherit",
        timeout: 30_000,
    });
    if (!QUIET && !JSON_MODE) {
        console.log(""); // visual separator after the script's output
    }
    return r.status === 0;
}

// ============================================================
// Output
// ============================================================

function formatHuman(results: CheckResult[]): {
    text: string;
    failCount: number;
} {
    const lines: string[] = [];
    let failCount = 0;
    let fixIdx = 0;
    const NAME_WIDTH = 36;

    for (const r of results) {
        const isVisible =
            !QUIET || r.status === "fail" || r.status === "warn";
        if (r.status === "fail") failCount += 1;
        if (!isVisible) continue;

        const namePad = r.name.padEnd(NAME_WIDTH, " ");
        const detail = r.detail ? c.dim(r.detail) : "";
        lines.push(`${ICON[r.status]} ${namePad} ${detail}`);

        if (
            (r.status === "fail" || r.status === "warn") &&
            r.remediation &&
            r.remediation.length > 0
        ) {
            for (const fix of r.remediation) {
                fixIdx += 1;
                const tag = c.bold(`[fix ${fixIdx}]`);
                lines.push(`    ${tag} ${fix}`);
            }
        }
    }

    if (lines.length === 0) {
        lines.push(c.green("All checks passed."));
    } else {
        // Summary line
        const passCount = results.filter((r) => r.status === "pass")
            .length;
        const warnCount = results.filter((r) => r.status === "warn")
            .length;
        const skipCount = results.filter((r) => r.status === "skip")
            .length;
        const total = results.length;
        const summaryParts = [
            c.green(`${passCount} pass`),
            warnCount > 0 ? c.yellow(`${warnCount} warn`) : null,
            failCount > 0 ? c.red(`${failCount} fail`) : null,
            skipCount > 0 ? c.dim(`${skipCount} skip`) : null,
        ].filter(Boolean);
        lines.push(``);
        lines.push(
            `${c.bold("Summary:")} ${summaryParts.join(", ")} (of ${total})`,
        );
    }

    return { text: lines.join("\n"), failCount };
}

function formatJson(results: CheckResult[]): {
    text: string;
    failCount: number;
} {
    const failCount = results.filter((r) => r.status === "fail").length;
    return {
        text: JSON.stringify(
            {
                ok: failCount === 0,
                cwd: process.cwd(),
                node: process.version,
                platform: platform(),
                results,
            },
            null,
            2,
        ),
        failCount,
    };
}

// ============================================================
// Main
// ============================================================

const start = Date.now();
const results = run_all();
const elapsedMs = Date.now() - start;

const formatted = JSON_MODE
    ? formatJson(results)
    : formatHuman(results);

console.log(formatted.text);

if (!JSON_MODE && !QUIET) {
    console.log(c.dim(`(${elapsedMs}ms)`));
}

process.exit(formatted.failCount > 0 ? 1 : 0);
