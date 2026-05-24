// YuktiCastle task-spec linter — `npm run agents:lint -- tasks/foo.md`.
//
// Catches the failures that would otherwise surface 3+ minutes into
// a Docker run:
//   - PromptError from `{{X}}` placeholders in prose
//   - Missing acceptance criteria (reviewer has nothing to check)
//   - Missing H1 (branch slug derives from it)
//   - Forbidden phrases (db:push, etc.) when a manual-migration
//     policy is in effect
//   - `git push` instructions (host owns the branch, not the agent)
//
// Designed to be cheap (~50ms) and runnable manually before launch.
// A follow-up commit will wire auto-run into main.mts behind a
// --no-lint flag once the UX is proven; doing it now would add a
// pre-flight blocker to every project that pulls a new template
// version, which is more disruption than is warranted before the
// linter itself has been used in anger.
//
// Usage:
//   npm run agents:lint -- tasks/your-task.md
//   npm run agents:lint -- tasks/your-task.md --quiet
//   npm run agents:lint -- tasks/your-task.md --json
//
// Exit 0 if no errors (warns/infos OK); exit 1 if any error.
//
// No new deps — pure Node built-ins.

import { existsSync, readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// ============================================================
// Types
// ============================================================

type Severity = "error" | "warn" | "info";

interface Issue {
    rule: string;          // short rule id
    severity: Severity;
    message: string;       // operator-facing one-liner
    line?: number;         // 1-indexed if attributable
    hint?: string;         // optional remediation
}

interface TaskFile {
    path: string;
    raw: string;
    lines: string[];           // for line-number reporting
    bodyLength: number;        // raw char count
    fenceMask: boolean[];      // per-line: true if inside ```...```
}

// ============================================================
// CLI flags
// ============================================================

const argv = process.argv.slice(2);
const QUIET = argv.includes("--quiet");
const JSON_MODE = argv.includes("--json");
const NO_COLOR =
    argv.includes("--no-color") ||
    !process.stdout.isTTY ||
    process.env.NO_COLOR !== undefined;

// First positional argument that doesn't look like a flag is the task path.
const positional = argv.find((a) => !a.startsWith("--"));
if (!positional) {
    console.error(
        "Usage: npm run agents:lint -- tasks/your-task.md [--quiet] [--json]",
    );
    process.exit(2);
}
const TASK_PATH = positional;

// ============================================================
// Tiny ANSI helpers (mirror doctor.mts; no chalk dep)
// ============================================================

const c = {
    green: (s: string) => (NO_COLOR ? s : `\x1b[32m${s}\x1b[0m`),
    yellow: (s: string) => (NO_COLOR ? s : `\x1b[33m${s}\x1b[0m`),
    red: (s: string) => (NO_COLOR ? s : `\x1b[31m${s}\x1b[0m`),
    blue: (s: string) => (NO_COLOR ? s : `\x1b[34m${s}\x1b[0m`),
    dim: (s: string) => (NO_COLOR ? s : `\x1b[2m${s}\x1b[0m`),
    bold: (s: string) => (NO_COLOR ? s : `\x1b[1m${s}\x1b[0m`),
};

const ICON: Record<Severity, string> = {
    error: c.red("✗"),
    warn: c.yellow("⚠"),
    info: c.blue("ℹ"),
};

// ============================================================
// Load task file + compute fence mask (which lines are inside ```)
// ============================================================

function loadTask(path: string): TaskFile | null {
    const abs = resolvePath(path);
    if (!existsSync(abs)) {
        console.error(c.red(`Task file not found: ${path} (${abs})`));
        return null;
    }
    const raw = readFileSync(abs, "utf8");
    const lines = raw.split(/\r?\n/);
    // Track fenced code blocks. Anything between ``` markers is
    // exempt from substitution-placeholder errors and forbidden-phrase
    // checks, since example code naturally contains things like
    // `db:push` or `{{TOKEN}}` for discussion purposes.
    const fenceMask: boolean[] = [];
    let inside = false;
    for (const line of lines) {
        if (/^\s*```/.test(line)) {
            // The fence line itself is treated as inside (operator-
            // facing — the `{{X}}` on the same line as the opening
            // fence is in the block).
            fenceMask.push(true);
            inside = !inside;
        } else {
            fenceMask.push(inside);
        }
    }
    return { path, raw, lines, bodyLength: raw.length, fenceMask };
}

// ============================================================
// Helpers shared across rules
// ============================================================

function findFirstH1Line(t: TaskFile): { text: string; line: number } | null {
    for (let i = 0; i < t.lines.length; i++) {
        if (t.fenceMask[i]) continue; // ignore code-block lines
        const m = /^#\s+(.+?)\s*$/.exec(t.lines[i]);
        if (m) return { text: m[1], line: i + 1 };
    }
    return null;
}

// Return [start, end) 1-indexed line range for the H2 with the given
// title (case-insensitive). Returns null if the section isn't found.
function findSection(
    t: TaskFile,
    titlePattern: RegExp,
): { start: number; end: number } | null {
    let start: number | null = null;
    for (let i = 0; i < t.lines.length; i++) {
        if (t.fenceMask[i]) continue;
        const m = /^##\s+(.+?)\s*$/.exec(t.lines[i]);
        if (!m) continue;
        if (start === null && titlePattern.test(m[1])) {
            start = i + 1;
        } else if (start !== null) {
            // Next H2 marks the end.
            return { start, end: i + 1 };
        }
    }
    if (start !== null) return { start, end: t.lines.length + 1 };
    return null;
}

function sectionHasContent(t: TaskFile, start: number, end: number): boolean {
    // Skip the heading line itself; any non-blank, non-blockquote line
    // counts as content. (Blockquotes alone — ">>" prose-only — count
    // as commentary, not actionable acceptance criteria.)
    for (let i = start; i < end - 1; i++) {
        if (i >= t.lines.length) break;
        const line = t.lines[i].trim();
        if (!line) continue;
        if (line.startsWith(">")) continue;
        return true;
    }
    return false;
}

// ============================================================
// Rules
// ============================================================

function ruleH1Present(t: TaskFile): Issue[] {
    const h1 = findFirstH1Line(t);
    if (!h1) {
        return [
            {
                rule: "h1-present",
                severity: "error",
                message:
                    "No H1 heading found. The branch slug derives from the first H1.",
                hint: "Add a single concise H1 at the top: `# Add hospitality domain config`",
            },
        ];
    }
    return [];
}

function ruleH1Length(t: TaskFile): Issue[] {
    const h1 = findFirstH1Line(t);
    if (!h1) return []; // h1-present rule will error
    if (h1.text.length > 60) {
        return [
            {
                rule: "h1-length",
                severity: "warn",
                line: h1.line,
                message: `H1 is ${h1.text.length} chars; branch slugs truncate at 40.`,
                hint: "Tighten the H1 — verb-led + noun is the proven shape.",
            },
        ];
    }
    return [];
}

function ruleSubstitutionPlaceholders(t: TaskFile): Issue[] {
    // The task body becomes the value of `{{TASK_DESCRIPTION}}` in the
    // implementer/reviewer prompts. YuktiCastle's prompt-substitution
    // (via @ai-hero/sandcastle) requires every `{{X}}` it sees to have
    // a matching value in promptArgs. So any `{{X}}` in the task body
    // (outside code fences) becomes a PromptError at startup.
    //
    // Use [[X]] for discussion examples instead — those are harmless.
    // The only legal `{{...}}` substitutions in the prompt files are
    // {{TASK_DESCRIPTION}} and {{BRANCH}}, both filled by main.mts.
    const issues: Issue[] = [];
    const pattern = /\{\{([A-Z_][A-Z0-9_]*)\}\}/g;
    for (let i = 0; i < t.lines.length; i++) {
        if (t.fenceMask[i]) continue;
        const line = t.lines[i];
        let m: RegExpExecArray | null;
        pattern.lastIndex = 0;
        while ((m = pattern.exec(line)) !== null) {
            issues.push({
                rule: "substitution-placeholder",
                severity: "error",
                line: i + 1,
                message: `Found \`{{${m[1]}}}\` in prose — sandcastle will fail at startup with PromptError.`,
                hint: `Use \`[[${m[1]}]]\` if it's a discussion example. Only \`{{TASK_DESCRIPTION}}\` and \`{{BRANCH}}\` are real subs (and they belong in the prompt files, not here).`,
            });
        }
    }
    return issues;
}

function ruleAcceptanceCriteria(t: TaskFile): Issue[] {
    const sec = findSection(t, /^acceptance\s+criteria$/i);
    if (!sec) {
        return [
            {
                rule: "acceptance-criteria-section",
                severity: "error",
                message: "Missing `## Acceptance criteria` section.",
                hint: "The reviewer needs concrete, testable criteria (typecheck command, smoke output, file list, etc.). Without it, the reviewer has nothing to verify.",
            },
        ];
    }
    if (!sectionHasContent(t, sec.start, sec.end)) {
        return [
            {
                rule: "acceptance-criteria-empty",
                severity: "error",
                line: sec.start,
                message: "`## Acceptance criteria` section is empty (only blockquote commentary).",
                hint: "Add at least one bullet listing what must be true after the agent finishes (e.g. `npx tsc --noEmit` passes; specific file list; smoke test exits 0).",
            },
        ];
    }
    return [];
}

function ruleFilesToRead(t: TaskFile): Issue[] {
    const sec = findSection(t, /^files\s+to\s+(read|reference)$/i);
    if (!sec) {
        return [
            {
                rule: "files-to-read-section",
                severity: "warn",
                message: "Missing `## Files to read` section.",
                hint: "Listing the canonical reference, types, registry, and any relevant ADR cuts the agent's discovery iterations roughly in half. Skippable for trivial one-file tweaks.",
            },
        ];
    }
    return [];
}

function ruleForbiddenPhrases(t: TaskFile, projectPolicy: string[]): Issue[] {
    // Hardcoded baseline + project-specific extension via
    // `.yukticastle/policy.json` (schema TBD — for now, the file is
    // detected and listed but not parsed; full policy support is a
    // follow-up alongside ENHANCEMENTS.md #6 runtime enforcement).
    const baseline = [
        "npm run db:push",
        "npm run db:migrate",
        "drizzle-kit push",
        "drizzle-kit migrate",
        "prisma migrate deploy",
    ];
    const phrases = [...baseline, ...projectPolicy];
    const issues: Issue[] = [];
    for (let i = 0; i < t.lines.length; i++) {
        if (t.fenceMask[i]) continue; // examples in code blocks are fine
        for (const phrase of phrases) {
            if (t.lines[i].toLowerCase().includes(phrase.toLowerCase())) {
                issues.push({
                    rule: "forbidden-phrase",
                    severity: "error",
                    line: i + 1,
                    message: `Task instructs forbidden command: \`${phrase}\`.`,
                    hint: "DB migrations should be written as `migrations/manual_*.sql` and applied by the operator post-merge — never run by the agent. See `docs/runbooks/existing-project-integration.md`.",
                });
            }
        }
    }
    return issues;
}

function ruleNoGitPush(t: TaskFile): Issue[] {
    const issues: Issue[] = [];
    for (let i = 0; i < t.lines.length; i++) {
        if (t.fenceMask[i]) continue;
        // Match `git push` as a command instruction. Skip lines that
        // are explicit "do NOT push" directives (those are good).
        const line = t.lines[i];
        if (!/git\s+push/i.test(line)) continue;
        if (/(don'?t|do\s+not|never|no)\s+(`?git\s+push`?)/i.test(line))
            continue;
        if (/^\s*[->]/.test(line) && /no\s+`?git\s+push`?/i.test(line))
            continue;
        issues.push({
            rule: "git-push-instruction",
            severity: "error",
            line: i + 1,
            message: "Task includes a `git push` instruction.",
            hint: "The host operator owns the branch. The agent commits locally; the host pushes. Remove the instruction or rephrase as a constraint (e.g. `Don't git push`).",
        });
    }
    return issues;
}

function ruleBodyLength(t: TaskFile): Issue[] {
    const len = t.bodyLength;
    if (len < 200) {
        return [
            {
                rule: "body-length-short",
                severity: "info",
                message: `Task body is ${len} chars — likely underspecified.`,
                hint: "Specs under ~200 chars usually trigger discovery iterations. Add file paths, schema, naming, and reference impls.",
            },
        ];
    }
    if (len > 5000) {
        return [
            {
                rule: "body-length-long",
                severity: "info",
                message: `Task body is ${len} chars — likely scope creep.`,
                hint: "Specs over ~5000 chars usually fit better as 2–3 dependent tasks. Smaller tasks are cheaper to iterate on.",
            },
        ];
    }
    return [];
}

function ruleCompletionPromise(t: TaskFile): Issue[] {
    if (!/<promise>\s*COMPLETE\s*<\/promise>/i.test(t.raw)) {
        return [
            {
                rule: "completion-promise",
                severity: "warn",
                message:
                    "No `<promise>COMPLETE</promise>` reminder found.",
                hint: "Adding `Emit `<promise>COMPLETE</promise>` when done.` to a `## When done` section gives the orchestrator a clean stop signal. Without it, the agent often runs to maxIterations.",
            },
        ];
    }
    return [];
}

// ============================================================
// Project-policy loader (minimal v1 — full schema is a follow-up)
// ============================================================

function loadProjectPolicy(): string[] {
    // Try the new path first; fall back to the legacy `.sandcastle/`
    // layout for projects that haven't migrated yet (Phase B rebrand).
    const candidates = [
        resolvePath(process.cwd(), ".yukticastle/policy.json"),
        resolvePath(process.cwd(), ".sandcastle/policy.json"),
    ];
    for (const policyPath of candidates) {
        if (!existsSync(policyPath)) continue;
        try {
            const json = JSON.parse(readFileSync(policyPath, "utf8"));
            // v1 schema: { "forbiddenPhrases": ["string", ...] }
            if (Array.isArray(json?.forbiddenPhrases)) {
                return json.forbiddenPhrases.filter(
                    (s: unknown): s is string => typeof s === "string",
                );
            }
        } catch {
            // malformed — silently ignore in v1; full validation is a follow-up
        }
        return [];
    }
    return [];
}

// ============================================================
// Run all rules
// ============================================================

function runAllRules(t: TaskFile): Issue[] {
    const projectPolicy = loadProjectPolicy();
    return [
        ...ruleH1Present(t),
        ...ruleH1Length(t),
        ...ruleSubstitutionPlaceholders(t),
        ...ruleAcceptanceCriteria(t),
        ...ruleFilesToRead(t),
        ...ruleForbiddenPhrases(t, projectPolicy),
        ...ruleNoGitPush(t),
        ...ruleBodyLength(t),
        ...ruleCompletionPromise(t),
    ];
}

// ============================================================
// Output
// ============================================================

function formatHuman(
    t: TaskFile,
    issues: Issue[],
): { text: string; errorCount: number } {
    const errorCount = issues.filter((i) => i.severity === "error").length;
    const warnCount = issues.filter((i) => i.severity === "warn").length;
    const infoCount = issues.filter((i) => i.severity === "info").length;

    const lines: string[] = [];
    lines.push(`${c.bold("Linting:")} ${t.path}`);

    const visible = QUIET
        ? issues.filter((i) => i.severity !== "info")
        : issues;

    if (issues.length === 0) {
        lines.push(c.green("✓ No issues found."));
    } else if (visible.length === 0) {
        lines.push(c.green("✓ No errors or warnings."));
    } else {
        for (const issue of visible) {
            const loc = issue.line ? c.dim(`L${issue.line}`) : c.dim("—");
            const ruleTag = c.dim(`(${issue.rule})`);
            lines.push(
                `${ICON[issue.severity]} ${loc}  ${issue.message} ${ruleTag}`,
            );
            if (issue.hint) {
                lines.push(`     ${c.dim("hint:")} ${issue.hint}`);
            }
        }
    }

    // Summary
    const summaryParts = [
        errorCount > 0 ? c.red(`${errorCount} error`) : null,
        warnCount > 0 ? c.yellow(`${warnCount} warn`) : null,
        infoCount > 0 ? c.blue(`${infoCount} info`) : null,
    ].filter(Boolean);
    lines.push(``);
    if (summaryParts.length === 0) {
        lines.push(`${c.bold("Summary:")} ${c.green("clean")}`);
    } else {
        lines.push(`${c.bold("Summary:")} ${summaryParts.join(", ")}`);
    }

    return { text: lines.join("\n"), errorCount };
}

function formatJson(
    t: TaskFile,
    issues: Issue[],
): { text: string; errorCount: number } {
    const errorCount = issues.filter((i) => i.severity === "error").length;
    return {
        text: JSON.stringify(
            {
                ok: errorCount === 0,
                path: t.path,
                bodyLength: t.bodyLength,
                issues,
                summary: {
                    errors: errorCount,
                    warnings: issues.filter((i) => i.severity === "warn")
                        .length,
                    infos: issues.filter((i) => i.severity === "info").length,
                },
            },
            null,
            2,
        ),
        errorCount,
    };
}

// ============================================================
// Main
// ============================================================

const task = loadTask(TASK_PATH);
if (!task) {
    process.exit(2);
}

const issues = runAllRules(task);
const formatted = JSON_MODE
    ? formatJson(task, issues)
    : formatHuman(task, issues);

console.log(formatted.text);
process.exit(formatted.errorCount > 0 ? 1 : 0);
