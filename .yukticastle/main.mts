// YuktiCastle orchestrator (task-driven sequential reviewer).
//
// Pattern: pass TASK_DESCRIPTION via env, run implementer (Claude Opus)
// in a Docker sandbox on a fresh branch, then run reviewer (Claude
// Sonnet) on the same branch to verify project conventions + typecheck
// pass. Both phases use Claude Code CLI; reviewer can be swapped to
// `yukticastle.codex("gpt-5")` later for a different perspective.
//
// Usage:
//   TASK_DESCRIPTION="Add hospitality domain config..." npm run agents:run
//
// Cluster-1 polish (May 7, post-smoke):
//   - Token usage + estimated cost summary per phase
//   - Commit-by-commit summary (sha + subject) at end
//   - Silent-failure detection (warn when iterations ran but produced
//     neither commits nor a completion signal)
//   - Branch + diff inspection commands printed at the end so the
//     operator can review before merging

import * as yukticastle from "@ai-hero/sandcastle";
import type { AgentStreamEvent } from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { execSync, spawnSync } from "node:child_process";
import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve as resolvePath } from "node:path";
// Type-only import — dag-executor is dynamically import()ed at the
// spec_critic call site (matches the pattern used for ./dag.js and
// ./spec-critic.mjs). Types are erased at compile time.
import type * as DagExecutor from "./dag-executor.js";

// ============================================================
// Task input — three forms, in priority order:
//
//   1. Markdown file (positional or --task-file=…). Best for any task
//      that benefits from structure: a paragraph of context, then a
//      bullet list of constraints, then references. Operator can edit
//      it in their editor with no shell-escaping pain.
//
//        npm run agents:run -- tasks/add-hospitality-domain.md
//        npm run agents:run -- --task-file=tasks/foo.md
//
//   2. TASK_DESCRIPTION env var. Concise one-liners.
//
//        TASK_DESCRIPTION="Add a comment to README.md" npm run agents:run
//
// File wins if both are provided. The first H1 heading in the file
// (if any) is used to derive the branch slug — keeps branch names
// readable when the body is multi-paragraph.
// ============================================================

interface TaskInput {
    description: string;
    sourcePath?: string;       // file path, if loaded from disk
    branchSlugSource: string;  // short string used to derive branch slug
}

function loadTaskInput(): TaskInput | null {
    const args = process.argv.slice(2);
    const fileFlag = args.find((a) => a.startsWith("--task-file="))?.split("=")[1];
    const positional = args.find((a) => !a.startsWith("--"));
    const filePath =
        fileFlag ??
        (positional && (positional.endsWith(".md") || positional.includes("/"))
            ? positional
            : undefined);

    if (filePath) {
        const abs = resolvePath(filePath);
        if (!existsSync(abs)) {
            console.error(`[yukticastle] Task file not found: ${filePath}`);
            console.error(`[yukticastle] (resolved to ${abs})`);
            return null;
        }
        const content = readFileSync(abs, "utf8").trim();
        if (!content) {
            console.error(`[yukticastle] Task file is empty: ${filePath}`);
            return null;
        }
        const firstH1 = /^#\s+(.+)$/m.exec(content)?.[1]?.trim();
        return {
            description: content,
            sourcePath: filePath,
            branchSlugSource: (firstH1 ?? content).slice(0, 80),
        };
    }

    const envTask = process.env.TASK_DESCRIPTION;
    if (envTask && envTask.trim()) {
        return {
            description: envTask.trim(),
            branchSlugSource: envTask.trim().slice(0, 80),
        };
    }

    return null;
}

const taskInput = loadTaskInput();
if (!taskInput) {
    console.error(``);
    console.error(`Provide a task via one of:`);
    console.error(`  npm run agents:run -- tasks/your-task.md           (positional)`);
    console.error(`  npm run agents:run -- --task-file=tasks/foo.md     (explicit flag)`);
    console.error(`  TASK_DESCRIPTION="..." npm run agents:run          (env var, one-liner)`);
    console.error(``);
    process.exit(1);
}

const TASK = taskInput.description;
const slug = taskInput.branchSlugSource
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
// PR #53 — branch name now includes HHMMSS so same-day re-fires of
// the same task spec get unique branches. Pre-PR #53, the branch
// name was `agent/<slug>-<YYYYMMDD>`, which collided on same-day
// re-runs and caused the sandcastle worktree to reuse the prior
// run's stale base — root cause of the gextrader 2026-05-18 near-miss
// (1278-deletion auto-approved PR). See ENHANCEMENTS.md #43.
//
// Operators can revert to date-only suffix via
// YUKTICASTLE_BRANCH_SUFFIX_DATE_ONLY=true (CI determinism / replay).
const stampDate = new Date().toISOString().slice(0, 10).replace(/-/g, "");
const stampTime = new Date().toISOString().slice(11, 19).replace(/:/g, "");
const stamp =
    process.env.YUKTICASTLE_BRANCH_SUFFIX_DATE_ONLY === "true"
        ? stampDate
        : `${stampDate}-${stampTime}`;
const branch = `agent/${slug}-${stamp}`;

// ============================================================
// PR #53 — host base-branch freshness check + protected paths
//
// Before any agent work, ensure the host's current branch (which
// sandcastle will base the agent worktree off of) matches the
// remote tip. Operators who haven't pulled recently — or who
// accidentally re-fire a task spec from shell history hours after
// intervening PRs merged — would otherwise have the agent branch
// off a stale base. The reviewer phase then diffs against THAT
// stale `main`, sees a small acceptable change, signals COMPLETE,
// and the orchestrator opens a PR that wipes out the work merged
// in between.
//
// Real incident: gextrader 2026-05-18. The auto-PR was
// `https://github.com/sandhipveera/gextrader/pull/23` —
// 1278 deletions / 119 insertions. Caught at human code review.
// See ENHANCEMENTS.md #43 for the full incident postmortem.
//
// Knobs:
//   YUKTICASTLE_SKIP_MAIN_UPDATE_CHECK=true  — skip the check
//     entirely (CI, detached HEAD, intentional stale-base run).
//   YUKTICASTLE_DELETION_GUARD_RATIO=N       — net-deletions /
//     insertions ratio that trips the guard. Default 3.
//   YUKTICASTLE_DELETION_GUARD_FLOOR=N       — minimum deletion
//     count needed before the ratio check fires (avoids tripping
//     on tiny PRs that drop 4 lines and add 1). Default 50.
//   YUKTICASTLE_ALLOW_BULK_DELETIONS=true    — bypass the guard
//     entirely (legitimate big-deletion PR).
//   YUKTICASTLE_PROTECTED_PATHS=p1,p2,...    — comma-list of
//     prefixes whose deletion always trips the guard. Defaults
//     to "prisma/migrations/,tasks/,.yukticastle/,pine/" — paths
//     where deletion is almost always a mistake.
// ============================================================

function detectDefaultBranch(): string {
    const r = spawnSync(
        "git",
        ["symbolic-ref", "refs/remotes/origin/HEAD"],
        { encoding: "utf8", timeout: 5000 },
    );
    if (r.status === 0 && r.stdout) {
        // e.g. "refs/remotes/origin/main" → "main"
        const parts = r.stdout.trim().split("/");
        return parts[parts.length - 1] || "main";
    }
    // Fallback: probe local "main" then "master".
    const mainProbe = spawnSync(
        "git",
        ["rev-parse", "--verify", "main"],
        { encoding: "utf8", timeout: 3000 },
    );
    if (mainProbe.status === 0) return "main";
    const masterProbe = spawnSync(
        "git",
        ["rev-parse", "--verify", "master"],
        { encoding: "utf8", timeout: 3000 },
    );
    if (masterProbe.status === 0) return "master";
    return "main";
}

const DEFAULT_BRANCH = detectDefaultBranch();

function ensureHostBaseFresh(): void {
    if (process.env.YUKTICASTLE_SKIP_MAIN_UPDATE_CHECK === "true") {
        console.log(
            `[yukticastle] skipping host-base freshness check (YUKTICASTLE_SKIP_MAIN_UPDATE_CHECK=true)`,
        );
        return;
    }

    const currentBranchProbe = spawnSync(
        "git",
        ["rev-parse", "--abbrev-ref", "HEAD"],
        { encoding: "utf8", timeout: 5000 },
    );
    const currentBranch = currentBranchProbe.stdout.trim();

    if (currentBranch !== DEFAULT_BRANCH) {
        console.warn(
            `[yukticastle] ⚠  Host is on \`${currentBranch}\`, not \`${DEFAULT_BRANCH}\`.`,
        );
        console.warn(
            `[yukticastle]    The agent branch will be based off \`${currentBranch}\`'s current HEAD.`,
        );
        console.warn(
            `[yukticastle]    Make sure that's intentional. (Skipping origin-staleness check since`,
        );
        console.warn(
            `[yukticastle]    we don't know whether \`${currentBranch}\` is supposed to track origin.)`,
        );
        return;
    }

    console.log(
        `[yukticastle] checking \`${DEFAULT_BRANCH}\` is fresh vs origin/${DEFAULT_BRANCH}…`,
    );

    const fetchResult = spawnSync(
        "git",
        ["fetch", "origin", DEFAULT_BRANCH, "--quiet"],
        { encoding: "utf8", timeout: 60_000 },
    );
    if (fetchResult.status !== 0) {
        console.error(``);
        console.error(
            `[yukticastle] ⛔ git fetch origin ${DEFAULT_BRANCH} failed:`,
        );
        console.error(
            fetchResult.stderr || fetchResult.stdout || "(no output)",
        );
        console.error(``);
        console.error(`[yukticastle]    Fix the network/auth issue and re-run.`);
        console.error(
            `[yukticastle]    To bypass this check (CI, offline runs):  YUKTICASTLE_SKIP_MAIN_UPDATE_CHECK=true`,
        );
        process.exit(1);
    }

    const localSha = spawnSync(
        "git",
        ["rev-parse", DEFAULT_BRANCH],
        { encoding: "utf8", timeout: 5000 },
    ).stdout.trim();
    const remoteSha = spawnSync(
        "git",
        ["rev-parse", `origin/${DEFAULT_BRANCH}`],
        { encoding: "utf8", timeout: 5000 },
    ).stdout.trim();

    if (localSha === remoteSha) {
        console.log(
            `[yukticastle]    ✓ ${DEFAULT_BRANCH} == origin/${DEFAULT_BRANCH} (${localSha.slice(0, 8)})`,
        );
        return;
    }

    const behindN = Number(
        spawnSync(
            "git",
            [
                "rev-list",
                "--count",
                `${DEFAULT_BRANCH}..origin/${DEFAULT_BRANCH}`,
            ],
            { encoding: "utf8", timeout: 5000 },
        ).stdout.trim(),
    ) || 0;
    const aheadN = Number(
        spawnSync(
            "git",
            [
                "rev-list",
                "--count",
                `origin/${DEFAULT_BRANCH}..${DEFAULT_BRANCH}`,
            ],
            { encoding: "utf8", timeout: 5000 },
        ).stdout.trim(),
    ) || 0;

    if (aheadN > 0) {
        // Don't auto-rebase or auto-push the operator's local work.
        // Hard-fail with a clear message.
        console.error(``);
        console.error(
            `[yukticastle] ⛔ Local \`${DEFAULT_BRANCH}\` is ${aheadN} commit(s) ahead of \`origin/${DEFAULT_BRANCH}\`.`,
        );
        if (behindN > 0) {
            console.error(
                `[yukticastle]    (Also ${behindN} behind — branches have diverged.)`,
            );
        }
        console.error(``);
        console.error(
            `[yukticastle]    YuktiCastle won't auto-rebase or auto-push your local work — too risky.`,
        );
        console.error(`[yukticastle]    Resolve by hand:`);
        console.error(``);
        console.error(
            `      git push origin ${DEFAULT_BRANCH}                  # if your commits should land`,
        );
        console.error(
            `      git reset --hard origin/${DEFAULT_BRANCH}          # if you want to discard local`,
        );
        console.error(``);
        console.error(
            `[yukticastle]    To bypass entirely (intentional stale-base run):`,
        );
        console.error(`      YUKTICASTLE_SKIP_MAIN_UPDATE_CHECK=true npm run agents:run -- ...`);
        console.error(``);
        process.exit(1);
    }

    // Pure fast-forward case (behind, not ahead) — safe to update.
    console.log(
        `[yukticastle]    local ${DEFAULT_BRANCH} is ${behindN} commit(s) behind — fast-forwarding…`,
    );
    const ff = spawnSync(
        "git",
        ["merge", "--ff-only", `origin/${DEFAULT_BRANCH}`],
        { encoding: "utf8", timeout: 30_000 },
    );
    if (ff.status !== 0) {
        console.error(``);
        console.error(`[yukticastle] ⛔ git merge --ff-only failed:`);
        console.error(ff.stderr || ff.stdout || "(no output)");
        console.error(``);
        console.error(
            `[yukticastle]    Likely cause: uncommitted local changes blocking the merge.`,
        );
        console.error(`[yukticastle]    Resolve:`);
        console.error(`      git status                           # what's dirty`);
        console.error(`      git stash push -m "yc-prefetch"      # stash + retry`);
        console.error(``);
        process.exit(1);
    }
    console.log(
        `[yukticastle]    ✓ updated to origin/${DEFAULT_BRANCH} (${remoteSha.slice(0, 8)})`,
    );
}

// PR F — parse security findings from the auditor's committed report.
//
// The auditor commits findings to .yukticastle/security-findings/<branch>.md
// with this required HTML-comment header for machine parsing:
//
//   <!-- yukticastle-security-audit-counts: critical=N high=N medium=N low=N info=N -->
//
// The body below is human-readable markdown — we ONLY consume the
// counts comment. Body shape is up to the auditor; operators read the
// markdown directly.
//
// We use `git show <branch>:<path>` so the file is read from the
// committed branch, not from the host worktree (which may be a
// different state — the agent committed inside the sandcastle
// container, the host worktree wasn't modified).
//
// ============================================================
// PR #63 — runWithIdleCompletionRecovery: wrapper for yukticastle.run()
//
// Works around an upstream sandcastle bug (accessquint PR #108,
// 2026-05-20): when the agent emits its completion signal but
// doesn't EXIT the process (just goes idle waiting for next
// prompt), sandcastle's idle-timeout watchdog fires before the
// completion-signal check runs. Symptom: run marked failed even
// though work landed (commits on branch, tests passing). Operator
// has to manually push the branch and open the PR.
//
// The workaround chain:
//
//   1. Attach `onAgentStreamEvent` to capture the agent's text
//      stream as it comes off the wire. Append each chunk to a
//      local buffer.
//
//   2. After each chunk, check the buffer for the completion
//      signal. When seen, arm a short grace timer.
//
//   3. If the grace timer fires (sandcastle hasn't returned yet,
//      meaning the agent is in the idle-wait state), abort the
//      agent process via AbortController. This shortcuts the
//      10-minute idle wait — operator gets back to the next phase
//      in 30s instead of 10min.
//
//   4. If `yukticastle.run()` throws (idle timeout, our abort, or
//      any other failure) AND we saw the completion signal in the
//      stream, SYNTHESIZE a success RunResult from git state. The
//      branch's commits are real (sandcastle's failure didn't roll
//      them back), so the downstream phases (reviewer, auditors,
//      etc.) can proceed normally.
//
// We lose iteration-level token usage on the recovery path — the
// synthesized iterations array is empty. That's the cost; the
// alternative is losing the entire run. The cost dashboard
// (ENHANCEMENTS.md #4) will need to handle this case explicitly
// (e.g. mark such runs "recovered" so cost-per-run averages
// aren't skewed).
//
// Upstream bug should be filed against @ai-hero/sandcastle (the
// fix there is: check the completion signal inside the onText
// callback in invokeAgent — when matched, resolve `timeoutSignal`
// to short-circuit the idle-timer check). When that upstream
// lands, this helper becomes a no-op and can be removed.
// ============================================================

interface IdleCompletionRecoveryOpts {
    /** Phase label for logging. "implementer" / "reviewer" / "security_auditor" / "migration_auditor" / "architect". */
    readonly phase: string;
    /** Branch the agent committed to — used to synthesize commit list on recovery. */
    readonly branch: string;
    /**
     * The `run()` options minus `signal` and `logging` (which this helper
     * manages). The wrapper preserves all other options unchanged.
     */
    readonly runOptions: Omit<yukticastle.RunOptions, "signal" | "logging">;
    /**
     * Grace period after observing the completion signal before forcibly
     * aborting the agent. Default 30s — gives sandcastle's natural
     * iteration-end path a chance to fire first (most well-behaved CLIs
     * exit within a few seconds of emitting the marker).
     */
    readonly completionGraceMs?: number;
}

async function runWithIdleCompletionRecovery(
    opts: IdleCompletionRecoveryOpts,
): Promise<yukticastle.RunResult> {
    const { phase, branch, runOptions } = opts;
    const completionGraceMs = opts.completionGraceMs ?? 30_000;

    // Resolve which signal(s) we're watching for. Matches sandcastle's
    // own default + normalization (see Orchestrator.js DEFAULT_COMPLETION_SIGNAL).
    const signalOpt = runOptions.completionSignal;
    const completionSignals: string[] =
        signalOpt === undefined
            ? ["<promise>COMPLETE</promise>"]
            : Array.isArray(signalOpt)
              ? signalOpt
              : [signalOpt];

    // Auto-generate a log path matching sandcastle's default convention
    // (`.yukticastle/logs/<branch>-<phase>.log`). Operators who already
    // tail `.yukticastle/logs/` see the same file they would have without
    // this wrapper.
    const safeBranch = branch.replace(/\//g, "-");
    const logPath = resolvePath(
        process.cwd(),
        ".yukticastle",
        "logs",
        `agent-${safeBranch}-${phase}.log`,
    );

    let completionSeen = false;
    let completionSeenAtMs = 0;
    let graceTimer: NodeJS.Timeout | null = null;
    const stdoutChunks: string[] = [];
    const abortCtl = new AbortController();

    const onAgentStreamEvent = (event: AgentStreamEvent) => {
        if (event.type !== "text") return;
        stdoutChunks.push(event.message);
        if (completionSeen) return;
        // Look at the last few chunks joined — completion signal could
        // straddle a stream boundary if the agent emits it in pieces.
        const tail = stdoutChunks.slice(-4).join("");
        for (const sig of completionSignals) {
            if (tail.includes(sig)) {
                completionSeen = true;
                completionSeenAtMs = Date.now();
                console.log(
                    `[yukticastle] ${phase}: observed completion signal "${sig.slice(0, 60)}" in stream; ` +
                        `arming ${Math.round(completionGraceMs / 1000)}s grace timer ` +
                        `(workaround for sandcastle idle-timeout-post-COMPLETE bug; see PR #63)`,
                );
                graceTimer = setTimeout(() => {
                    console.warn(
                        `[yukticastle] ${phase}: ${Math.round(completionGraceMs / 1000)}s grace expired after completion signal — aborting agent to bypass idle wait`,
                    );
                    abortCtl.abort(
                        new Error(
                            `yukticastle: graceful post-COMPLETE abort for ${phase} (workaround PR #63)`,
                        ),
                    );
                }, completionGraceMs);
                break;
            }
        }
    };

    try {
        const result = await yukticastle.run({
            ...runOptions,
            signal: abortCtl.signal,
            logging: {
                type: "file",
                path: logPath,
                onAgentStreamEvent,
            },
        });
        if (graceTimer) clearTimeout(graceTimer);
        return result;
    } catch (err) {
        if (graceTimer) clearTimeout(graceTimer);
        if (!completionSeen) {
            // Real failure — propagate unchanged. The handler's existing
            // crash-recovery logic takes it from here.
            throw err;
        }
        // We saw the completion signal but sandcastle still failed. Either:
        //   (a) The idle timeout fired before our grace timer could abort
        //       (unlikely with 30s grace vs 600s idle, but possible if the
        //       operator overrode the idle timeout much lower).
        //   (b) Our grace-timer abort fired and surfaced as the failure.
        //   (c) Some other failure between observation and return.
        //
        // In all three cases, the agent's work is on the branch (commits
        // are atomic — they don't depend on sandcastle's success). Read
        // the commit list from git and synthesize a success result.
        const synthesized = synthesizeRunResultFromGit({
            branch,
            completionSignal: completionSignals.find((s) =>
                stdoutChunks.join("").includes(s),
            ),
            stdout: stdoutChunks.join(""),
        });
        const recoveredAfterMs = Date.now() - completionSeenAtMs;
        console.warn(``);
        console.warn(
            `[yukticastle] ${phase}: sandcastle reported failure but agent emitted completion signal — RECOVERING.`,
        );
        console.warn(
            `[yukticastle]   cause:      ${(err as Error).message ?? String(err)}`,
        );
        console.warn(
            `[yukticastle]   recovered:  ${synthesized.commits.length} commit(s) on ${branch}`,
        );
        console.warn(
            `[yukticastle]   elapsed:    ${Math.round(recoveredAfterMs / 1000)}s from signal-observed to recovery`,
        );
        console.warn(
            `[yukticastle]   note:       iteration-level token usage is LOST on this recovery path.`,
        );
        console.warn(``);
        return synthesized;
    }
}

function synthesizeRunResultFromGit(opts: {
    branch: string;
    completionSignal: string | undefined;
    stdout: string;
}): yukticastle.RunResult {
    // Read commits on the agent branch that aren't on origin/<default>.
    // Uses --format=%H so we get just the SHAs; we don't have access to
    // the rich commit metadata sandcastle would have collected, but we
    // don't need it — downstream code only consumes commits[].sha.
    const probe = spawnSync(
        "git",
        [
            "log",
            "--format=%H",
            "--reverse",
            `origin/${DEFAULT_BRANCH}..${opts.branch}`,
        ],
        { encoding: "utf8", timeout: 10_000 },
    );
    const commits: { sha: string }[] = [];
    if (probe.status === 0 && probe.stdout) {
        for (const line of probe.stdout.split("\n")) {
            const sha = line.trim();
            if (sha) commits.push({ sha });
        }
    }
    return {
        // Iterations is empty — we don't have per-iteration usage data.
        // The cost dashboard (ENHANCEMENTS.md #4) needs to treat
        // empty-iterations runs as "recovered" and not skew averages.
        iterations: [],
        completionSignal: opts.completionSignal,
        stdout: opts.stdout,
        commits,
        branch: opts.branch,
        preservedWorktreePath: undefined,
    };
}

// Returns counts with a `foundFile` discriminator so callers can
// distinguish "audit ran and found nothing" from "audit didn't run".
function parseSecurityFindings(
    branch: string,
    findingsPathRel: string,
): SecurityFindingCounts & { foundFile: boolean } {
    const empty: SecurityFindingCounts & { foundFile: boolean } = {
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
        foundFile: false,
    };
    const r = spawnSync(
        "git",
        ["show", `${branch}:${findingsPathRel}`],
        { encoding: "utf8", timeout: 10_000 },
    );
    if (r.status !== 0 || !r.stdout) {
        return empty;
    }
    const match = r.stdout.match(
        /<!--\s*yukticastle-security-audit-counts:\s*critical=(\d+)\s+high=(\d+)\s+medium=(\d+)\s+low=(\d+)\s+info=(\d+)\s*-->/,
    );
    if (!match) {
        // File exists but no parseable counts header. Treat as "ran
        // but couldn't classify" — counts are all zero, but file is
        // present so the operator can read it manually.
        console.warn(
            `[yukticastle] security_auditor wrote ${findingsPathRel} but the counts header was missing or malformed. Reading body anyway.`,
        );
        return { ...empty, foundFile: true };
    }
    return {
        critical: Number(match[1]) || 0,
        high: Number(match[2]) || 0,
        medium: Number(match[3]) || 0,
        low: Number(match[4]) || 0,
        info: Number(match[5]) || 0,
        foundFile: true,
    };
}

// PR H — parse migration findings (same shape as
// parseSecurityFindings, different file path + comment header so we
// don't cross-contaminate parsing if both auditors happen to commit
// to the same branch).
function parseMigrationFindings(
    branch: string,
    findingsPathRel: string,
): MigrationFindingCounts & { foundFile: boolean } {
    const empty: MigrationFindingCounts & { foundFile: boolean } = {
        critical: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
        foundFile: false,
    };
    const r = spawnSync(
        "git",
        ["show", `${branch}:${findingsPathRel}`],
        { encoding: "utf8", timeout: 10_000 },
    );
    if (r.status !== 0 || !r.stdout) {
        return empty;
    }
    const match = r.stdout.match(
        /<!--\s*yukticastle-migration-audit-counts:\s*critical=(\d+)\s+high=(\d+)\s+medium=(\d+)\s+low=(\d+)\s+info=(\d+)\s*-->/,
    );
    if (!match) {
        console.warn(
            `[yukticastle] migration_auditor wrote ${findingsPathRel} but the counts header was missing or malformed. Reading body anyway.`,
        );
        return { ...empty, foundFile: true };
    }
    return {
        critical: Number(match[1]) || 0,
        high: Number(match[2]) || 0,
        medium: Number(match[3]) || 0,
        low: Number(match[4]) || 0,
        info: Number(match[5]) || 0,
        foundFile: true,
    };
}

// PR H — runtime diff-match gating. Migration Auditor is dual-gated:
// the env var `YUKTICASTLE_MIGRATION_AUDITOR=true` AND the diff must
// touch at least one path matching `YUKTICASTLE_MIGRATION_PATHS`
// (default covers Prisma + generic + Rails + Drizzle + raw SQL).
//
// Returns the list of MATCHING file paths so the caller can record
// them in runs.jsonl + surface them to the operator. Empty array
// means "auditor should not run this time".
//
// Pattern syntax:
//   - "*.ext"  — extension match (file path ends with ".ext")
//   - anything else — prefix match against the file path
function diffMatchesMigrationPaths(branch: string): string[] {
    const patternsRaw =
        process.env.YUKTICASTLE_MIGRATION_PATHS ??
        "prisma/migrations/,migrations/,db/migrate/,drizzle/,*.sql";
    const patterns = patternsRaw
        .split(",")
        .map((p) => p.trim())
        .filter((p) => p.length > 0);
    const r = spawnSync(
        "git",
        [
            "diff",
            "--name-only",
            `origin/${DEFAULT_BRANCH}..${branch}`,
        ],
        { encoding: "utf8", timeout: 10_000 },
    );
    if (r.status !== 0) {
        // Don't block — if we can't compute the diff for some reason,
        // err on the side of running the auditor (it's cheap if
        // there's nothing to find).
        console.warn(
            `[yukticastle] migration_auditor: couldn't compute diff vs origin/${DEFAULT_BRANCH} — running anyway`,
        );
        return ["<diff-unavailable>"];
    }
    const changedFiles = r.stdout.split("\n").filter(Boolean);
    const matches: string[] = [];
    for (const file of changedFiles) {
        for (const pattern of patterns) {
            if (pattern.startsWith("*.")) {
                const ext = pattern.slice(1); // ".sql"
                if (file.endsWith(ext)) {
                    matches.push(file);
                    break;
                }
            } else {
                if (file.startsWith(pattern)) {
                    matches.push(file);
                    break;
                }
            }
        }
    }
    return matches;
}

interface DeletionGuardResult {
    tripped: boolean;
    /** Human-readable reasons, empty if not tripped. */
    reasons: string[];
    /** Diff stats for the operator-facing message. */
    totalInsertions: number;
    totalDeletions: number;
    /** Files deleted (status D). */
    deletedFiles: string[];
    /** Subset of deletedFiles that's under a protected path. */
    protectedDeletions: string[];
}

function checkDeletionGuard(branch: string): DeletionGuardResult {
    const empty: DeletionGuardResult = {
        tripped: false,
        reasons: [],
        totalInsertions: 0,
        totalDeletions: 0,
        deletedFiles: [],
        protectedDeletions: [],
    };
    if (process.env.YUKTICASTLE_ALLOW_BULK_DELETIONS === "true") {
        return empty;
    }

    // Diff against origin/<default>, NOT the worktree's local <default>.
    // Local <default> might still be the stale snapshot the agent
    // branched off of — diffing against it would hide the catastrophe.
    const numstat = spawnSync(
        "git",
        ["diff", "--numstat", `origin/${DEFAULT_BRANCH}..${branch}`],
        { encoding: "utf8", timeout: 10_000 },
    );
    if (numstat.status !== 0) {
        // Don't block the run on a guard-internal failure. Log loudly
        // so we know it happened.
        console.warn(
            `[yukticastle] ⚠  deletion guard: git diff --numstat origin/${DEFAULT_BRANCH}..${branch} failed; skipping check.`,
        );
        console.warn(
            `[yukticastle]    stderr: ${(numstat.stderr || "").slice(0, 200)}`,
        );
        return empty;
    }

    let totalInsert = 0;
    let totalDelete = 0;
    for (const line of numstat.stdout.split("\n")) {
        if (!line.trim()) continue;
        // Format: "INSERT\tDELETE\tPATH" — or "-\t-\tPATH" for binary.
        const [insStr, delStr] = line.split("\t");
        const ins = Number(insStr) || 0;
        const del = Number(delStr) || 0;
        totalInsert += ins;
        totalDelete += del;
    }

    // Use --diff-filter=D to find files DELETED (status D). That's
    // the deletion-vs-modification distinction the guard cares about.
    const deletedFilesProbe = spawnSync(
        "git",
        [
            "diff",
            "--diff-filter=D",
            "--name-only",
            `origin/${DEFAULT_BRANCH}..${branch}`,
        ],
        { encoding: "utf8", timeout: 10_000 },
    );
    const deletedFiles: string[] = [];
    if (deletedFilesProbe.status === 0) {
        for (const f of deletedFilesProbe.stdout.split("\n")) {
            const file = f.trim();
            if (file) deletedFiles.push(file);
        }
    }

    const protectedPaths = (
        process.env.YUKTICASTLE_PROTECTED_PATHS ??
        "prisma/migrations/,tasks/,.yukticastle/,pine/"
    )
        .split(",")
        .map((p) => p.trim())
        .filter((p) => p.length > 0);

    const protectedDeletions = deletedFiles.filter((f) =>
        protectedPaths.some((p) => f.startsWith(p)),
    );

    const RATIO =
        Number(process.env.YUKTICASTLE_DELETION_GUARD_RATIO) || 3;
    const FLOOR =
        Number(process.env.YUKTICASTLE_DELETION_GUARD_FLOOR) || 50;

    const reasons: string[] = [];
    if (
        totalDelete > RATIO * Math.max(totalInsert, 1) &&
        totalDelete > FLOOR
    ) {
        reasons.push(
            `net deletions ${totalDelete} > ${RATIO}× insertions ${totalInsert} (floor=${FLOOR})`,
        );
    }
    if (protectedDeletions.length > 0) {
        const sample = protectedDeletions.slice(0, 5).join(", ");
        const more =
            protectedDeletions.length > 5
                ? `, … (+${protectedDeletions.length - 5} more)`
                : "";
        reasons.push(
            `${protectedDeletions.length} file(s) deleted under protected paths: ${sample}${more}`,
        );
    }

    return {
        tripped: reasons.length > 0,
        reasons,
        totalInsertions: totalInsert,
        totalDeletions: totalDelete,
        deletedFiles,
        protectedDeletions,
    };
}

ensureHostBaseFresh();

console.log(``);
if (taskInput.sourcePath) {
    console.log(`[yukticastle] task source: ${taskInput.sourcePath}`);
    console.log(`[yukticastle] task title:  ${taskInput.branchSlugSource.split("\n")[0]}`);
} else {
    console.log(`[yukticastle] task: ${TASK}`);
}
console.log(`[yukticastle] branch:      ${branch}`);
console.log(``);

// ============================================================
// Run-history tracking
//
// Captures one JSONL record per run at `.yukticastle/runs.jsonl`.
// Powers `npm run agents:report` (cost trends, fallback rate, etc.).
// See docs/runs-schema.md for the schema; v1 is small and additive.
//
// State is built up as the script progresses. The single
// process.on('exit') handler at the bottom writes whatever has been
// captured — partial state on early exits (silent failure, etc.) is
// recorded too, so the operator's history reflects what actually
// happened, not just clean runs.
// ============================================================

const RUNS_SCHEMA_VERSION = 1;
const runStartedAt = new Date();
const runStartedAtMs = Date.now();

interface PhaseRecord {
    iterations: number;
    commits: number;
    completed: boolean;
    tokens: {
        input: number;
        output: number;
        cache_read: number;
        cache_create: number;
    };
    cost_usd: number;
}

// PR F — Security Auditor finding-count breakdown. Per-severity tally
// from the auditor's JSON findings block. Populated by
// runSecurityAuditorPhase; consumed by runs.jsonl readers + the future
// cost/health dashboard (ENHANCEMENTS.md #4). Critical/high counts also
// drive the orchestrator's halt decision.
interface SecurityFindingCounts {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
}

interface SecurityAuditorRecord extends PhaseRecord {
    findings_count: SecurityFindingCounts;
    /** Path to the markdown report relative to repo root. */
    findings_path: string | null;
    /** True iff the auditor's findings tripped the halt-on-critical
     *  guard (one or more `critical` findings). */
    halt_on_critical: boolean;
}

// PR H — Migration Auditor finding-count breakdown. Same severity
// bands as security_auditor (critical / high / medium / low / info)
// but the focus is data-loss + production-data-integrity issues
// (DROP TABLE without backfill, ALTER COLUMN NOT NULL without
// populating existing rows, etc.). Structurally identical to
// SecurityFindingCounts; aliased to keep call sites self-documenting
// without forcing duplicate type declarations.
type MigrationFindingCounts = SecurityFindingCounts;

interface MigrationAuditorRecord extends PhaseRecord {
    findings_count: MigrationFindingCounts;
    findings_path: string | null;
    /** True iff the auditor's findings tripped the halt-on-critical
     *  guard (data-loss risk). */
    halt_on_critical: boolean;
    /** Paths that triggered the auditor to run. Empty when the
     *  auditor was gated off or ran for some other reason. */
    triggered_by_paths: string[];
}

// PR I — Architect / Planner. Produces a structured plan file that
// the implementer consumes via {{PLAN}} prompt substitution.
//
// The plan_excerpt is capped at ~2KB so runs.jsonl entries don't
// balloon. Operators reading the full plan should use
// `git show <branch>:<plan_path>`.
interface ArchitectRecord extends PhaseRecord {
    /** Path to the plan markdown relative to repo root. Null if
     *  the architect ran but didn't commit a file. */
    plan_path: string | null;
    /** Plan file size in bytes (post-commit). Null when missing. */
    plan_size_bytes: number | null;
    /** First ~2KB of the plan body, for at-a-glance dashboard use.
     *  Null when the plan file is missing. */
    plan_excerpt: string | null;
}

// PR J — Test Engineer. Runs post-implementer in the same parallel
// group as reviewer + auditors. Reads the implementer's diff,
// identifies code paths without tests, writes tests, commits them.
// Distinct from auditors in that it WRITES code (test files) rather
// than producing findings markdown. No halt path — test gaps are
// forgivable.
interface TestEngineerRecord extends PhaseRecord {
    /** Test files the engineer added/modified (paths relative to repo
     *  root). Empty when the phase ran but didn't add tests (e.g.
     *  determined existing coverage was already adequate). */
    test_files_committed: string[];
    /** Net added lines across `tests_files_committed`. Approximate;
     *  computed from `git diff --numstat` on the engineer's commit. */
    test_lines_added: number;
}

type RunResult =
    | "ok"
    | "implementer_silent_failure"
    | "no_commits"
    | "reviewer_silent_failure"
    | "reviewer_crashed"
    // PR #53 — post-reviewer deletion guard tripped. Reviewer
    // signaled COMPLETE but the diff against origin/main showed
    // either (a) a deletion catastrophe (net deletions > 3× inserts
    // above the floor) or (b) deletion of a file under a protected
    // path (prisma/migrations/, tasks/, .yukticastle/, pine/). Orchestrator
    // refuses to mark the run successful so the auto-PR flow halts.
    // See ENHANCEMENTS.md #43 (gextrader 2026-05-18 near-miss).
    | "deletion_guard_tripped"
    // PR F — Security Auditor found one or more `critical` findings
    // and emitted a halt signal. Auto-PR suppressed; operator must
    // inspect the findings report at .yukticastle/security-findings/<branch>.md
    // before merging anything.
    | "security_halt"
    // PR F — Security Auditor phase threw or otherwise crashed
    // (codex CLI error, network glitch, JSON parse fail, etc.).
    // The implementer + reviewer work is intact on the branch; the
    // auditor just didn't add value this run. Less severe than
    // security_halt — manual review is still on the table.
    | "security_auditor_crashed"
    // PR H — Migration Auditor found one or more `critical` findings
    // (typically data-loss patterns: DROP TABLE / DROP COLUMN /
    // ALTER COLUMN ... NOT NULL without backfill) and emitted a
    // halt signal. Auto-PR suppressed; operator must inspect
    // .yukticastle/migration-findings/<branch>.md before merging.
    //
    // Outranks security_halt in the severity priority because
    // data loss is irreversible (security breaches are sometimes
    // recoverable; data is not).
    | "migration_halt"
    // PR H — Migration Auditor phase threw / crashed. Implementer +
    // reviewer work intact; migration auditor just didn't add value
    // this run. Operator should still manually review any
    // prisma/migrations/ changes before merging.
    | "migration_auditor_crashed"
    // PR I — Architect / Planner phase crashed (timeout, container
    // failure, JSON parse error). Continues to implementer without
    // a plan; implementer's prompt receives empty {{PLAN}}. Lower
    // severity than implementer_silent_failure — architect's value
    // is advisory, not blocking.
    | "architect_crashed"
    // PR J — Test Engineer phase crashed. Implementer's work +
    // reviewer + auditor results all intact; the engineer just
    // didn't add the test coverage it would have. Forgivable —
    // operator can write tests by hand or re-run with the engineer
    // enabled. No halt path on this role.
    | "test_engineer_crashed"
    | "error";

interface RunState {
    schema_version: number;
    started_at: string;
    finished_at: string | null;
    task: {
        path: string | null;
        title: string;
        description_preview: string;
    };
    branch: string;
    models: {
        implementer: string | null;
        reviewer: string | null;
        // PR I — architect's model is null when the phase didn't run.
        architect?: string | null;
    };
    reviewer_kind: "claude" | "codex" | null;
    auth: {
        anthropic:
            | "macos-keychain-oauth"
            | "env-oauth"             // CLAUDE_CODE_OAUTH_TOKEN env (Linux/Codespace MAX-billed path)
            | "api-key"
            | "keychain-api-key"
            | "none";
        // ENHANCEMENTS.md #13 — proactive between-phase refresh
        // fired (true) or didn't (false). Null when feature was off.
        proactive_refresh_fired: boolean | null;
    };
    // PR #48 — host-side codex access_token refresh telemetry.
    // Null when codex isn't the reviewer (we didn't need to forward
    // auth.json, so we didn't probe its freshness). See
    // CodexRefreshTelemetry above for shape.
    codex_refresh: CodexRefreshTelemetry | null;
    phases: {
        spec_critic?: PhaseRecord | null;        // ENHANCEMENTS.md #15
        // PR I — Architect / Planner. Optional / null when the
        // phase is gated off (YUKTICASTLE_ARCHITECT not set).
        // Produces a plan file the implementer consumes; the
        // ArchitectRecord carries pointers to the plan blob so
        // runs.jsonl readers can find it without rebuilding paths.
        architect?: ArchitectRecord | null;
        implementer: PhaseRecord | null;
        reviewer: PhaseRecord | null;
        // PR F — Security Auditor. Optional / null when the phase is
        // gated off (YUKTICASTLE_SECURITY_AUDITOR not set).
        security_auditor?: SecurityAuditorRecord | null;
        // PR H — Migration Auditor. Optional / null when the phase
        // is gated off OR when the diff didn't touch any migration
        // paths (auditor is dual-gated: env flag + diff match).
        migration_auditor?: MigrationAuditorRecord | null;
        // PR J — Test Engineer. Optional / null when the phase is
        // gated off (YUKTICASTLE_TEST_ENGINEER not set).
        test_engineer?: TestEngineerRecord | null;
    };
    wall_time_s: number | null;
    files_changed: number | null;
    total_cost_usd: number;
    total_cost_billed_usd: number;
    result: RunResult;
    // Set when result === "reviewer_crashed" (or any future failure that
    // captures a thrown message). Populated by the Phase 2 catch block.
    error?: string;
    // Set when the orchestrator auto-committed the implementer's
    // uncommitted worktree state because the implementer emitted
    // <promise>COMPLETE</promise> without calling `git commit` itself.
    // See FEEDBACK-gextrader-impl-complete-no-autocommit.md.
    auto_commit_sha?: string;
}

const runState: RunState = {
    schema_version: RUNS_SCHEMA_VERSION,
    started_at: runStartedAt.toISOString(),
    finished_at: null,
    task: {
        path: taskInput.sourcePath ?? null,
        title: taskInput.branchSlugSource.split("\n")[0].slice(0, 200),
        description_preview: TASK.slice(0, 200),
    },
    branch,
    models: { implementer: null, reviewer: null },
    reviewer_kind: null,
    auth: { anthropic: "none", proactive_refresh_fired: null },
    codex_refresh: null,
    phases: { implementer: null, reviewer: null },
    wall_time_s: null,
    files_changed: null,
    total_cost_usd: 0,
    total_cost_billed_usd: 0,
    result: "error", // overwritten on success / specific failure modes
};

function recordPhase(
    // PR F widened the discriminator to accept "security_auditor";
    // PR H added "migration_auditor"; PR I added "architect"; PR J
    // added "test_engineer". The `name` parameter is unused inside
    // the body — it's only here so callsites read self-documentingly.
    // Any future role can add itself to this union without semantic
    // change.
    name:
        | "implementer"
        | "reviewer"
        | "security_auditor"
        | "migration_auditor"
        | "architect"
        | "test_engineer",
    result: yukticastle.RunResult,
    cost: number,
): PhaseRecord {
    const totals = result.iterations.reduce(
        (acc, it) => {
            const u = it.usage;
            return {
                input: acc.input + (u?.inputTokens ?? 0),
                output: acc.output + (u?.outputTokens ?? 0),
                cache_read: acc.cache_read + (u?.cacheReadInputTokens ?? 0),
                cache_create:
                    acc.cache_create + (u?.cacheCreationInputTokens ?? 0),
            };
        },
        { input: 0, output: 0, cache_read: 0, cache_create: 0 },
    );
    return {
        iterations: result.iterations.length,
        commits: result.commits.length,
        completed: result.completionSignal !== undefined,
        tokens: totals,
        cost_usd: Number(cost.toFixed(6)),
    };
}

function writeRunRecord(): void {
    runState.finished_at = new Date().toISOString();
    runState.wall_time_s = Math.round((Date.now() - runStartedAtMs) / 1000);
    const cwd = process.cwd();
    const runsPath = resolvePath(cwd, ".yukticastle/runs.jsonl");
    try {
        mkdirSync(dirname(runsPath), { recursive: true });
        appendFileSync(runsPath, JSON.stringify(runState) + "\n");
    } catch (err) {
        // Best-effort — don't let history-write fail crash the script.
        console.warn(
            `[yukticastle] could not append runs.jsonl: ${(err as Error).message}`,
        );
    }
}

process.on("exit", () => {
    writeRunRecord();
});
// SIGINT/SIGTERM aren't covered by 'exit' alone; explicitly route.
process.on("SIGINT", () => process.exit(130));
process.on("SIGTERM", () => process.exit(143));

// ============================================================
// Auth + sandbox config
// ============================================================

// Auth resolution — tries paths in this priority order:
//
//   1. macOS Keychain extract (free, MAX-subscription-billed). Requires
//      Claude Code CLI installed + signed in on the host. Verified
//      working 2026-05-08: extract via `security find-generic-password
//      -s "Claude Code-credentials" -w`, forward access token as
//      ANTHROPIC_AUTH_TOKEN env var. Container's claude CLI uses Bearer
//      auth against api.anthropic.com, billed against the host's MAX
//      `default_claude_max_5x` rate-limit tier. YuktiCastle runs are
//      effectively free.
//
//   2. ANTHROPIC_API_KEY (per-token billed). The legacy fallback.
//      Used when the host doesn't have Claude Code installed or the
//      Keychain entry is missing.
//
// CRITICAL: only forward variables that have actual values. Claude CLI
// treats "set-but-empty" as "OAuth attempted, no token, fail" instead
// of falling through to ANTHROPIC_API_KEY — which means forwarding
// ANTHROPIC_AUTH_TOKEN="" can break a perfectly valid ANTHROPIC_API_KEY
// flow. This bit us during the demo run when the macOS shell had
// ANTHROPIC_API_KEY="" exported. The operator-side fix is `unset
// ANTHROPIC_API_KEY` in the launching shell; the in-code fix is to
// only forward vars with actual values.

const agentEnv: Record<string, string> = {};

// Buffer (ms) — if the token has less than this remaining at launch, fall
// back to API key. Prevents a long-running task from hitting mid-run 401s
// when the access token expires partway through. Configurable via
// YUKTICASTLE_OAUTH_MIN_REMAINING_MS in .yukticastle/.env.
//
// Default 60 min: covers our worst-case run ceiling (~80 min if all
// iterations max out + slow cold starts). Tokens normally have ~6h life,
// so the buffer only kicks in when the host CLI hasn't been used in
// hours — in which case running `claude` once on the host triggers a
// keychain refresh.
// Backward-compat: read both `YUKTICASTLE_*` (current) and
// `SANDCASTLE_*` (legacy, pre-Phase-B-rebrand) env-var names. The new
// name wins; the old name is honored to avoid breaking existing
// operator workflows. Same pattern applied to every other env-var
// rename below — search for `legacyEnv()`.
function legacyEnv(newName: string, oldName: string): string | undefined {
    if (process.env[newName] !== undefined && process.env[newName] !== "") {
        return process.env[newName];
    }
    if (process.env[oldName] !== undefined && process.env[oldName] !== "") {
        console.warn(
            `[yukticastle] env-var ${oldName} is deprecated; rename to ${newName}. (Honored for now; will be removed in a future major.)`,
        );
        return process.env[oldName];
    }
    return undefined;
}

const OAUTH_MIN_REMAINING_MS =
    Number(legacyEnv("YUKTICASTLE_OAUTH_MIN_REMAINING_MS", "SANDCASTLE_OAUTH_MIN_REMAINING_MS")) ||
    60 * 60_000;

// YUKTICASTLE_REQUIRE_OAUTH=true → if keychain OAuth is unavailable for any
// reason (missing entry, expired, below buffer), refuse to silently fall
// back to ANTHROPIC_API_KEY. Useful for operators on a MAX subscription
// who want to guarantee free runs and treat API-key billing as a misconfig.
// Backward-compat: SANDCASTLE_REQUIRE_OAUTH (legacy, pre-Phase-B-rebrand)
// still honored via legacyEnv() with a deprecation warning.
const REQUIRE_OAUTH =
    String(
        legacyEnv("YUKTICASTLE_REQUIRE_OAUTH", "SANDCASTLE_REQUIRE_OAUTH") ?? "",
    ).toLowerCase() === "true";

// Why the keychain skipped: lets the main flow show a more useful hint
// after falling back to ANTHROPIC_API_KEY. "missing" means no claude CLI
// signed in on this host (no nudge needed). The other reasons mean the
// operator already has Claude Code set up but the token needs a refresh.
type KeychainSkipReason = null | "missing" | "expired" | "below_buffer";
let lastKeychainSkipReason: KeychainSkipReason = null;

// Pure time-remaining peek — returns ms remaining on the keychain
// access token, or undefined if no entry / malformed / no expiresAt.
// Unlike tryKeychainOAuth(), does NOT apply the buffer; callers
// decide policy based on the raw remaining.
function peekKeychainRemainingMs(): number | undefined {
    try {
        const r = spawnSync(
            "security",
            ["find-generic-password", "-s", "Claude Code-credentials", "-w"],
            { encoding: "utf8", timeout: 3000 },
        );
        if (r.status !== 0 || !r.stdout) return undefined;
        const json = JSON.parse(r.stdout.trim());
        const expiresAt = json?.claudeAiOauth?.expiresAt;
        if (typeof expiresAt !== "number") return undefined;
        return expiresAt - Date.now();
    } catch {
        return undefined;
    }
}

// Try to extract OAuth from macOS Keychain first — free MAX-billed path.
function tryKeychainOAuth(): string | undefined {
    lastKeychainSkipReason = null;
    try {
        const result = spawnSync(
            "security",
            ["find-generic-password", "-s", "Claude Code-credentials", "-w"],
            { encoding: "utf8", timeout: 3000 },
        );
        if (result.status !== 0 || !result.stdout) {
            lastKeychainSkipReason = "missing";
            return undefined;
        }
        const json = JSON.parse(result.stdout.trim());
        const accessToken = json?.claudeAiOauth?.accessToken;
        const expiresAt = json?.claudeAiOauth?.expiresAt;
        if (typeof accessToken !== "string" || !accessToken.startsWith("sk-ant-oat")) {
            lastKeychainSkipReason = "missing";
            return undefined;
        }
        // Refuse tokens that are expired OR within the safety buffer.
        // Refresh-token rotation happens on the host's Claude CLI, not here;
        // running `claude` once on the host triggers a renewal.
        if (typeof expiresAt === "number") {
            const remainingMs = expiresAt - Date.now();
            if (remainingMs <= 0) {
                console.warn(
                    `[yukticastle] Keychain OAuth token expired ${Math.round(-remainingMs / 60000)} min ago — host Claude CLI should auto-refresh on next use. Falling back to API key.`,
                );
                lastKeychainSkipReason = "expired";
                return undefined;
            }
            if (remainingMs < OAUTH_MIN_REMAINING_MS) {
                console.warn(
                    `[yukticastle] Keychain OAuth token has only ${Math.round(remainingMs / 60000)} min remaining ` +
                        `(buffer = ${Math.round(OAUTH_MIN_REMAINING_MS / 60000)} min). Falling back to API key to avoid mid-run 401. ` +
                        `Run \`claude\` once on the host to refresh.`,
                );
                lastKeychainSkipReason = "below_buffer";
                return undefined;
            }
        }
        return accessToken;
    } catch {
        lastKeychainSkipReason = "missing";
        return undefined;
    }
}

// Read a YuktiCastle-managed Anthropic API key from the macOS
// Keychain. Lets the operator store the per-token-billed failover
// key ONCE per host instead of pasting it into every project's
// .yukticastle/.env — every fresh clone / worktree picks it up
// automatically.
//
// Service: yukticastle-anthropic-api-key
// Set up:  npm run yukticastle:api-key:set
// Remove:  npm run yukticastle:api-key:unset
// Inspect: npm run yukticastle:api-key:peek
//
// Project-local ANTHROPIC_API_KEY (via .yukticastle/.env or shell
// export) still takes precedence — this layer only fires when no
// env-var key is available, so per-project overrides keep working.
function tryKeychainApiKey(): string | undefined {
    try {
        const r = spawnSync(
            "security",
            ["find-generic-password", "-s", "yukticastle-anthropic-api-key", "-w"],
            { encoding: "utf8", timeout: 3000 },
        );
        if (r.status !== 0 || !r.stdout) return undefined;
        const key = r.stdout.trim();
        // Sanity check — Anthropic keys start with `sk-ant-`. If the
        // entry holds something else (typo, stale paste), refuse it
        // so we don't forward a bogus value and get an opaque 401.
        if (!key.startsWith("sk-ant-")) {
            console.warn(
                `[yukticastle] Keychain api-key entry exists but doesn't start with "sk-ant-" — ignoring. Fix with: npm run yukticastle:api-key:set`,
            );
            return undefined;
        }
        return key;
    } catch {
        return undefined;
    }
}

// Single-source "what to do" message reused by both the warning hint
// (when falling back) and the hard error (when REQUIRE_OAUTH is set).
function refreshHintLines(): string[] {
    return [
        `To refresh the keychain token, in another terminal run:`,
        `  npm run claude:login    # one-shot helper (5 sec)`,
        `  - or -`,
        `  claude                  # interactive: press Enter once, then Ctrl+C`,
        `Then re-launch this command.`,
    ];
}

// ============================================================
// Codex OAuth (~/.codex/auth.json) — Tier 6 #41 / HANDOFF-codex-oauth.md
//
// When roles.json reviewer is `provider: "openai"`, we want to use the
// operator's ChatGPT subscription (Plus / Team / Pro / Business) instead
// of billing per-token against OPENAI_API_KEY. That subscription is what
// unlocks the `-codex` family of models (gpt-5-codex, etc.) and yields
// $0 reviewer-phase cost.
//
// Mechanism:
//   1. Host's codex CLI mints `~/.codex/auth.json` via `codex login`
//      (or `setup-token`). File contains chatgpt-mode tokens: id_token,
//      access_token (1h JWT), refresh_token (~months), account_id.
//   2. This helper reads + validates the file at orchestrator startup.
//   3. When the reviewer phase IS codex, we forward the full JSON via
//      env var (CODEX_AUTH_JSON, reviewer-phase-only) into the container.
//   4. An onSandboxReady hook materializes it to ~/.codex/auth.json
//      with chmod 600 inside the container.
//   5. Container's codex CLI auto-refreshes the access_token via the
//      refresh_token on first call — same mechanism that runs on host.
//
// We do NOT block on access_token expiry: codex CLI inside the container
// will refresh transparently using refresh_token. We only require that
// auth_mode === "chatgpt" AND tokens.{access_token, refresh_token,
// account_id} all exist. The buffer check (15-min default) is
// informational only — logs a warning but doesn't block.
//
// Security: codex auth tokens are forwarded ONLY to the reviewer phase,
// never to the implementer. Limits blast radius if the implementer
// somehow exfiltrates env vars. The materialized file is chmod 600.

type CodexSkipReason =
    | "missing"
    | "malformed"
    | "wrong_mode"
    | "incomplete_tokens"
    | "below_buffer";

let lastCodexSkipReason: CodexSkipReason | null = null;

// ENHANCEMENTS.md #17 / PR #48 — codex pre-run refresh telemetry.
// Captures whether we fired the host-side refresh before forwarding
// auth.json into the reviewer container, so `runs.jsonl` can correlate
// 401-recurrence with refresh state. Populated by
// `ensureCodexAuthFreshForReviewer()` at reviewer-env build time.
interface CodexRefreshTelemetry {
    /** Whether we attempted a refresh this run. False if disabled, no codex
     *  auth resolved, or token was still fresh. */
    attempted: boolean;
    /** True iff the host `codex exec` invocation exited 0. */
    success: boolean;
    /** Wall-time of the refresh subprocess, ms. 0 when not attempted. */
    duration_ms: number;
    /** access_token `exp` claim (unix seconds) BEFORE the refresh. */
    exp_before_seconds: number | null;
    /** access_token `exp` claim AFTER the refresh (re-read auth.json). */
    exp_after_seconds: number | null;
    /** Model passed to `codex exec --model` for the refresh ping. */
    model_used: string | null;
    /** stderr from the refresh subprocess if it failed, else null. */
    error: string | null;
    /** Why we skipped the refresh attempt, when `attempted === false`. */
    skipped_reason?: "disabled" | "fresh" | "no_initial_auth" | "no_exp_claim";
}

interface CodexAuthResolved {
    /** Raw JSON string suitable for env-var forwarding (compact JSON). */
    rawJson: string;
    /** chatgpt_plan_type from the access_token JWT — "team" / "plus" / etc. */
    planType: string | null;
    /** access_token expiry in unix seconds (informational; CLI refreshes anyway). */
    expSeconds: number | null;
}

// Configurable buffer for the access_token's `exp`. 1h life vs claude's
// 6h → don't use the same 60-min default. 15 min is enough headroom for
// a typical reviewer phase to complete; tighter than that risks a
// mid-phase refresh that could race with codex CLI internals.
const OAUTH_MIN_REMAINING_MS_CODEX =
    Number(
        legacyEnv(
            "YUKTICASTLE_CODEX_OAUTH_MIN_REMAINING_MS",
            "SANDCASTLE_CODEX_OAUTH_MIN_REMAINING_MS",
        ),
    ) || 15 * 60_000;

function decodeJwtPayload(jwt: string): Record<string, unknown> | null {
    // Standard base64url with right-pad fix. JWTs in production
    // sometimes strip padding; Node's Buffer.from('base64') needs it.
    const parts = jwt.split(".");
    if (parts.length !== 3) return null;
    try {
        let b64 = parts[1]!.replace(/-/g, "+").replace(/_/g, "/");
        while (b64.length % 4 !== 0) b64 += "=";
        const payload = Buffer.from(b64, "base64").toString("utf8");
        return JSON.parse(payload) as Record<string, unknown>;
    } catch {
        return null;
    }
}

function tryCodexOAuth(): CodexAuthResolved | undefined {
    lastCodexSkipReason = null;
    const home = process.env.HOME;
    if (!home) {
        lastCodexSkipReason = "missing";
        return undefined;
    }
    const path = resolvePath(home, ".codex", "auth.json");
    if (!existsSync(path)) {
        lastCodexSkipReason = "missing";
        return undefined;
    }
    let raw: string;
    try {
        raw = readFileSync(path, "utf8");
    } catch {
        lastCodexSkipReason = "missing";
        return undefined;
    }
    let parsed: {
        auth_mode?: string;
        tokens?: {
            id_token?: string;
            access_token?: string;
            refresh_token?: string;
            account_id?: string;
        };
    };
    try {
        parsed = JSON.parse(raw);
    } catch {
        lastCodexSkipReason = "malformed";
        return undefined;
    }
    if (parsed.auth_mode !== "chatgpt") {
        // Operator is using API-key mode via codex CLI; we don't have an
        // OAuth credential to forward. (They could still use OPENAI_API_KEY
        // for non-`-codex` models; that's the existing path.)
        lastCodexSkipReason = "wrong_mode";
        return undefined;
    }
    const t = parsed.tokens ?? {};
    if (!t.access_token || !t.refresh_token || !t.account_id) {
        lastCodexSkipReason = "incomplete_tokens";
        return undefined;
    }
    // Pull plan + expiry from the access_token JWT for telemetry.
    // CLI refresh handles actual expiry; we just log if it's below
    // the buffer so the operator can decide whether to refresh before
    // a long-running Phase 2.
    const claims = decodeJwtPayload(t.access_token);
    const expSeconds =
        claims && typeof claims["exp"] === "number"
            ? (claims["exp"] as number)
            : null;
    const planType =
        claims &&
        typeof claims["https://api.openai.com/auth"] === "object" &&
        claims["https://api.openai.com/auth"] !== null
            ? (
                  (claims["https://api.openai.com/auth"] as Record<string, unknown>)[
                      "chatgpt_plan_type"
                  ] as string | undefined
              ) ?? null
            : null;
    if (expSeconds !== null) {
        const remainingMs = expSeconds * 1000 - Date.now();
        if (remainingMs < OAUTH_MIN_REMAINING_MS_CODEX) {
            // Informational at startup. If reviewer turns out to be codex,
            // `ensureCodexAuthFreshForReviewer()` will actually trigger a
            // host-side refresh before forwarding auth.json into the
            // container. (Previously we relied on the container's codex CLI
            // to refresh via refresh_token, but the refreshed token died
            // with the container — host auth.json stayed stale, next-day
            // runs hit 401. PR #48 fixes that.)
            const remainingMin = Math.round(remainingMs / 60_000);
            const bufferMin = Math.round(OAUTH_MIN_REMAINING_MS_CODEX / 60_000);
            console.warn(
                `[yukticastle] codex access_token has ${remainingMin} min remaining ` +
                    `(buffer = ${bufferMin} min). If reviewer is codex-backed, will ` +
                    `auto-refresh on host before forwarding (set ` +
                    `YUKTICASTLE_CODEX_AUTO_REFRESH=false to disable).`,
            );
            lastCodexSkipReason = "below_buffer";
            // Don't return undefined — the refresh_token still works.
        }
    }
    return {
        rawJson: JSON.stringify(parsed),
        planType,
        expSeconds,
    };
}

// ============================================================
// PR #48 — host-side codex auth refresh before container forward
//
// Problem: codex CLI's access_token JWT in `~/.codex/auth.json` lives
// ~1 hour. The refresh_token lives ~10 days. The CLI refreshes the
// access_token whenever it runs and detects staleness, writing the new
// token back to auth.json.
//
// But when YuktiCastle forwards `CODEX_AUTH_JSON` into the reviewer
// container, that's a read-only env-var snapshot. The container's CLI
// may refresh successfully, but the new token dies with the ephemeral
// container — the host's auth.json stays stale. Next-day YC runs read
// the stale host auth.json and forward it again → 401.
//
// Fix: before forwarding, refresh ON THE HOST. The simplest way to
// force a refresh-and-writeback is to invoke `codex exec` with a no-op
// prompt; the CLI reads auth.json, sees staleness, exchanges the
// refresh_token for a fresh access_token, writes auth.json, and exits.
// We then re-read auth.json and forward the fresh snapshot.
//
// Cost: ~1 token of ChatGPT quota per stale-detection. On Team/Plus
// subscriptions this is quota-based and effectively free. Operators on
// API-key mode aren't using OAuth in the first place, so this code path
// doesn't apply.
//
// Opt out with YUKTICASTLE_CODEX_AUTO_REFRESH=false.
// ============================================================

const CODEX_AUTO_REFRESH_ENABLED =
    (legacyEnv(
        "YUKTICASTLE_CODEX_AUTO_REFRESH",
        "SANDCASTLE_CODEX_AUTO_REFRESH",
    ) ?? "true")
        .toLowerCase() !== "false";

const CODEX_REFRESH_TIMEOUT_MS =
    Number(
        legacyEnv(
            "YUKTICASTLE_CODEX_REFRESH_TIMEOUT_MS",
            "SANDCASTLE_CODEX_REFRESH_TIMEOUT_MS",
        ),
    ) || 20_000;

/**
 * Pick which model to use for the refresh ping. We don't care about
 * the output — we just need a successful `codex exec` invocation to
 * trigger the auth-refresh side-effect.
 *
 * Default: the reviewer model itself. Exception: `-codex` suffix models
 * (gpt-5-codex, etc.) are NOT available on ChatGPT OAuth subscriptions
 * (they 400 with "model is not supported when using Codex with a
 * ChatGPT account"). If the reviewer model is `-codex`, we substitute
 * a broadly-available fallback so the refresh ping itself doesn't fail.
 *
 * Operators can override with YUKTICASTLE_CODEX_REFRESH_MODEL=...
 */
function pickRefreshModel(reviewerModel: string): string {
    const override = process.env.YUKTICASTLE_CODEX_REFRESH_MODEL?.trim();
    if (override) return override;
    if (
        reviewerModel.endsWith("-codex") ||
        reviewerModel.includes("-codex-")
    ) {
        return "gpt-5.4-mini";
    }
    return reviewerModel;
}

/**
 * Shell out to `codex exec` with a no-op prompt. Returns timing +
 * success state. The actual refresh side-effect happens inside the
 * codex CLI before it issues the API call — even a network failure
 * AFTER the refresh would still leave a fresh auth.json behind. But
 * we treat non-zero exit as "didn't trust the refresh" and let the
 * caller decide whether to proceed.
 */
function forceCodexAuthRefresh(
    model: string,
    timeoutMs: number,
): { success: boolean; durationMs: number; stderr: string } {
    const startedAt = Date.now();
    const result = spawnSync("codex", ["exec", "--model", model, "ok"], {
        timeout: timeoutMs,
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
    });
    const durationMs = Date.now() - startedAt;
    if (result.error || result.status !== 0) {
        const stderr =
            (typeof result.stderr === "string" && result.stderr) ||
            result.error?.message ||
            `exit ${result.status ?? "?"}`;
        return { success: false, durationMs, stderr: stderr.slice(0, 500) };
    }
    return { success: true, durationMs, stderr: "" };
}

/**
 * If the resolved codex auth has a stale access_token (or is about to
 * be stale within the buffer), trigger a host-side refresh and re-read
 * auth.json. Returns the (possibly fresh) CodexAuthResolved.
 *
 * Records telemetry to runState.codex_refresh so the dashboard /
 * runs.jsonl reader can correlate 401 recurrence with refresh state.
 *
 * Always returns a value — falls back to `initial` if refresh fails or
 * is skipped. Never throws.
 */
function ensureCodexAuthFreshForReviewer(
    initial: CodexAuthResolved | undefined,
    reviewerModel: string,
): CodexAuthResolved | undefined {
    if (!initial) {
        runState.codex_refresh = {
            attempted: false,
            success: false,
            duration_ms: 0,
            exp_before_seconds: null,
            exp_after_seconds: null,
            model_used: null,
            error: null,
            skipped_reason: "no_initial_auth",
        };
        return initial;
    }
    if (!CODEX_AUTO_REFRESH_ENABLED) {
        runState.codex_refresh = {
            attempted: false,
            success: false,
            duration_ms: 0,
            exp_before_seconds: initial.expSeconds,
            exp_after_seconds: initial.expSeconds,
            model_used: null,
            error: null,
            skipped_reason: "disabled",
        };
        return initial;
    }
    const expSeconds = initial.expSeconds;
    if (expSeconds === null) {
        // Atypical (JWT had no `exp` claim). Don't fire blindly — we
        // have no signal that a refresh is needed.
        runState.codex_refresh = {
            attempted: false,
            success: false,
            duration_ms: 0,
            exp_before_seconds: null,
            exp_after_seconds: null,
            model_used: null,
            error: null,
            skipped_reason: "no_exp_claim",
        };
        return initial;
    }
    const remainingMs = expSeconds * 1000 - Date.now();
    if (remainingMs >= OAUTH_MIN_REMAINING_MS_CODEX) {
        runState.codex_refresh = {
            attempted: false,
            success: false,
            duration_ms: 0,
            exp_before_seconds: expSeconds,
            exp_after_seconds: expSeconds,
            model_used: null,
            error: null,
            skipped_reason: "fresh",
        };
        return initial;
    }
    const model = pickRefreshModel(reviewerModel);
    const remainingMin = Math.round(remainingMs / 60_000);
    console.log(
        `[yukticastle] codex access_token ${remainingMin} min remaining — ` +
            `refreshing on host via \`codex exec --model ${model}\` before ` +
            `forwarding to reviewer container…`,
    );
    const { success, durationMs, stderr } = forceCodexAuthRefresh(
        model,
        CODEX_REFRESH_TIMEOUT_MS,
    );
    if (!success) {
        console.warn(
            `[yukticastle] codex host refresh failed in ${durationMs}ms — ` +
                `forwarding stale auth.json anyway. Container may 401. ` +
                `stderr: ${stderr}`,
        );
        runState.codex_refresh = {
            attempted: true,
            success: false,
            duration_ms: durationMs,
            exp_before_seconds: expSeconds,
            exp_after_seconds: expSeconds,
            model_used: model,
            error: stderr || null,
        };
        return initial;
    }
    const refreshed = tryCodexOAuth();
    const expAfter = refreshed?.expSeconds ?? null;
    const expAfterIso =
        expAfter !== null ? new Date(expAfter * 1000).toISOString() : "unknown";
    console.log(
        `[yukticastle] codex host refresh succeeded in ${durationMs}ms — ` +
            `new access_token exp ${expAfterIso}`,
    );
    runState.codex_refresh = {
        attempted: true,
        success: true,
        duration_ms: durationMs,
        exp_before_seconds: expSeconds,
        exp_after_seconds: expAfter,
        model_used: model,
        error: null,
    };
    return refreshed ?? initial;
}

// OAuth resolution — two backends, same outcome:
//   1. macOS Keychain (`Claude Code-credentials` entry). The original
//      free-MAX path; auto-refreshes when the operator runs `claude`
//      anywhere on the host. macOS-only.
//   2. CLAUDE_CODE_OAUTH_TOKEN env var. The Linux / GitHub Codespace
//      path: `claude setup-token` (works on any OS) mints a long-lived
//      `sk-ant-oat01-…` token; the operator pastes it into
//      `.yukticastle/.env` as CLAUDE_CODE_OAUTH_TOKEN. The library
//      then bills against the operator's MAX quota the same way the
//      Keychain path does — no per-token API charges.
//
// Both paths yield identical downstream behavior (Bearer-token
// `ANTHROPIC_AUTH_TOKEN` forwarded into the container). Source of
// truth for the auth source is `runState.auth.anthropic`.
//
// The env-var path was empirically validated by AccessQuint's
// Codespace setup (2026-05-12); see .devcontainer/CLAUDE_AUTH_SETUP.md
// in any consumer project that ships the Codespace scaffolding.
const keychainOAuth = tryKeychainOAuth();
const envOAuth = (() => {
    const tok = process.env.CLAUDE_CODE_OAUTH_TOKEN?.trim();
    if (!tok) return undefined;
    if (!tok.startsWith("sk-ant-oat")) {
        console.warn(
            `[yukticastle] CLAUDE_CODE_OAUTH_TOKEN is set but doesn't start with "sk-ant-oat" — ignoring. Re-mint with \`claude setup-token\`.`,
        );
        return undefined;
    }
    return tok;
})();
const oauthToken = keychainOAuth ?? envOAuth;
const oauthSource = keychainOAuth
    ? "macOS Keychain (Claude Code-credentials)"
    : envOAuth
      ? "CLAUDE_CODE_OAUTH_TOKEN env"
      : null;

if (oauthToken) {
    agentEnv.ANTHROPIC_AUTH_TOKEN = oauthToken;
    runState.auth.anthropic = keychainOAuth ? "macos-keychain-oauth" : "env-oauth";
    console.log(
        `[yukticastle] auth: ${oauthSource} OAuth (MAX-billed) — token ${oauthToken.slice(0, 15)}...`,
    );
} else if (REQUIRE_OAUTH) {
    // Hard guard — used when the operator has a Business/MAX subscription
    // and wants to GUARANTEE every run draws from that quota. Refusing
    // to start beats silently API-key-billing a run the operator thought
    // was free. Two OAuth backends are checked above (Keychain + env
    // var); when both miss, this branch fires with actionable next
    // steps for each host shape.
    const envTokenPresent = !!process.env.CLAUDE_CODE_OAUTH_TOKEN?.trim();
    console.error(``);
    console.error(
        `[yukticastle] ⛔ YUKTICASTLE_REQUIRE_OAUTH=true and no OAuth source resolved.`,
    );
    console.error(
        `[yukticastle]    Refusing to fall back to ANTHROPIC_API_KEY (would bill per-token).`,
    );
    console.error(``);
    console.error(`Backends checked:`);
    console.error(
        `  - macOS Keychain (\`Claude Code-credentials\`):    ${lastKeychainSkipReason ?? "absent"}`,
    );
    console.error(
        `  - CLAUDE_CODE_OAUTH_TOKEN env var:               ${envTokenPresent ? "present but invalid (bad prefix? must start with sk-ant-oat)" : "unset"}`,
    );
    console.error(``);
    console.error(`Fix — pick the one that matches your host:`);
    console.error(``);
    console.error(`  macOS:`);
    for (const line of refreshHintLines()) console.error(`    ${line}`);
    console.error(``);
    console.error(`  Linux / GitHub Codespace:`);
    console.error(`    claude setup-token         # interactive — opens URL, paste code`);
    console.error(`    # right-click-copy the printed sk-ant-oat01-… token, then:`);
    console.error(`    echo 'CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-…' >> .yukticastle/.env`);
    console.error(``);
    console.error(
        `Or unset YUKTICASTLE_REQUIRE_OAUTH to allow per-token API-key fallback.`,
    );
    process.exit(1);
} else {
    // Failover resolution — try project-local env first (so .env or a
    // shell export can override per-project), then fall through to the
    // host-wide Keychain entry. Either path yields the same per-token
    // billing; the Keychain just removes "paste your key into every
    // checkout" toil.
    const envKey = process.env.ANTHROPIC_API_KEY;
    const keychainKey = envKey ? undefined : tryKeychainApiKey();
    const apiKey = envKey || keychainKey;

    if (apiKey) {
        agentEnv.ANTHROPIC_API_KEY = apiKey;
        runState.auth.anthropic = envKey ? "api-key" : "keychain-api-key";
        const source = envKey
            ? "ANTHROPIC_API_KEY env"
            : "macOS Keychain (yukticastle-anthropic-api-key)";
        console.log(
            `[yukticastle] auth: ${source} (per-token billed) — key ${apiKey.slice(0, 12)}...`,
        );
        // Discoverable nudge: the operator HAS Claude Code set up but the
        // token's stale. Telling them how to refresh is cheap and gets the
        // next run on the free MAX path.
        if (lastKeychainSkipReason === "expired" || lastKeychainSkipReason === "below_buffer") {
            console.log(`[yukticastle] Hint: refresh the keychain OAuth to get future runs free on MAX:`);
            for (const line of refreshHintLines()) console.log(`           ${line}`);
        }
    } else {
        console.error(
            "[yukticastle] No Anthropic auth available. Pick a path:",
        );
        console.error(``);
        console.error(`  Free (MAX-billed) — preferred:`);
        console.error(
            "    macOS host:        sign in via `claude` (Keychain auto-refresh)",
        );
        console.error(
            "    Linux/Codespace:   `claude setup-token`, then set CLAUDE_CODE_OAUTH_TOKEN in .yukticastle/.env",
        );
        console.error(``);
        console.error(`  Per-token billed (fallback):`);
        console.error(
            "    macOS host:        `npm run yukticastle:api-key:set` (Keychain-stored, scoped to YC)",
        );
        console.error(
            "    Any host:          set ANTHROPIC_API_KEY in .yukticastle/.env",
        );
        console.error(``);
        console.error(
            "  See YUKTICASTLE-GUIDE §13 for the full auth flow + troubleshooting.",
        );
        console.error(
            "  Note: if your shell has ANTHROPIC_API_KEY exported as empty, run `unset ANTHROPIC_API_KEY` first.",
        );
        process.exit(1);
    }
}

if (process.env.OPENAI_API_KEY) {
    agentEnv.OPENAI_API_KEY = process.env.OPENAI_API_KEY;
}

// ============================================================
// Codex OAuth resolution — read host's ~/.codex/auth.json (if present)
// once at startup. Forwarding to the container is deferred until the
// reviewer phase runs and we know the operator picked a codex-backed
// reviewer.
//
// SECURITY: codex auth contains a refresh_token that can mint new
// access_tokens (months-long lifetime). We forward it ONLY to the
// reviewer phase, never to the implementer. See `reviewerEnv` below.
// ============================================================

const codexAuth = tryCodexOAuth();
if (codexAuth) {
    const planLabel = codexAuth.planType ? ` (ChatGPT ${codexAuth.planType})` : "";
    console.log(
        `[yukticastle] codex OAuth: ~/.codex/auth.json resolved${planLabel} — will forward to reviewer phase when codex-backed`,
    );
}

// ============================================================
// Context pack — opt-in via `npm run agents:context`
//
// If `.yukticastle/context.md` exists (operator ran agents:context to
// pre-extract project shape: storage layer, routes, ADRs, manual
// migrations, public route inventory), pass its content as the
// `{{CONTEXT}}` substitution in the implementer + reviewer prompts.
// Cuts the agent's discovery iterations roughly in half on
// existing-project tasks.
//
// When the file is missing, `{{CONTEXT}}` substitutes to an empty
// string — operator's existing prompts that don't reference
// `{{CONTEXT}}` are unaffected. Operators who want to use it add
// a `## Project context\n\n{{CONTEXT}}\n` section to their
// implement-prompt.md (introspect.mts adds this for new projects).
//
// Loaded BEFORE docker preflight so the operator sees what's loaded
// without waiting on docker cold-start. No docker dependency here.
// ============================================================

const contextPath = resolvePath(process.cwd(), ".yukticastle/context.md");
const CONTEXT_PACK = existsSync(contextPath)
    ? readFileSync(contextPath, "utf8")
    : "";

if (CONTEXT_PACK.length > 0) {
    const sizeKb = (Buffer.byteLength(CONTEXT_PACK) / 1024).toFixed(1);
    console.log(
        `[yukticastle] context: .yukticastle/context.md loaded (${sizeKb}KB) — implementer + reviewer get {{CONTEXT}} pre-filled`,
    );
}

// ============================================================
// Learnings ledger — opt-in self-improvement
//
// Reviewer fix-ups from previous runs are captured into
// `.yukticastle/learnings.jsonl` (see captureReviewerLearnings()
// below, called after the reviewer phase). On THIS run, the
// most-recent ~10 deduplicated entries are surfaced as
// `{{RECENT_PATTERNS}}` in the implementer prompt — telling the
// agent "the reviewer has caught these things before; please don't
// make us catch them again."
//
// When the file is missing or empty (clean projects, first run, or
// reviewer never had to fix anything), `{{RECENT_PATTERNS}}`
// substitutes to an empty string. Existing operator prompts that
// don't reference it are unaffected.
//
// Roadmap: docs/ENHANCEMENTS.md item #3b (the companion to #3a
// context pack). Self-improving without retraining: every reviewer
// fix-up trains the next implementer to avoid the same mistake.
// ============================================================

interface LearningEntry {
    date: string;             // ISO date (YYYY-MM-DD), for human reads
    timestamp: number;        // ms since epoch, for sorting
    branch: string;
    commit_sha: string;
    commit_subject: string;   // first line of the commit message
    files_changed: string[];
    insertions: number;
    deletions: number;
}

const learningsPath = resolvePath(process.cwd(), ".yukticastle/learnings.jsonl");

// Capture each reviewer-phase commit as a learning entry. Reads
// `git show --format='%H%n%s%n' --shortstat` per commit on the host
// (the reviewer phase has already merged the branch back to the
// worktree, so host git can resolve the SHAs).
function captureReviewerLearnings(
    runBranch: string,
    commits: Array<{ sha: string }>,
): number {
    let captured = 0;
    const now = Date.now();
    const dateStr = new Date(now).toISOString().slice(0, 10);
    for (const c of commits) {
        try {
            // %H = full SHA, %s = subject. --shortstat gives:
            //   "<n> file[s] changed, <n> insertion[s](+), <n> deletion[s](-)"
            const out = execSync(
                `git show --format='%H%n%s%n' --shortstat --name-only ${c.sha}`,
                { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
            ).toString();
            // Split into: SHA / subject / blank / shortstat / blank / files...
            const lines = out.split("\n");
            const sha = (lines[0] ?? c.sha).trim();
            const subject = (lines[1] ?? "").trim();
            // shortstat is somewhere in the first ~6 lines; find it.
            const shortstat =
                lines.find((l) =>
                    /\d+\s+file/.test(l) &&
                    (/insertion/.test(l) || /deletion/.test(l)),
                ) ?? "";
            const insMatch = /(\d+)\s+insertion/.exec(shortstat);
            const delMatch = /(\d+)\s+deletion/.exec(shortstat);
            // Files are everything after the shortstat blank line.
            // git show --name-only emits them after the stat block.
            const files: string[] = [];
            // Walk lines after shortstat collecting non-empty entries
            // that aren't another stat-shaped line.
            let pastStat = false;
            for (const l of lines) {
                if (!pastStat) {
                    if (l === shortstat) pastStat = true;
                    continue;
                }
                const t = l.trim();
                if (!t) continue;
                if (/\d+\s+file/.test(t)) continue;
                files.push(t);
            }
            if (!subject) continue; // skip empty / malformed
            const entry: LearningEntry = {
                date: dateStr,
                timestamp: now,
                branch: runBranch,
                commit_sha: sha,
                commit_subject: subject,
                files_changed: files,
                insertions: insMatch ? Number(insMatch[1]) : 0,
                deletions: delMatch ? Number(delMatch[1]) : 0,
            };
            mkdirSync(dirname(learningsPath), { recursive: true });
            appendFileSync(learningsPath, JSON.stringify(entry) + "\n");
            captured += 1;
        } catch (err) {
            // Best-effort — don't crash the run on a learnings-write fail.
            console.warn(
                `[yukticastle] could not capture learning for ${c.sha.slice(0, 8)}: ${(err as Error).message}`,
            );
        }
    }
    return captured;
}

function loadLearnings(): LearningEntry[] {
    if (!existsSync(learningsPath)) return [];
    const out: LearningEntry[] = [];
    try {
        const raw = readFileSync(learningsPath, "utf8");
        for (const line of raw.split(/\r?\n/)) {
            const trimmed = line.trim();
            if (!trimmed) continue;
            try {
                out.push(JSON.parse(trimmed));
            } catch {
                // Skip malformed lines — don't crash the run.
            }
        }
    } catch {
        // Best-effort — if we can't read, don't surface patterns.
    }
    return out;
}

function formatRecentPatterns(entries: LearningEntry[]): string {
    if (entries.length === 0) return "";
    // Most recent first; dedupe by commit_subject (so a recurring fix
    // doesn't crowd out other patterns); cap at 10.
    const sorted = [...entries].sort((a, b) => b.timestamp - a.timestamp);
    const seen = new Set<string>();
    const top: LearningEntry[] = [];
    for (const e of sorted) {
        const key = e.commit_subject.trim().toLowerCase();
        if (seen.has(key)) continue;
        seen.add(key);
        top.push(e);
        if (top.length >= 10) break;
    }
    const lines: string[] = [];
    lines.push(
        `> The reviewer caught and corrected these patterns on previous runs.`,
        `> Avoid them in your work to skip a review iteration.`,
        ``,
    );
    for (const e of top) {
        const stat = `+${e.insertions}/-${e.deletions}`;
        const files = e.files_changed.slice(0, 3).join(", ");
        const moreFiles =
            e.files_changed.length > 3
                ? ` and ${e.files_changed.length - 3} more`
                : "";
        lines.push(
            `- **${e.commit_subject}**`,
            `  ${e.date} · files: ${files}${moreFiles} · ${stat}`,
        );
    }
    return lines.join("\n");
}

const RECENT_PATTERNS = formatRecentPatterns(loadLearnings());

if (RECENT_PATTERNS.length > 0) {
    const lineCount = RECENT_PATTERNS.split("\n").filter(
        (l) => l.startsWith("- "),
    ).length;
    console.log(
        `[yukticastle] learnings: ${lineCount} pattern(s) from previous reviewer fix-ups loaded — implementer gets {{RECENT_PATTERNS}} pre-filled`,
    );
}

// ============================================================
// Spec Critic role — first cut from ROLES.md / ENHANCEMENTS.md #15
//
// Pre-Phase-1 critique of the task spec via a free OpenRouter model
// (Llama 3.3 70b by default). Does NOT block the run — operator
// reads the critique inline and decides whether to abort + refine.
// Opt-in via YUKTICASTLE_SPEC_CRITIC=true.
//
// Recorded in runs.jsonl as `phases.spec_critic` (additive schema).
// Future roles (Security Auditor, Migration Auditor, etc.) will
// land as parallel pre-/post-phase hooks following the same shape.
// ============================================================

// Load declarative DAG config — ENHANCEMENTS.md #18. roles.json
// drives per-phase model / iterations / effort / env-gate. If the
// file is absent, falls back to a default config that exactly
// matches the hard-coded behavior we shipped before this PR.
//
// v1 scope: config-driven model selection + env-gates for the
// three existing phases (spec_critic, implementer, reviewer).
// v2 will replace the sequential phase blocks below with a
// generic executeDag() loop, enabling new role types without
// touching main.mts.
const { loadDagConfig, phaseFor, shouldRunPhase, describeConfig } =
    await import("./dag.js");
const dagConfig = loadDagConfig();
console.log(``);
console.log(describeConfig(dagConfig));
console.log(``);

const specCriticPhase = phaseFor(dagConfig, "spec_critic");
const SPEC_CRITIC_ENABLED =
    specCriticPhase !== undefined && shouldRunPhase(specCriticPhase);

// ============================================================
// PR B (refactor/dag-executor) — spec_critic now runs through the
// new dag-executor. Implementer + reviewer still live as inline
// blocks below; PRs C+D will migrate them. The executor module is
// the spine for the multi-role rollout (docs/DAG-EXECUTOR.md).
//
// Byte-identical proof-obligation for this refactor:
//   - Same console.log output (Pre-Phase header, critique body,
//     diagnostic line).
//   - Same runState.phases.spec_critic shape (iterations, commits,
//     completed, tokens, cost_usd).
// ============================================================
if (SPEC_CRITIC_ENABLED && specCriticPhase) {
    const { critiqueSpec } = await import("./spec-critic.mjs");
    const dagExecutor = await import("./dag-executor.js");

    // Handler closure — keeps console output + runState mutation
    // identical to the pre-refactor inline block. Wraps critiqueSpec.
    const runSpecCriticPhase: DagExecutor.PhaseHandler = async (
        config,
        _ctx,
    ) => {
        const startedAt = new Date().toISOString();
        const t0 = Date.now();
        console.log(``);
        console.log(`=== Pre-Phase: spec critic (${config.model}) ===`);
        console.log(``);
        const result = await critiqueSpec(TASK, { model: config.model });
        const finishedAt = new Date().toISOString();
        const durationMs = Date.now() - t0;
        if (result.error) {
            console.warn(`[spec-critic] ⚠ ${result.error}`);
            console.log(``);
            // Pre-refactor recorded `completed: false`. The executor
            // doesn't see this as a "failure" (we don't want to halt
            // the run for a spec-critic glitch) — record as success
            // with a non-empty `outputs.error` and the runState shim
            // below distinguishes by `completed`.
            return {
                kind: "success",
                report: {
                    role: config.role,
                    model: config.model,
                    startedAt,
                    finishedAt,
                    durationMs,
                    iterations: [],
                    usage: { input: 0, output: 0, cache_read: 0, cache_create: 0 },
                    costUsd: 0,
                    commits: [],
                    outputs: { completed: false, error: result.error },
                },
            };
        }
        console.log(result.critique);
        console.log(``);
        console.log(
            `[spec-critic] model=${result.model_used} tokens=${result.tokens_in}→${result.tokens_out} elapsed=${result.elapsed_ms}ms`,
        );
        console.log(``);
        return {
            kind: "success",
            report: {
                role: config.role,
                model: config.model,
                startedAt,
                finishedAt,
                durationMs,
                iterations: [
                    { tokens_in: result.tokens_in, tokens_out: result.tokens_out },
                ],
                usage: {
                    input: result.tokens_in,
                    output: result.tokens_out,
                    cache_read: 0,
                    cache_create: 0,
                },
                costUsd: result.cost_usd,
                commits: [],
                outputs: {
                    completed: true,
                    critique: result.critique,
                    model_used: result.model_used,
                    elapsed_ms: result.elapsed_ms,
                },
            },
        };
    };

    // Minimal RunContext for spec_critic — branch + context pack +
    // patterns are all that's needed. Implementer + reviewer migrations
    // will broaden this.
    const ctx: DagExecutor.RunContext = {
        branch,
        contextPack: CONTEXT_PACK,
        recentPatterns: RECENT_PATTERNS,
        hostEnv: Object.freeze({ ...process.env }),
        reports: [],
        abortReason: null,
        scratchpad: {},
    };

    const plan = dagExecutor.compileFlatToPlan(
        [specCriticPhase],
        () => true, // already gated above; the compiler doesn't re-check
    );
    const dagResult = await dagExecutor.executeDag(plan, ctx, {
        spec_critic: runSpecCriticPhase,
    });

    // Shim the new PhaseReport back into the existing
    // runState.phases.spec_critic shape so runs.jsonl format is
    // unchanged. When PRs C+D land, runState.phases will be derived
    // wholesale from dagResult.reports and this shim goes away.
    const report = dagResult.reports[0];
    if (report) {
        const completed = report.outputs.completed === true;
        runState.phases.spec_critic = {
            iterations: completed ? 1 : 0,
            commits: 0,
            completed,
            tokens: {
                input: report.usage.input,
                output: report.usage.output,
                cache_read: report.usage.cache_read,
                cache_create: report.usage.cache_create,
            },
            cost_usd: report.costUsd,
        };
    }
}

const copyToWorktree = ["node_modules"];

// The sandcastle library defaults `onSandboxReady` hooks to a 60s
// timeout. That was enough for prior YC runs on gextrader (sandbox
// setup landed 31-37s thanks to the container's warm npm cache),
// but the Phase G Dockerfile rebuild (PR #37) reset that cache —
// now `npm install --no-audit --no-fund` reliably exceeds 60s on
// gextrader (~70 direct deps, ~1100 transitive). Phase E0 + the
// 4 reasoning-agent tasks queued behind it were ALL blocked on
// HookTimeoutError.
//
// Default raised to 120s (covers cold-cache installs on most repos)
// and made operator-overridable for monorepos that legitimately
// need more headroom. See FEEDBACK-gextrader-npm-install-timeout.md.
//
// Longer-term: pre-install node_modules at image-build time so the
// in-container hook becomes a no-op. Tracked in ENHANCEMENTS.md.
const NPM_INSTALL_TIMEOUT_MS =
    Number(process.env.YUKTICASTLE_NPM_INSTALL_TIMEOUT_MS) || 120_000;

// onSandboxReady hooks fire AFTER the container is up and BEFORE the
// agent process starts. We use them for:
//   1. `npm install` (existing — installs project deps fresh inside container)
//   2. Codex auth materialization (new) — when CODEX_AUTH_JSON is set in
//      the agent env (only happens for the reviewer phase when codex
//      OAuth is wired), write it to ~/.codex/auth.json with chmod 600
//      so the container's codex CLI picks it up via its default lookup
//      path. The hook is a no-op when the env var is unset (e.g.
//      implementer phase, or codex disabled, or no host auth.json).
const hooks = {
    sandbox: {
        onSandboxReady: [
            {
                command: "npm install --no-audit --no-fund",
                timeoutMs: NPM_INSTALL_TIMEOUT_MS,
            },
            {
                // Materialize codex OAuth auth.json from env var if present.
                // Single-quoted shell: $CODEX_AUTH_JSON expands inside the
                // container at hook execution time. `printf '%s'` (not
                // `echo`) avoids backslash-escape surprises on the JSON
                // payload. The compact JSON has no real newlines so quoting
                // is safe.
                command:
                    'if [ -n "$CODEX_AUTH_JSON" ]; then ' +
                    "mkdir -p \"$HOME/.codex\" && " +
                    "printf '%s' \"$CODEX_AUTH_JSON\" > \"$HOME/.codex/auth.json\" && " +
                    "chmod 600 \"$HOME/.codex/auth.json\"; " +
                    "fi",
                timeoutMs: 5_000,
            },
        ],
    },
};
// Backward-compat for the Docker image rename (Phase B). New
// projects build `yukticastle:local`; existing projects may still
// only have `sandcastle:local` cached. Detect which one is present
// and use it; new wins if both exist.
function pickImageName(): string {
    const newName = "yukticastle:local";
    const oldName = "sandcastle:local";
    function imageExists(name: string): boolean {
        const r = spawnSync(
            "docker",
            ["image", "inspect", name, "--format", "{{.Id}}"],
            { encoding: "utf8", timeout: 5000 },
        );
        return r.status === 0;
    }
    if (imageExists(newName)) return newName;
    if (imageExists(oldName)) {
        console.warn(
            `[yukticastle] using legacy image \`${oldName}\` — please rebuild as \`${newName}\` to clear this warning:`,
        );
        console.warn(
            `              docker build -t ${newName} -f .yukticastle/Dockerfile .yukticastle`,
        );
        return oldName;
    }
    // Neither image exists; pick the new name and let preflight surface
    // a clear "image missing" error with build instructions.
    return newName;
}

const sandboxConfig = docker({ imageName: pickImageName() });

// YuktiCastle's default copyToWorktree timeout is 60s. That's too tight
// for any project with non-trivial deps — AccessQuint (574 packages)
// hit this on its first reviewer phase. Default bumped to 240s; operator
// can override via YUKTICASTLE_COPY_TIMEOUT_MS in .yukticastle/.env for
// large monorepos.
//
// (Better long-term fix: add a yukticastle .copyignore or use .dockerignore
// to exclude node_modules from worktree copy, since the image already runs
// `npm install` in its onSandboxReady hook. But that's a yukticastle feature
// request; bumping the timeout works today.)
const COPY_TIMEOUT_MS =
    Number(legacyEnv("YUKTICASTLE_COPY_TIMEOUT_MS", "SANDCASTLE_COPY_TIMEOUT_MS")) ||
    240_000;
const phaseTimeouts = { copyToWorktreeMs: COPY_TIMEOUT_MS };

// Surface the active timeouts at startup so the operator can confirm
// any override is in effect (and so the orchestrator log captures the
// config for post-mortem debugging if a HookTimeoutError fires).
console.log(
    `[yukticastle] hook timeouts: npm-install=${Math.round(NPM_INSTALL_TIMEOUT_MS / 1000)}s, copy-to-worktree=${Math.round(COPY_TIMEOUT_MS / 1000)}s`,
);

// ============================================================
// Docker daemon preflight
//
// YuktiCastle launches via dockerode → /var/run/docker.sock. If Docker
// Desktop is half-asleep (we've seen it return 500 on /containers/json
// after macOS sleep/wake) every yukticastle.run() will throw a cryptic
// dockerode error 30+ seconds in. Catch this upfront with a longer-
// running first ping (Docker Desktop's gateway can take ~120s to
// warm up after long idle) so the operator gets one clean failure
// with the remediation, not three layers of stack trace.
//
// The first attempt has a generous 130s timeout — covers Docker
// Desktop's worst-case cold-start. Subsequent attempts are tighter
// since the daemon is warm by then; if it's STILL failing, the
// daemon is genuinely wedged.
// ============================================================

function pingDocker(timeoutMs: number): { ok: boolean; reason: string } {
    const res = spawnSync("docker", ["info", "--format", "{{.ServerVersion}}"], {
        encoding: "utf8",
        timeout: timeoutMs,
    });
    if (res.status === 0 && res.stdout.trim()) {
        return { ok: true, reason: res.stdout.trim() };
    }
    if (res.error) return { ok: false, reason: res.error.message };
    return { ok: false, reason: (res.stderr || res.stdout || "no response").trim() };
}

async function preflightDocker(): Promise<void> {
    // [pause-before-attempt-ms, ping-timeout-ms]. Total worst-case
    // wall: 0+130 + 5+30 + 10+30 = ~205s. Longer than feels right,
    // but Docker Desktop's cold-start is real and the alternative
    // (failing fast and forcing the operator to manually warm Docker)
    // is worse UX.
    const attempts: Array<[number, number]> = [
        [0, 130_000],
        [5_000, 30_000],
        [10_000, 30_000],
    ];
    for (let i = 0; i < attempts.length; i++) {
        const [pause, timeout] = attempts[i]!;
        if (pause > 0) await new Promise((r) => setTimeout(r, pause));
        if (i === 0) {
            console.log(`[yukticastle] docker preflight (cold-start tolerant, may take up to 130s)...`);
        }
        const ping = pingDocker(timeout);
        if (ping.ok) {
            console.log(`[yukticastle] docker daemon ok (Server v${ping.reason})`);
            return;
        }
        console.warn(
            `[yukticastle] docker preflight ${i + 1}/${attempts.length} failed: ${ping.reason.split("\n")[0]}`,
        );
    }
    console.error(``);
    console.error(`[yukticastle] ⛔ Docker daemon is not responding after ${attempts.length} attempts.`);
    console.error(`[yukticastle]    On macOS, this usually means Docker Desktop went to sleep`);
    console.error(`[yukticastle]    or the daemon is wedged. Remediation:`);
    console.error(``);
    console.error(`      # Option A: full restart of Docker Desktop`);
    console.error(`      osascript -e 'quit app "Docker"'`);
    console.error(`      sleep 5 && open -a Docker`);
    console.error(``);
    console.error(`      # Option B: just kick the daemon`);
    console.error(`      killall -KILL docker && open -a Docker`);
    console.error(``);
    console.error(`    Then retry: npm run agents:run`);
    process.exit(1);
}

// ============================================================
// Clean-host-worktree preflight (ENHANCEMENTS.md #22)
//
// Why this exists: `@ai-hero/sandcastle` creates phase worktrees via
// `git worktree add` from the host checkout, which inherits whatever
// modified/untracked files the host already has. If the reviewer
// agent then runs `git add -A` inside its worktree, it sweeps up
// those unrelated paths into the review commit. AccessQuint's
// first end-to-end run hit this — see
// FEEDBACK-accessquint-first-agent-run.md Issue #1.
//
// Default behavior: refuse to start when the host has dirty state,
// with a clear remediation message. Operator can opt into the
// auto-stash escape hatch with YUKTICASTLE_ALLOW_DIRTY_HOST=true,
// which stashes the host's state before the run and pops it on
// exit (best-effort — a manual `git stash list` recovers if the
// pop hits a conflict).
//
// `git status --porcelain` is the canonical "is the host clean"
// check. Output is one path per line; empty = clean.
//
// ⚠ Both the porcelain check and the auto-stash MUST exclude the
// orchestrator's own runtime paths (`.yukticastle/`) and the
// sandcastle library's runtime (`.sandcastle/`). Otherwise:
//
//   1. A fresh consumer project (just ran `init-yukticastle.sh`)
//      has `.yukticastle/implement-prompt.md` + scaffold files
//      UNTRACKED. The porcelain check would flag them, and
//      `--include-untracked` stash would SWEEP THEM AWAY — Phase 1
//      then can't find its prompt file and the run dies before
//      starting. Operator surfaced this bug on 2026-05-11 after
//      PR #29 shipped Bundle A.
//   2. `.sandcastle/logs/` accumulates per-run files; without
//      excluding it, every run's pre-flight would see it as dirty.
//
// The orchestrator should never touch these paths — they're its
// own substrate. Exclusion is unconditional, not toggle-able.
//
// Pathspec syntax: `git <cmd> -- '.' ':(exclude).yukticastle' ':(exclude).sandcastle'`
// matches everything except those two trees. Verified against
// git 2.x; same syntax works for both `status` and `stash push`.
// ============================================================

const ALLOW_DIRTY_HOST =
    String(process.env.YUKTICASTLE_ALLOW_DIRTY_HOST ?? "").toLowerCase() === "true";

// Marker embedded in the stash message so we can find + pop OUR
// stash on exit without disturbing any other stashes the operator
// has parked. Timestamp keeps it unique across overlapping runs
// (e.g. a parallel `npm run agents:run` in a sibling worktree).
const STASH_MARKER = `yukticastle-autostash-${Date.now()}`;
let autoStashPushed = false;

// Pathspecs the orchestrator NEVER touches in its preflight or
// auto-stash. `.yukticastle/` = our own scaffold (prompts, logs,
// learnings, .env). `.sandcastle/` = the upstream library's runtime
// (per-agent transcripts). Both are required to be in place when
// the run starts; both accumulate ephemeral state across runs.
const HOST_STATE_PATHSPECS = [
    ".",
    ":(exclude).yukticastle",
    ":(exclude).sandcastle",
];

function gitPorcelainStatus(): string {
    try {
        const r = spawnSync(
            "git",
            ["status", "--porcelain", "--", ...HOST_STATE_PATHSPECS],
            { encoding: "utf8", timeout: 5000 },
        );
        if (r.status !== 0) return ""; // not a git repo / git error — let downstream surface it
        return (r.stdout ?? "").trim();
    } catch {
        return "";
    }
}

function preflightCleanHostWorktree(): void {
    const porcelain = gitPorcelainStatus();
    if (porcelain === "") return; // clean — proceed

    const dirtyLines = porcelain.split("\n");
    const sampleCount = Math.min(dirtyLines.length, 8);

    if (!ALLOW_DIRTY_HOST) {
        console.error(``);
        console.error(`[yukticastle] ⛔ Host checkout has uncommitted changes — refusing to start.`);
        console.error(``);
        console.error(`Why this matters: \`git worktree add\` inherits these paths into the`);
        console.error(`agent's sandbox, where a wildcard \`git add -A\` will sweep them into the`);
        console.error(`review commit. (See FEEDBACK-accessquint-first-agent-run.md Issue #1.)`);
        console.error(``);
        console.error(`Dirty paths (${dirtyLines.length} total, showing ${sampleCount}):`);
        for (let i = 0; i < sampleCount; i++) {
            console.error(`    ${dirtyLines[i]}`);
        }
        if (dirtyLines.length > sampleCount) {
            console.error(`    … and ${dirtyLines.length - sampleCount} more (run \`git status\` to see all)`);
        }
        console.error(``);
        console.error(`Remediation — pick one:`);
        console.error(``);
        console.error(`  1. Commit or stash the changes first (recommended):`);
        console.error(`        git stash push --include-untracked -m "before yukticastle run" \\`);
        console.error(`            -- . ':(exclude).yukticastle' ':(exclude).sandcastle'`);
        console.error(`        npm run agents:run -- "your task"`);
        console.error(`        git stash pop`);
        console.error(``);
        console.error(`     (The exclude pathspecs keep the orchestrator's own scaffold and the`);
        console.error(`     sandcastle library's runtime in place — both are required for the run.)`);
        console.error(``);
        console.error(`  2. Let yukticastle auto-stash + restore on exit (best-effort):`);
        console.error(`        YUKTICASTLE_ALLOW_DIRTY_HOST=true npm run agents:run -- "your task"`);
        console.error(``);
        process.exit(1);
    }

    // ALLOW_DIRTY_HOST=true path — auto-stash with a marker, restore on exit.
    console.warn(``);
    console.warn(
        `[yukticastle] ⚠  Host checkout has ${dirtyLines.length} dirty path(s); YUKTICASTLE_ALLOW_DIRTY_HOST=true → auto-stashing.`,
    );
    for (let i = 0; i < sampleCount; i++) {
        console.warn(`    ${dirtyLines[i]}`);
    }
    if (dirtyLines.length > sampleCount) {
        console.warn(`    … and ${dirtyLines.length - sampleCount} more`);
    }

    const stash = spawnSync(
        "git",
        [
            "stash", "push",
            "--include-untracked",
            "-m", STASH_MARKER,
            "--",
            ...HOST_STATE_PATHSPECS,
        ],
        { encoding: "utf8", timeout: 10_000 },
    );
    if (stash.status !== 0) {
        console.error(``);
        console.error(`[yukticastle] ⛔ git stash push failed (status ${stash.status}):`);
        console.error(stash.stderr || stash.stdout);
        console.error(``);
        console.error(`Resolve the stash conflict manually, then retry.`);
        process.exit(1);
    }
    autoStashPushed = true;
    console.warn(`[yukticastle]    stashed as: ${STASH_MARKER}`);
    console.warn(
        `[yukticastle]    will pop on exit. If anything goes wrong: \`git stash list | grep ${STASH_MARKER}\``,
    );
    console.warn(``);
}

function restoreAutoStash(): void {
    if (!autoStashPushed) return;
    try {
        // Find our specific stash by marker. The list format is
        // "stash@{N}: On <branch>: <message>" — match by full message.
        const list = spawnSync("git", ["stash", "list"], {
            encoding: "utf8",
            timeout: 5000,
        });
        if (list.status !== 0) {
            console.error(`[yukticastle] auto-stash restore: \`git stash list\` failed — your changes are still stashed. Recover with: git stash list`);
            return;
        }
        const lines = (list.stdout ?? "").split("\n");
        const match = lines.find((l) => l.includes(STASH_MARKER));
        if (!match) {
            console.error(`[yukticastle] auto-stash restore: marker ${STASH_MARKER} not found in stash list — manual recovery needed.`);
            return;
        }
        const refMatch = match.match(/^(stash@\{\d+\})/);
        if (!refMatch) {
            console.error(`[yukticastle] auto-stash restore: could not parse stash ref from: ${match}`);
            return;
        }
        const stashRef = refMatch[1]!;
        const pop = spawnSync("git", ["stash", "pop", stashRef], {
            encoding: "utf8",
            timeout: 10_000,
        });
        if (pop.status !== 0) {
            console.error(``);
            console.error(`[yukticastle] auto-stash restore: \`git stash pop ${stashRef}\` exited ${pop.status}.`);
            console.error(`Your host changes are still safely stashed. Recover with:`);
            console.error(`    git stash list      # find: ${STASH_MARKER}`);
            console.error(`    git stash pop ${stashRef}`);
            return;
        }
        console.log(`[yukticastle] auto-stash restored ✓ (${stashRef})`);
    } catch (e) {
        console.error(
            `[yukticastle] auto-stash restore threw: ${(e as Error).message}. Your changes are still in git stash.`,
        );
    }
}

// ============================================================
// Auto-commit on implementer COMPLETE (3rd-recurrence fix)
//
// LLM implementers (Opus + Sonnet both) consistently emit
// <promise>COMPLETE</promise> after acceptance criteria pass without
// calling `git commit`. The library preserves the worktree's
// uncommitted changes on disk and exposes the path via
// `RunResult.preservedWorktreePath`. We use that to auto-commit on
// the implementer's behalf so Phase 2 runs and the operator doesn't
// have to spelunk in `.sandcastle/worktrees/` to recover.
//
// Source: FEEDBACK-gextrader-impl-complete-no-autocommit.md.
//
// Safety:
//   1. We enumerate paths explicitly via `git diff --name-only HEAD`
//      + `git ls-files --others --exclude-standard` and pass them to
//      `git add -- <path>...`. NEVER `git add -A` — that would
//      re-introduce the scope-creep regression PR #33 fixed.
//   2. If any path is inside the orchestrator's own scaffold
//      (`.yukticastle/` or `.sandcastle/`), we refuse to auto-commit
//      and fall through to manual-recovery instructions. The agent
//      shouldn't be modifying those trees; if it did, the operator
//      should review before anything lands.
//   3. The commit message is generated with an "auto-commit:" prefix
//      so future git log skimming distinguishes implementer-authored
//      vs orchestrator-authored commits at a glance.
//   4. Pre-commit hooks run normally. If they reject, the spawn
//      returns non-zero and we fall through to recovery instructions
//      without leaving partial state — the worktree's index is
//      reset before bailing.
//   5. Because git worktrees share the underlying object database,
//      committing in the preserved worktree advances the branch ref
//      that the host already tracks via `branch`. No separate merge
//      step needed; the next FF-merge from the host's main hand
//      picks up the new commit.
//
// Returns the new commit SHA on success, or null on any failure
// (caller falls through to manual recovery instructions).
// ============================================================

function autoCommitImplementerWork(
    worktreePath: string,
    taskTitle: string,
    implementerModel: string,
): string | null {
    try {
        // 1. Enumerate dirty paths (tracked-but-modified + untracked).
        //    Using --name-only + ls-files (rather than parsing
        //    porcelain) sidesteps quoting/-z escaping concerns.
        const modified = spawnSync(
            "git",
            ["diff", "--name-only", "HEAD"],
            { cwd: worktreePath, encoding: "utf8", timeout: 10_000 },
        );
        const untracked = spawnSync(
            "git",
            ["ls-files", "--others", "--exclude-standard"],
            { cwd: worktreePath, encoding: "utf8", timeout: 10_000 },
        );
        if (modified.status !== 0 || untracked.status !== 0) {
            console.error(
                `[yukticastle] auto-commit: failed to enumerate worktree paths (modified.status=${modified.status}, untracked.status=${untracked.status})`,
            );
            return null;
        }
        const paths = [
            ...(modified.stdout ?? "").split("\n"),
            ...(untracked.stdout ?? "").split("\n"),
        ]
            .map((p) => p.trim())
            .filter((p) => p.length > 0);
        const uniquePaths = Array.from(new Set(paths));

        if (uniquePaths.length === 0) {
            // No actual dirty paths — library preserved the worktree
            // for some other reason. Treat as "task already done."
            return null;
        }

        // 2. Refuse to auto-commit if the implementer touched our own
        //    scaffold or the upstream sandcastle library's scaffold.
        const tainted = uniquePaths.filter(
            (p) => p.startsWith(".yukticastle/") || p.startsWith(".sandcastle/"),
        );
        if (tainted.length > 0) {
            console.error(``);
            console.error(
                `[yukticastle] auto-commit refused: implementer modified ${tainted.length} path(s) in orchestrator scaffold:`,
            );
            for (const p of tainted.slice(0, 8)) {
                console.error(`    ${p}`);
            }
            console.error(
                `[yukticastle]   These are off-limits per the implementer prompt. Review by hand before committing.`,
            );
            return null;
        }

        // 3. Stage explicit paths (NEVER -A). Pass via separate args to
        //    avoid shell quoting issues for paths with spaces.
        const add = spawnSync(
            "git",
            ["add", "--", ...uniquePaths],
            { cwd: worktreePath, encoding: "utf8", timeout: 30_000 },
        );
        if (add.status !== 0) {
            console.error(
                `[yukticastle] auto-commit: \`git add\` failed (status ${add.status}):`,
            );
            console.error(add.stderr || add.stdout);
            return null;
        }

        // 4. Commit via stdin to avoid shell escaping in the message.
        const subjectSeed = taskTitle.trim().split("\n")[0]?.slice(0, 60) ?? "implementer work";
        const message =
            `auto-commit: ${subjectSeed}\n\n` +
            `Implementer signaled <promise>COMPLETE</promise> with ${uniquePaths.length} ` +
            `uncommitted path(s) in the sandbox worktree. YuktiCastle staged + committed ` +
            `the changes so Phase 2 (reviewer) can validate. Manual review still recommended.\n\n` +
            `Paths committed (${uniquePaths.length}):\n` +
            uniquePaths.slice(0, 20).map((p) => `  - ${p}`).join("\n") +
            (uniquePaths.length > 20 ? `\n  - … and ${uniquePaths.length - 20} more` : "") +
            `\n\n` +
            `Co-Authored-By: ${implementerModel} <noreply@anthropic.com>\n`;
        const commit = spawnSync(
            "git",
            ["commit", "-F", "-"],
            {
                cwd: worktreePath,
                encoding: "utf8",
                timeout: 60_000,
                input: message,
            },
        );
        if (commit.status !== 0) {
            console.error(
                `[yukticastle] auto-commit: \`git commit\` failed (status ${commit.status}):`,
            );
            console.error(commit.stderr || commit.stdout);
            // Reset the index so the worktree isn't left in a half-staged state.
            spawnSync("git", ["reset"], { cwd: worktreePath, timeout: 5_000 });
            return null;
        }

        // 5. Capture the new commit's SHA.
        const rev = spawnSync(
            "git",
            ["rev-parse", "HEAD"],
            { cwd: worktreePath, encoding: "utf8", timeout: 5_000 },
        );
        if (rev.status !== 0) {
            console.error(
                `[yukticastle] auto-commit: \`git rev-parse HEAD\` failed (status ${rev.status})`,
            );
            return null;
        }
        return (rev.stdout ?? "").trim() || null;
    } catch (e) {
        console.error(
            `[yukticastle] auto-commit: unexpected error: ${(e as Error).message}`,
        );
        return null;
    }
}

// Register BEFORE writeRunRecord so stash-pop runs first; writing
// runs.jsonl shouldn't depend on host worktree state. process.on('exit')
// callbacks fire in registration order.
process.on("exit", () => {
    restoreAutoStash();
});

preflightCleanHostWorktree();

await preflightDocker();

// ============================================================
// Pricing table for cost estimation (USD per million tokens).
//
// "Cost" is hypothetical — when CLAUDE_AUTH_TOKEN is in use, real cost
// is absorbed by the MAX subscription. The number is still useful as
// a "had this been API key" reference for understanding usage scale.
// ============================================================

interface ModelPricing {
    input: number;             // $/M tokens
    output: number;            // $/M tokens
    cacheCreate: number;       // $/M tokens
    cacheRead: number;         // $/M tokens
}

const PRICING: Record<string, ModelPricing> = {
    "claude-opus-4-6":   { input: 15.0, output: 75.0, cacheCreate: 18.75, cacheRead: 1.50 },
    "claude-sonnet-4-6": { input:  3.0, output: 15.0, cacheCreate:  3.75, cacheRead: 0.30 },
    // Codex reviewer (GPT-5 family). OpenAI doesn't yet publish prompt-
    // caching pricing as separate line items; treat cache as input.
    "gpt-5":             { input:  1.25, output: 10.0, cacheCreate:  1.25, cacheRead: 0.125 },
    "gpt-5-codex":       { input:  1.25, output: 10.0, cacheCreate:  1.25, cacheRead: 0.125 },
};

interface UsageTotals {
    inputTokens: number;
    cacheCreationInputTokens: number;
    cacheReadInputTokens: number;
    outputTokens: number;
}

function sumUsage(iterations: yukticastle.IterationResult[]): UsageTotals {
    return iterations.reduce<UsageTotals>(
        (acc, it) => {
            const u = it.usage;
            if (!u) return acc;
            return {
                inputTokens: acc.inputTokens + u.inputTokens,
                cacheCreationInputTokens:
                    acc.cacheCreationInputTokens + u.cacheCreationInputTokens,
                cacheReadInputTokens:
                    acc.cacheReadInputTokens + u.cacheReadInputTokens,
                outputTokens: acc.outputTokens + u.outputTokens,
            };
        },
        {
            inputTokens: 0,
            cacheCreationInputTokens: 0,
            cacheReadInputTokens: 0,
            outputTokens: 0,
        },
    );
}

function estimateCost(model: string, u: UsageTotals): number {
    const p = PRICING[model];
    if (!p) return 0;
    return (
        (u.inputTokens / 1_000_000) * p.input +
        (u.outputTokens / 1_000_000) * p.output +
        (u.cacheCreationInputTokens / 1_000_000) * p.cacheCreate +
        (u.cacheReadInputTokens / 1_000_000) * p.cacheRead
    );
}

function formatTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
    return String(n);
}

function reportPhase(
    name: string,
    model: string,
    result: yukticastle.RunResult,
    onMaxOauth: boolean,
): void {
    const usage = sumUsage(result.iterations);
    const cost = estimateCost(model, usage);
    console.log(``);
    console.log(`[${name}] iterations:      ${result.iterations.length}`);
    console.log(`[${name}] commits:         ${result.commits.length}`);
    console.log(`[${name}] completion:      ${result.completionSignal ? "✓ signaled" : "(no signal — hit iteration cap or agent quit)"}`);
    console.log(`[${name}] tokens in/out:   ${formatTokens(usage.inputTokens)} / ${formatTokens(usage.outputTokens)}`);
    if (usage.cacheReadInputTokens > 0 || usage.cacheCreationInputTokens > 0) {
        console.log(`[${name}] tokens cached:   read=${formatTokens(usage.cacheReadInputTokens)} create=${formatTokens(usage.cacheCreationInputTokens)}`);
    }
    if (onMaxOauth) {
        console.log(`[${name}] est. cost:       $0 (MAX-OAuth) — would have been $${cost.toFixed(3)} on API-key`);
    } else {
        console.log(`[${name}] est. cost:       $${cost.toFixed(3)} (API-key billed)`);
    }
}


// ============================================================
// Phase 1: Implement
// ============================================================

// Phase configs from roles.json (or DEFAULT_CONFIG). Both implementer
// AND reviewer config are resolved here, BEFORE Phase 1 runs, so that
// `printFinalSummary` (which references both `IMPLEMENTER_MODEL` and
// `REVIEWER_MODEL` for cost math) can be safely called from any
// early-exit path — including the "0 commits, skip review" branch
// that previously hit a `ReferenceError: Cannot access 'REVIEWER_MODEL'
// before initialization` TDZ violation. See
// FEEDBACK-gextrader-phase-1b-chain-endpoint.md Issue #1 (CRITICAL).
//
// Falls back to documented defaults if a phase is missing from the
// config (operator may have removed it — degenerate but allowed).

// — Implementer config —
const implementerPhase = phaseFor(dagConfig, "implementer");
const IMPLEMENTER_MODEL = implementerPhase?.model ?? "claude-opus-4-6";
const IMPLEMENTER_MAX_ITER = implementerPhase?.max_iterations ?? 5;
const IMPLEMENTER_EFFORT: "low" | "medium" | "high" =
    implementerPhase?.effort ?? "high";
runState.models.implementer = IMPLEMENTER_MODEL;

// — Reviewer config (hoisted from inside the Phase 2 block) —
// REVIEWER env override lets the operator force codex (or back to
// claude) for a single run without editing roles.json — preserves
// the existing operator workflow for "audit this run with codex for
// an independent perspective."
type ReviewerKind = "claude" | "codex";
const reviewerPhase = phaseFor(dagConfig, "reviewer");
const reviewerKindFromEnv = process.env.REVIEWER as ReviewerKind | undefined;
const reviewerKind: ReviewerKind =
    reviewerKindFromEnv === "codex"
        ? "codex"
        : reviewerPhase?.provider === "openai"
          ? "codex"
          : "claude";
runState.reviewer_kind = reviewerKind;

const REVIEWER_MODEL =
    reviewerKindFromEnv === "codex"
        ? "gpt-5-codex"
        : (reviewerPhase?.model ??
          (reviewerKind === "codex" ? "gpt-5-codex" : "claude-sonnet-4-6"));
const REVIEWER_MAX_ITER = reviewerPhase?.max_iterations ?? 3;
runState.models.reviewer = REVIEWER_MODEL;

// Codex reviewer auth preflight — corrected 2026-05-15.
//
// Empirically verified (OpenAI API error message):
//   "The 'gpt-5-codex' model is not supported when using Codex with a
//    ChatGPT account."
//
// So the earlier "`-codex` models are ChatGPT-OAuth-only" claim was
// BACKWARDS. The actual model/auth matrix:
//
//   - `gpt-5`, `gpt-5-mini`, `o4-mini`, … (no `-codex` suffix):
//     Available via BOTH ChatGPT OAuth (Plus/Team/Pro/Business) AND
//     OPENAI_API_KEY (paid). OAuth is the free path on a subscription.
//   - `gpt-5-codex`, `gpt-4-codex` (with `-codex` suffix):
//     Available ONLY via OPENAI_API_KEY (paid). ChatGPT accounts —
//     including Team — are REJECTED at the API with a 400 error.
//
// What this means for the preflight:
//   - If codex OAuth resolved (~/.codex/auth.json in chatgpt mode):
//     codex CLI will use it. No OPENAI_API_KEY required at the orchestrator
//     level; the CLI picks the auth backend per-call. We can't validate
//     the model is available on this subscription tier without an API
//     round-trip, so trust the CLI and let it surface runtime errors.
//   - If codex OAuth did NOT resolve:
//     codex CLI falls back to OPENAI_API_KEY. Require it here.
//
// We removed the `-codex`-suffix-based gate entirely — it was based on
// a wrong assumption. If the operator picks `gpt-5-codex` and only has
// ChatGPT OAuth, the codex CLI will emit the 400 error above; the
// reviewer-phase catch block will surface it. To save a wasted run,
// we ALSO warn (not exit) at preflight when this combo is configured.
if (reviewerKind === "codex" && !codexAuth && !process.env.OPENAI_API_KEY) {
    console.error(``);
    console.error(
        `[yukticastle] ⛔ Reviewer is codex-backed but no OpenAI auth resolved.`,
    );
    console.error(``);
    // Surface reason-specific remediation when codex OAuth was attempted.
    // (TS narrows the module-scoped `let` to `null`; assert to widen for switch.)
    const skipReason = lastCodexSkipReason as CodexSkipReason | null;
    switch (skipReason) {
        case "missing":
            console.error(`  ~/.codex/auth.json not found and OPENAI_API_KEY is not set. Pick one:`);
            console.error(``);
            console.error(`  A. Free via ChatGPT subscription (Plus / Team / Pro / Business):`);
            console.error(`        codex login            # interactive — opens browser`);
            console.error(`     This creates ~/.codex/auth.json. Re-run YuktiCastle; main.mts`);
            console.error(`     will forward it into the reviewer container automatically.`);
            console.error(``);
            console.error(`  B. Paid via OpenAI API key:`);
            console.error(`        echo 'OPENAI_API_KEY=sk-…' >> .yukticastle/.env`);
            break;
        case "wrong_mode":
            console.error(`  ~/.codex/auth.json exists but auth_mode is NOT "chatgpt"`);
            console.error(`  (likely api-key mode). Either:`);
            console.error(`    A. Re-auth in chatgpt mode:  codex login            # interactive`);
            console.error(`    B. Set OPENAI_API_KEY in .yukticastle/.env (paid)`);
            break;
        case "incomplete_tokens":
            console.error(`  ~/.codex/auth.json is present but missing required tokens.`);
            console.error(`  Re-authenticate:  codex login`);
            break;
        case "malformed":
            console.error(`  ~/.codex/auth.json is corrupt. Delete + re-auth:`);
            console.error(`      rm ~/.codex/auth.json && codex login`);
            break;
        default:
            console.error(`  No codex auth available (reason: ${skipReason ?? "unknown"}).`);
            console.error(`  Run \`codex login\` for the free OAuth path,`);
            console.error(`  or set OPENAI_API_KEY for the paid API path.`);
    }
    console.error(``);
    console.error(`  See YUKTICASTLE-GUIDE §13 for the full auth setup.`);
    console.error(``);
    process.exit(1);
}

// Soft warning (not exit): `-codex` suffix models are paid-API-only.
// ChatGPT accounts get rejected with a 400 at the OpenAI API. If the
// operator has BOTH codex OAuth AND OPENAI_API_KEY, the codex CLI may
// route correctly — but we'd rather surface the trap upfront than have
// them spend implementer cost only to hit the 400 at Phase 2.
if (
    reviewerKind === "codex" &&
    REVIEWER_MODEL.includes("-codex") &&
    !process.env.OPENAI_API_KEY
) {
    console.warn(``);
    console.warn(
        `[yukticastle] ⚠  Reviewer model "${REVIEWER_MODEL}" has the \`-codex\` suffix.`,
    );
    console.warn(
        `[yukticastle]    OpenAI does NOT serve \`-codex\` models to ChatGPT-account auth`,
    );
    console.warn(
        `[yukticastle]    (verified empirically: "model not supported when using Codex with`,
    );
    console.warn(
        `[yukticastle]    a ChatGPT account"). These are API-key-only.`,
    );
    console.warn(``);
    console.warn(`  If you want free-via-ChatGPT review, switch to a non-codex model:`);
    console.warn(`      "model": "gpt-5"          # or gpt-5-mini, o4-mini, etc.`);
    console.warn(`  These work via ChatGPT OAuth on Plus/Team/Pro/Business plans.`);
    console.warn(``);
    console.warn(`  Continuing the run — codex CLI inside the container will surface the`);
    console.warn(`  exact error if the model isn't accessible.`);
    console.warn(``);
}

// ============================================================
// Phase 0b — Architect / Planner (PR I, ROLES.md §2.1 / §6 #5).
//
// Opt-in via YUKTICASTLE_ARCHITECT=true. Runs BEFORE the implementer
// and produces a structured plan committed as
// `.yukticastle/plans/<branch>.md`. The implementer's prompt then
// receives the plan content via {{PLAN}} substitution.
//
// First role in the rollout whose OUTPUT is consumed by the NEXT
// phase. Auditors (PRs F+H) consume implementer's output;
// spec_critic's output is only informational (printed to console,
// not threaded into a prompt). Architect's plan, in contrast, is
// part of the implementer's input contract.
//
// Failure mode is forgiving: if the architect crashes, the run
// continues to the implementer with an empty {{PLAN}} — same
// behavior as architect-disabled. Sets runState.result =
// "architect_crashed" so runs.jsonl captures the gap, but doesn't
// halt the run.
//
// Skipped if:
//   - YUKTICASTLE_ARCHITECT not set
//   - (Future PR: skipped on tasks below a size threshold — for
//     now, opt-in is the only gate.)
// ============================================================
const architectPhase = phaseFor(dagConfig, "architect");
const ARCHITECT_ENABLED =
    architectPhase !== undefined && shouldRunPhase(architectPhase);

const architectPlanPathRel = `.yukticastle/plans/${branch.replace(/\//g, "-")}.md`;

// `architectPlan` is populated when the architect ran and committed a
// plan file. The implementer's promptArgs.PLAN substitutes this
// string. When architect didn't run (or crashed), this stays empty
// and {{PLAN}} expands to "" — implementer behaves identically to
// pre-PR-I.
let architectPlan: string = "";

if (ARCHITECT_ENABLED && architectPhase) {
    const architectDagExecutor = await import("./dag-executor.js");

    runState.models.architect = architectPhase.model;

    const runArchitectPhase: DagExecutor.PhaseHandler = async (config, _ctx) => {
        const startedAt = new Date().toISOString();
        const t0 = Date.now();
        console.log(``);
        console.log(
            `=== Phase 0b: architect (${config.model}) ===`,
        );
        console.log(``);

        // Architect uses claudeCode like the implementer (same family,
        // different cognitive mode). Future PR could swap to a
        // dedicated codex or reasoning-heavy model independently.
        const architectAgent = yukticastle.claudeCode(config.model, {
            effort: config.effort ?? "high",
            env: agentEnv,
        });

        // PR #63 — same idle-completion-recovery wrapper as the
        // other agent phases. Architect's completion signal is
        // PLAN_COMPLETE (per the architect-prompt.md template).
        const planResult = await runWithIdleCompletionRecovery({
            phase: "architect",
            branch,
            runOptions: {
                name: "architect",
                agent: architectAgent,
                sandbox: sandboxConfig,
                branchStrategy: { type: "branch", branch },
                promptFile: "./.yukticastle/architect-prompt.md",
                promptArgs: {
                    TASK_DESCRIPTION: TASK,
                    BRANCH: branch,
                    CONTEXT: CONTEXT_PACK,
                    RECENT_PATTERNS,
                    PLAN_PATH: architectPlanPathRel,
                    DEFAULT_BRANCH,
                },
                maxIterations: config.max_iterations ?? 1,
                completionSignal: "<promise>PLAN_COMPLETE</promise>",
                hooks,
                copyToWorktree,
                timeouts: phaseTimeouts,
            },
        });

        reportPhase(
            "architect",
            config.model,
            planResult,
            Boolean(oauthToken),
        );

        const cost = estimateCost(
            config.model,
            sumUsage(planResult.iterations),
        );

        // Read the plan file from the branch (the architect committed
        // it as a single file-add commit). Best-effort — handler
        // succeeds even if the plan file is missing, but runs.jsonl
        // records the gap.
        const planRead = spawnSync(
            "git",
            ["show", `${branch}:${architectPlanPathRel}`],
            { encoding: "utf8", timeout: 10_000 },
        );
        let planSize: number | null = null;
        let planExcerpt: string | null = null;
        let planContent: string = "";
        if (planRead.status === 0 && planRead.stdout) {
            planContent = planRead.stdout;
            planSize = planContent.length;
            // Cap excerpt at ~2KB — runs.jsonl shouldn't balloon.
            // Full plan is in the branch via `git show <plan_path>`.
            planExcerpt = planContent.slice(0, 2048);
        } else {
            console.warn(
                `[yukticastle] architect signaled PLAN_COMPLETE but ${architectPlanPathRel} is missing from the branch. Implementer will run without a plan.`,
            );
        }

        const baseRecord = recordPhase(
            "architect",
            planResult,
            cost,
        );
        runState.phases.architect = {
            ...baseRecord,
            plan_path: planSize !== null ? architectPlanPathRel : null,
            plan_size_bytes: planSize,
            plan_excerpt: planExcerpt,
        };

        // Stash the plan content for the implementer's promptArgs.
        // Uses the closure to mutate `architectPlan` declared in the
        // enclosing scope — handler returns void-like, side-effecting
        // is fine for orchestrator-internal state.
        if (planContent) {
            architectPlan = planContent;
        }

        const finishedAt = new Date().toISOString();
        const usage = sumUsage(planResult.iterations);
        return {
            kind: "success",
            report: {
                role: config.role,
                model: config.model,
                startedAt,
                finishedAt,
                durationMs: Date.now() - t0,
                iterations: planResult.iterations.map((it) => ({
                    tokens_in: it.usage?.inputTokens ?? 0,
                    tokens_out: it.usage?.outputTokens ?? 0,
                    cache_read: it.usage?.cacheReadInputTokens ?? 0,
                    cache_create:
                        it.usage?.cacheCreationInputTokens ?? 0,
                })),
                usage: {
                    input: usage.inputTokens,
                    output: usage.outputTokens,
                    cache_read: usage.cacheReadInputTokens,
                    cache_create: usage.cacheCreationInputTokens,
                },
                costUsd: cost,
                commits: planResult.commits.map((c) => c.sha),
                outputs: {
                    plan_path: planSize !== null ? architectPlanPathRel : null,
                    plan_size_bytes: planSize,
                },
            },
        };
    };

    const architectCtx: DagExecutor.RunContext = {
        branch,
        contextPack: CONTEXT_PACK,
        recentPatterns: RECENT_PATTERNS,
        hostEnv: Object.freeze({ ...process.env }),
        reports: [],
        abortReason: null,
        scratchpad: {},
    };

    try {
        const architectDagResult = await architectDagExecutor.executeDag(
            architectDagExecutor.compileFlatToPlan(
                [architectPhase],
                () => true,
            ),
            architectCtx,
            { architect: runArchitectPhase },
        );

        if (architectDagResult.outcome.kind === "failed") {
            // Handler threw — don't halt the run; implementer can
            // still proceed without a plan. Record the gap.
            runState.result = "architect_crashed";
            runState.error =
                architectDagResult.outcome.reason?.message ??
                "architect crashed without a message";
            console.warn(``);
            console.warn(
                `[yukticastle] ⚠️  Architect crashed: ${runState.error}`,
            );
            console.warn(
                `[yukticastle]    Implementer will run without a plan (continues degraded).`,
            );
            console.warn(``);
        } else {
            const rec = runState.phases.architect;
            if (rec?.plan_path) {
                console.log(
                    `[yukticastle] ✓ architect: ${rec.plan_size_bytes ?? "?"} bytes → git show ${branch}:${rec.plan_path}`,
                );
            }
        }
    } catch (architectErr) {
        // Belt-and-suspenders: executor catches handler throws, but
        // if something earlier crashes (rare) we still don't want
        // to take down the run.
        const msg = (architectErr as Error).message ?? String(architectErr);
        runState.result = "architect_crashed";
        runState.error = msg;
        console.warn(`[yukticastle] ⚠️  Architect phase setup crashed: ${msg}`);
    }
}

// ============================================================
// PR C (refactor/dag-executor-implementer) — implementer phase
// now runs through the executor introduced in PR #51 / PR B.
//
// The handler wraps:
//   - The "=== Phase 1: implementer ===" header
//   - The `yukticastle.run({...})` invocation
//   - reportPhase() for operator-visible status output
//   - Cost estimation + recordPhase() mutation of
//     runState.phases.implementer
//
// What stays in main.mts (below this block):
//   - Silent-failure detection (needs process.exit semantics)
//   - Auto-commit recovery (needs branch + slug + scaffold paths)
//   - no-commits early-exit (needs printFinalSummary)
//   - Proactive Anthropic OAuth refresh between phases
//   - Reviewer phase (still inline — migrates in PR D)
//
// The handler stashes the RunResult into PhaseReport.outputs.run_result
// so post-phase code can read it unchanged. That keeps the surface
// area minimal — every downstream reference to `implement` works as
// before.
//
// Byte-identical proof obligation:
//   - Same console output (header + reportPhase lines)
//   - Same runState.phases.implementer shape (recordPhase mutates
//     it inside the handler, identical to pre-refactor)
//   - Same RunResult exposed to post-phase logic (just re-bound
//     from outputs)
// ============================================================
const implementerDagExecutor = await import("./dag-executor.js");

const runImplementerPhase: DagExecutor.PhaseHandler = async (config, _ctx) => {
    const startedAt = new Date().toISOString();
    const t0 = Date.now();
    console.log(`=== Phase 1: implementer (${config.model}) ===\n`);
    // PR #63 — wrap run() with idle-completion-recovery to work around
    // the sandcastle idle-timeout-post-COMPLETE bug (accessquint #108).
    const implementResult = await runWithIdleCompletionRecovery({
        phase: "implementer",
        branch,
        runOptions: {
            name: "implementer",
            agent: yukticastle.claudeCode(config.model, {
                effort: IMPLEMENTER_EFFORT,
                env: agentEnv,
            }),
            sandbox: sandboxConfig,
            branchStrategy: { type: "branch", branch },
            promptFile: "./.yukticastle/implement-prompt.md",
            promptArgs: {
                TASK_DESCRIPTION: TASK,
                CONTEXT: CONTEXT_PACK,
                RECENT_PATTERNS,
                // PR I — Architect plan, when the architect phase ran
                // + committed a plan file. Empty string when architect
                // was gated off or crashed. The implementer prompt
                // template wraps {{PLAN}} in a conditional section
                // (no-op when empty) so existing operator prompts that
                // haven't been regenerated still work.
                PLAN: architectPlan,
            },
            maxIterations: config.max_iterations ?? IMPLEMENTER_MAX_ITER,
            completionSignal: "<promise>COMPLETE</promise>",
            hooks,
            copyToWorktree,
            timeouts: phaseTimeouts,
        },
    });
    reportPhase(
        "implementer",
        config.model,
        implementResult,
        Boolean(oauthToken),
    );
    const cost = estimateCost(config.model, sumUsage(implementResult.iterations));
    runState.phases.implementer = recordPhase(
        "implementer",
        implementResult,
        cost,
    );
    const finishedAt = new Date().toISOString();
    const usage = sumUsage(implementResult.iterations);
    return {
        kind: "success",
        report: {
            role: config.role,
            model: config.model,
            startedAt,
            finishedAt,
            durationMs: Date.now() - t0,
            iterations: implementResult.iterations.map((it) => ({
                tokens_in: it.usage?.inputTokens ?? 0,
                tokens_out: it.usage?.outputTokens ?? 0,
                cache_read: it.usage?.cacheReadInputTokens ?? 0,
                cache_create: it.usage?.cacheCreationInputTokens ?? 0,
            })),
            // Map sandcastle's camelCase UsageTotals → executor's
            // snake_case AggregatedUsage. The two schemas exist for
            // separate audiences (sandcastle library vs runs.jsonl
            // dashboard consumers); ENHANCEMENTS.md #4 will unify.
            usage: {
                input: usage.inputTokens,
                output: usage.outputTokens,
                cache_read: usage.cacheReadInputTokens,
                cache_create: usage.cacheCreationInputTokens,
            },
            costUsd: cost,
            // sandcastle's RunResult.commits is { sha: string }[]; the
            // executor's PhaseReport.commits is a flat SHA list.
            commits: implementResult.commits.map((c) => c.sha),
            // Hand the RunResult through to main.mts's post-phase
            // logic (silent-failure detection, auto-commit, etc.).
            // PR D will move that logic into handlers; for now we
            // ride through outputs.
            outputs: { run_result: implementResult },
        },
    };
};

const implementerCtx: DagExecutor.RunContext = {
    branch,
    contextPack: CONTEXT_PACK,
    recentPatterns: RECENT_PATTERNS,
    hostEnv: Object.freeze({ ...process.env }),
    reports: [],
    abortReason: null,
    scratchpad: {},
};

// Build the synthetic PhaseConfig for the implementer. Today
// roles.json carries this; once we plumb dagConfig through here
// we'll read it directly. For PR C we synthesize from the module
// constants so behavior is unchanged.
const implementerPhaseConfig = phaseFor(dagConfig, "implementer") ?? {
    role: "implementer",
    when: "always" as const,
    provider: "anthropic" as const,
    model: IMPLEMENTER_MODEL,
    max_iterations: IMPLEMENTER_MAX_ITER,
};

const implementerDagResult = await implementerDagExecutor.executeDag(
    implementerDagExecutor.compileFlatToPlan(
        [implementerPhaseConfig],
        () => true,
    ),
    implementerCtx,
    { implementer: runImplementerPhase },
);

// Unwrap the RunResult so downstream code (silent-failure detection,
// auto-commit, reviewer phase) reads `implement` the same way it
// did pre-refactor. If the handler threw, the executor synthesized
// a failure report with no run_result — surface that as a hard
// orchestrator error since downstream logic assumes `implement`
// exists.
const implementerReport = implementerDagResult.reports[0];
const implementerRunResult = implementerReport?.outputs.run_result as
    | yukticastle.RunResult
    | undefined;
if (!implementerRunResult) {
    console.error(``);
    console.error(
        `[yukticastle] ⛔ Implementer phase failed to produce a RunResult.`,
    );
    if (
        implementerDagResult.outcome.kind === "failed" &&
        implementerDagResult.outcome.reason
    ) {
        console.error(
            `[yukticastle]    cause: ${implementerDagResult.outcome.reason.message}`,
        );
    }
    console.error(``);
    process.exit(1);
}
const implement: yukticastle.RunResult = implementerRunResult;

// Silent-failure detection: agent ran iterations but neither committed
// nor signaled completion. Most common cause: claude CLI exits 0 with
// no output (auth misconfigured, prompt unsendable, etc.).
const implementerSilentFailure =
    implement.iterations.length > 0 &&
    implement.commits.length === 0 &&
    implement.completionSignal === undefined;

if (implementerSilentFailure) {
    runState.result = "implementer_silent_failure";
    console.error(``);
    console.error(`[yukticastle] ⚠️  Implementer ran ${implement.iterations.length} iteration(s) but produced no commits and no completion signal.`);
    console.error(`[yukticastle]    Most likely causes:`);
    console.error(`[yukticastle]      - Auth: CLAUDE_AUTH_TOKEN expired (1hr OAuth lifetime), ANTHROPIC_API_KEY invalid, or both unset`);
    console.error(`[yukticastle]      - Prompt: implement-prompt.md is empty or has unsubstituted {{TOKEN}} placeholders`);
    console.error(`[yukticastle]      - Container: claude CLI not actually working — see .yukticastle/logs/`);
    console.error(`[yukticastle]    Diagnostics:`);
    console.error(`[yukticastle]      tail .yukticastle/logs/agent-${slug}-${stamp}-implementer.log`);
    console.error(`[yukticastle]      docker run --rm --user 501:20 -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \\`);
    console.error(`[yukticastle]        --entrypoint sh yukticastle:local -c \\`);
    console.error(`[yukticastle]        'claude --print --debug "Say pong"'`);
    process.exit(2);
}

// SHA of the auto-commit we made on the implementer's behalf, if any.
// Module-scoped so printFinalSummary can include it in the SHA list
// without needing to thread an extra parameter through.
let autoCommitSha: string | null = null;

if (implement.commits.length === 0) {
    // No library-reported commits but completion was signaled. Two
    // scenarios:
    //   (a) "Task already done" — earlier run on the same branch
    //       satisfied it. No work in the sandbox worktree.
    //   (b) "Implementer wrote code but forgot to git-commit." LLMs
    //       (Opus and Sonnet both) consistently treat acceptance
    //       criteria passing as "done" and forget to commit. Third
    //       recurrence pattern across gextrader runs — see
    //       FEEDBACK-gextrader-impl-complete-no-autocommit.md.
    //
    // For (b), the sandcastle library preserves the worktree on disk
    // and exposes its path on the RunResult as `preservedWorktreePath`
    // (verified against @ai-hero/sandcastle@0.5.8 run.d.ts line 173).
    // We use that to attempt an auto-commit so Phase 2 (reviewer)
    // actually runs. If auto-commit fails (pre-commit hook rejects,
    // worktree has touched the orchestrator's own scaffold, etc.) we
    // fall through to the old recovery-instructions path below.
    const preservedPath = (implement as { preservedWorktreePath?: string })
        .preservedWorktreePath;
    if (preservedPath && existsSync(preservedPath)) {
        autoCommitSha = autoCommitImplementerWork(
            preservedPath,
            taskInput.branchSlugSource.split("\n")[0]?.replace(/^#+\s*/, "") ?? slug,
            IMPLEMENTER_MODEL,
        );
        if (autoCommitSha) {
            console.log(``);
            console.log(
                `[yukticastle] ✓ implementer signaled COMPLETE with uncommitted work — auto-committed on its behalf.`,
            );
            console.log(
                `[yukticastle]    Commit:  ${autoCommitSha.slice(0, 12)}  (full SHA on branch ${branch})`,
            );
            console.log(
                `[yukticastle]    Phase 2 (reviewer) will now run — independent eyes on the auto-committed diff.`,
            );
            runState.auto_commit_sha = autoCommitSha;
            // Don't early-exit; fall through to Phase 2.
        }
    }
}

if (implement.commits.length === 0 && autoCommitSha === null) {
    // Either the task was genuinely already-done (clean worktree, no
    // preserved path), OR auto-commit attempted and failed. Print the
    // recovery instructions (still useful for failures — operator sees
    // exactly which dir to inspect + how to recover by hand).
    console.log(``);
    console.log(`[yukticastle] Implementer signaled COMPLETE without making commits.`);
    console.log(``);
    console.log(`[yukticastle] If the task was already satisfied by an earlier run`);
    console.log(`[yukticastle] on the same branch, this is expected. Skipping review.`);
    console.log(``);
    console.log(`[yukticastle] ⚠️  If the implementer wrote NEW code but forgot to commit`);
    console.log(`[yukticastle]    AND auto-commit didn't recover (e.g. pre-commit hook rejected,`);
    console.log(`[yukticastle]    or scaffold-path safety check tripped), recover by hand:`);
    console.log(``);
    console.log(`      ls .sandcastle/worktrees/           # find the agent-${slug}-* dir`);
    console.log(`      cd .sandcastle/worktrees/agent-${slug}-${stamp}`);
    console.log(`      git status                          # confirm uncommitted work`);
    console.log(`      git add <paths>                     # stage EXPLICITLY — no \`git add -A\``);
    console.log(`      git commit -m "your message"`);
    console.log(`      cd "${"$"}{OLDPWD}"`);
    console.log(`      git merge --ff-only ${branch}`);
    console.log(``);
    runState.result = "no_commits";
    printFinalSummary(implement, null);
    process.exit(0);
}

// ============================================================
// Proactive between-phase OAuth refresh — ENHANCEMENTS.md #13
//
// Anthropic OAuth tokens last ~6h, but a long Phase 1 plus an
// initially-low-buffer start can land Phase 2 close to expiry. With
// `YUKTICASTLE_PROACTIVE_REFRESH=true`, between phases we peek at
// the keychain's remaining time; if it's now below the buffer, we
// invoke `.yukticastle/claude-login.sh` to refresh the token and
// re-forward the new value as ANTHROPIC_AUTH_TOKEN for Phase 2.
//
// Default OFF — for Anthropic-only setups the 60-min buffer is
// usually enough headroom. The mechanism becomes critical when codex
// OAuth (1h tokens) lands. Same code path will then check OpenAI's
// `~/.codex/auth.json` between phases.
//
// Track in runs.jsonl:
//   auth.proactive_refresh_fired: boolean | null
//     null  → feature off (default)
//     false → feature on, refresh not needed (token still healthy)
//     true  → feature on, refresh fired between phases
// ============================================================

const PROACTIVE_REFRESH =
    String(
        legacyEnv(
            "YUKTICASTLE_PROACTIVE_REFRESH",
            "SANDCASTLE_PROACTIVE_REFRESH",
        ) ?? "",
    ).toLowerCase() === "true";

if (PROACTIVE_REFRESH && oauthToken) {
    runState.auth.proactive_refresh_fired = false;
    const remainingMs = peekKeychainRemainingMs();
    if (
        remainingMs !== undefined &&
        remainingMs < OAUTH_MIN_REMAINING_MS
    ) {
        console.log(
            `[yukticastle] proactive refresh: keychain token has ${Math.round(remainingMs / 60000)} min remaining (buffer ${Math.round(OAUTH_MIN_REMAINING_MS / 60000)} min) — refreshing before Phase 2`,
        );
        const loginScript = resolvePath(
            process.cwd(),
            ".yukticastle/claude-login.sh",
        );
        if (existsSync(loginScript)) {
            const r = spawnSync("bash", [loginScript], {
                stdio: "inherit",
                timeout: 30_000,
            });
            if (r.status === 0) {
                const newToken = tryKeychainOAuth();
                if (newToken) {
                    agentEnv.ANTHROPIC_AUTH_TOKEN = newToken;
                    runState.auth.proactive_refresh_fired = true;
                    console.log(
                        `[yukticastle] proactive refresh: ✓ Phase 2 will use refreshed token`,
                    );
                } else {
                    console.warn(
                        `[yukticastle] proactive refresh: helper exited 0 but new token is still below buffer — Phase 2 falls through to API key if available`,
                    );
                    delete agentEnv.ANTHROPIC_AUTH_TOKEN;
                    const envKey = process.env.ANTHROPIC_API_KEY;
                    const fallbackKey = envKey || tryKeychainApiKey();
                    if (fallbackKey) {
                        agentEnv.ANTHROPIC_API_KEY = fallbackKey;
                        runState.auth.anthropic = envKey ? "api-key" : "keychain-api-key";
                    }
                }
            } else {
                console.warn(
                    `[yukticastle] proactive refresh: claude-login.sh exited ${r.status} — Phase 2 will use the existing (about-to-expire) token`,
                );
            }
        } else {
            console.warn(
                `[yukticastle] proactive refresh: ${loginScript.replace(process.cwd() + "/", "")} not found — skipping`,
            );
        }
    }
}

// ============================================================
// Phase 2: Review
//
// REVIEWER env switches the perspective:
//   REVIEWER=claude  (default) — Sonnet, same family as implementer.
//                                fastest path to "agree with itself" but
//                                catches obvious issues + style fixes.
//   REVIEWER=codex             — GPT-5 via Codex CLI. Independent
//                                family → catches things Claude
//                                rationalized away. Costs OpenAI tokens.
// ============================================================

// Reviewer config (REVIEWER_MODEL, reviewerKind, REVIEWER_MAX_ITER,
// OPENAI_API_KEY check) was hoisted to module scope alongside the
// implementer config — see the Phase 1 setup block. Hoisting fixes
// the TDZ crash that previously kicked in when the "0 commits, skip
// review" early-exit reached `printFinalSummary` before this block
// declared `REVIEWER_MODEL`.

// ============================================================
// PR D (refactor/dag-executor-reviewer) — reviewer phase now runs
// through executeDag(). Companion to PR #51 (executor module +
// spec_critic) and PR #54 (implementer re-apply).
//
// Pattern matches PR #54's implementer handler:
//   - Header (`=== Phase 2: reviewer ===`) moves inside the handler
//   - yukticastle.run({...}) + reportPhase + cost + recordPhase
//     move inside the handler
//   - RunResult stashed into PhaseReport.outputs.run_result so the
//     post-phase logic (captureReviewerLearnings, silent-failure
//     detection, PR #53 deletion guard) reads it unchanged
//
// What stays outside the handler:
//   - The try/catch wrapper (Phase 2 crash recovery — operator
//     needs FF-merge instructions on failure)
//   - Codex auth setup (refresh + forwarding env composition) —
//     this is reviewer-config-specific orchestrator concern,
//     not handler-internal
//   - captureReviewerLearnings (uses commits from RunResult)
//   - reviewer_silent_failure detection
//   - PR #53 deletion guard (the critical safety check that
//     diffs vs origin/main — runs AFTER reviewer reports success)
//
// Byte-identical proof obligation:
//   - Same console output (header + reportPhase, OPENAI_API_KEY
//     strip log line, same `config.model` resolution)
//   - Same runState.phases.reviewer shape (recordPhase mutates
//     it inside the handler, identical to pre-refactor)
//   - Same `review: yukticastle.RunResult | null` exposed to
//     downstream — just re-bound from outputs.run_result
//   - Crash recovery preserved: handler-throw becomes a failure
//     PhaseResult; we re-throw the captured error so the existing
//     catch block fires identically
// ============================================================

// Header printed BEFORE the try block so it sequences identically
// to pre-refactor (header → forwarding log → run output). Moving
// it inside the handler would have reversed the order against the
// codex OAuth forwarding log. Byte-identical proof obligation wins
// over pattern consistency with PR #52's implementer handler.
console.log(`\n=== Phase 2: reviewer (${reviewerKind} → ${REVIEWER_MODEL}) ===\n`);

// Phase 2 is wrapped in try/catch/finally so a crash here (Docker
// blip, network glitch, library exception) does NOT prevent the
// final summary from running. Without this, the operator gets a
// raw stack trace and no recap of which commits the implementer
// produced, no FF-merge command, no recovery instructions. See
// FEEDBACK-gextrader-phase-1b-chain-endpoint.md Issue #1 — the
// belt-and-suspenders ask from the operator.
let review: yukticastle.RunResult | null = null;
try {
    // Phase-scoped env: implementer always gets `agentEnv` (no codex
    // secrets). Reviewer gets `agentEnv` plus codex auth.json content
    // when codex is the reviewer AND we resolved host OAuth. Limits
    // refresh_token exposure to the single phase that needs it.
    //
    // PR #48 — refresh host auth.json BEFORE forwarding into the
    // container. The container can't write refreshed tokens back to
    // the host, so without this, every overnight stale access_token
    // would re-poison the next morning's run with a 401. Only fires
    // when codex is actually going to be the reviewer (gated by
    // `reviewerKind === "codex"`), so claude-reviewer runs don't burn
    // ChatGPT quota for nothing.
    //
    // Stays OUTSIDE the handler because (a) codex auth is
    // orchestrator-level concern, not handler-internal, and
    // (b) main.mts already does the freshness check before we
    // build the agent; moving into the handler would couple the
    // handler to codex-CLI specifics.
    const freshCodexAuth =
        reviewerKind === "codex"
            ? ensureCodexAuthFreshForReviewer(codexAuth, REVIEWER_MODEL)
            : codexAuth;
    const reviewerEnv: Record<string, string> = { ...agentEnv };
    if (reviewerKind === "codex" && freshCodexAuth) {
        reviewerEnv.CODEX_AUTH_JSON = freshCodexAuth.rawJson;
        // CRITICAL: strip OPENAI_API_KEY when codex OAuth is being forwarded.
        // codex CLI v0.130 (and likely all 0.13x) PREFERS OPENAI_API_KEY env
        // over ~/.codex/auth.json — so a dummy value in .yukticastle/.env
        // (operators added it earlier to satisfy older preflight checks
        // that required OPENAI_API_KEY for any codex reviewer) poisons the
        // container's auth resolution: codex sends the dummy to OpenAI's
        // wss://api.openai.com/v1/responses endpoint and gets 401-rejected.
        //
        // Diagnosed 2026-05-15 from accessquint reviewer log:
        //   codex_api::endpoint::responses_websocket: failed to connect to
        //   websocket: HTTP error: 401 Unauthorized, url: wss://api.openai.com/v1/responses
        //
        // Even when the operator has a REAL OPENAI_API_KEY for non-codex
        // reviewers, we shouldn't forward it to the codex-OAuth path —
        // mixing the two auth methods at the codex CLI is unsupported and
        // bills the wrong account in the best case, 401s in the worst.
        delete reviewerEnv.OPENAI_API_KEY;
        const planLabel = freshCodexAuth.planType ? ` (ChatGPT ${freshCodexAuth.planType})` : "";
        console.log(
            `[yukticastle] forwarding codex OAuth to reviewer container${planLabel} (OPENAI_API_KEY stripped so codex CLI uses auth.json)`,
        );
    }

    // ───── Reviewer handler ─────
    const reviewerDagExecutor = await import("./dag-executor.js");

    const runReviewerPhase: DagExecutor.PhaseHandler = async (config, _ctx) => {
        const startedAt = new Date().toISOString();
        const t0 = Date.now();
        // NOTE: Phase 2 header is printed by main.mts BEFORE this handler
        // (intentional — preserves console output order vs the codex OAuth
        // forwarding log line that fires between header and run). Don't
        // re-print it here.
        const reviewerAgent =
            reviewerKind === "codex"
                ? yukticastle.codex(config.model, { env: reviewerEnv })
                : yukticastle.claudeCode(config.model, { env: reviewerEnv });
        // PR #63 — same idle-completion-recovery wrapper as the implementer.
        const reviewResult = await runWithIdleCompletionRecovery({
            phase: "reviewer",
            branch,
            runOptions: {
                name: "reviewer",
                agent: reviewerAgent,
                sandbox: sandboxConfig,
                branchStrategy: { type: "branch", branch },
                promptFile: "./.yukticastle/review-prompt.md",
                promptArgs: {
                    TASK_DESCRIPTION: TASK,
                    BRANCH: branch,
                    CONTEXT: CONTEXT_PACK,
                    RECENT_PATTERNS,
                },
                maxIterations: config.max_iterations ?? REVIEWER_MAX_ITER,
                completionSignal: "<promise>COMPLETE</promise>",
                hooks,
                copyToWorktree,
                timeouts: phaseTimeouts,
            },
        });
        reportPhase("reviewer", config.model, reviewResult, Boolean(oauthToken));
        const cost = estimateCost(config.model, sumUsage(reviewResult.iterations));
        runState.phases.reviewer = recordPhase("reviewer", reviewResult, cost);
        const finishedAt = new Date().toISOString();
        const usage = sumUsage(reviewResult.iterations);
        return {
            kind: "success",
            report: {
                role: config.role,
                model: config.model,
                startedAt,
                finishedAt,
                durationMs: Date.now() - t0,
                iterations: reviewResult.iterations.map((it) => ({
                    tokens_in: it.usage?.inputTokens ?? 0,
                    tokens_out: it.usage?.outputTokens ?? 0,
                    cache_read: it.usage?.cacheReadInputTokens ?? 0,
                    cache_create: it.usage?.cacheCreationInputTokens ?? 0,
                })),
                usage: {
                    input: usage.inputTokens,
                    output: usage.outputTokens,
                    cache_read: usage.cacheReadInputTokens,
                    cache_create: usage.cacheCreationInputTokens,
                },
                costUsd: cost,
                commits: reviewResult.commits.map((c) => c.sha),
                // Hand the RunResult through so the post-phase logic
                // (captureReviewerLearnings, silent-failure detection,
                // deletion guard) reads it unchanged. Matches PR #54's
                // implementer pattern.
                outputs: { run_result: reviewResult },
            },
        };
    };

    const reviewerCtx: DagExecutor.RunContext = {
        branch,
        contextPack: CONTEXT_PACK,
        recentPatterns: RECENT_PATTERNS,
        hostEnv: Object.freeze({ ...process.env }),
        reports: [],
        abortReason: null,
        scratchpad: {},
    };

    // Build synthetic PhaseConfig matching the runtime resolution.
    // Today roles.json carries this; the synthesizer here preserves
    // the REVIEWER env-override behavior (codex / claude) that the
    // module-scoped reviewerKind already resolved.
    const reviewerProvider: "openai" | "anthropic" =
        reviewerKind === "codex" ? "openai" : "anthropic";
    const reviewerPhaseConfig = phaseFor(dagConfig, "reviewer") ?? {
        role: "reviewer",
        when: "always" as const,
        provider: reviewerProvider,
        model: REVIEWER_MODEL,
        max_iterations: REVIEWER_MAX_ITER,
    };

    // ============================================================
    // PR G — security_auditor co-runs in a parallel group with the
    // reviewer when YUKTICASTLE_SECURITY_AUDITOR=true. Composes the
    // two phases into one parallel group via runParallelGroup from
    // PR #56. Eliminates the ~2-5min wall-time penalty PR #57
    // introduced when the auditor was sequential after reviewer.
    //
    // The auditor's pre-handler env composition (codex auth refresh
    // + forwarding) is hoisted up here from the old Phase 3 block.
    // The auditor's handler is defined inline so it shares the
    // try/catch wrapper with reviewer — same crash-recovery path,
    // same try-tail fall-through to printFinalSummary.
    //
    // When the auditor is gated off, we skip all of this and run
    // the existing sequential single-phase reviewer group — that
    // path is byte-identical to pre-PR-G behavior.
    // ============================================================
    const securityAuditorPhase = phaseFor(dagConfig, "security_auditor");
    const SECURITY_AUDITOR_ENABLED =
        securityAuditorPhase !== undefined &&
        shouldRunPhase(securityAuditorPhase);

    const findingsPathRel = `.yukticastle/security-findings/${branch.replace(/\//g, "-")}.md`;
    let runSecurityAuditorPhase: DagExecutor.PhaseHandler | null = null;

    if (SECURITY_AUDITOR_ENABLED && securityAuditorPhase) {
        const auditorPromptPath = ".yukticastle/security-auditor-prompt.md";
        // Auditor codex auth: independent refresh from reviewer's.
        // When reviewer was claude (no codexAuth touched yet), this
        // is the first refresh — ensures auth.json is fresh before
        // the auditor's container forward.
        const auditorFreshCodexAuth = ensureCodexAuthFreshForReviewer(
            codexAuth,
            securityAuditorPhase.model,
        );
        const auditorEnv: Record<string, string> = { ...agentEnv };
        if (auditorFreshCodexAuth) {
            auditorEnv.CODEX_AUTH_JSON = auditorFreshCodexAuth.rawJson;
            // PR #46 reasoning applies — codex CLI prefers env
            // OPENAI_API_KEY over auth.json. Strip it for OAuth path.
            delete auditorEnv.OPENAI_API_KEY;
            const planLabel = auditorFreshCodexAuth.planType
                ? ` (ChatGPT ${auditorFreshCodexAuth.planType})`
                : "";
            console.log(
                `[yukticastle] forwarding codex OAuth to security_auditor container${planLabel} (OPENAI_API_KEY stripped)`,
            );
        }

        runSecurityAuditorPhase = async (config, _ctx) => {
            const startedAt = new Date().toISOString();
            const t0 = Date.now();
            console.log(``);
            console.log(
                `=== Phase 2b: security_auditor (codex → ${config.model}, parallel) ===`,
            );
            console.log(``);
            const auditorAgent = yukticastle.codex(config.model, {
                env: auditorEnv,
            });
            // PR #63 — idle-completion-recovery wrapper for the same
            // sandcastle post-COMPLETE idle bug.
            const auditResult = await runWithIdleCompletionRecovery({
                phase: "security_auditor",
                branch,
                runOptions: {
                    name: "security_auditor",
                    agent: auditorAgent,
                    sandbox: sandboxConfig,
                    branchStrategy: { type: "branch", branch },
                    promptFile: `./${auditorPromptPath}`,
                    promptArgs: {
                        TASK_DESCRIPTION: TASK,
                        BRANCH: branch,
                        CONTEXT: CONTEXT_PACK,
                        FINDINGS_PATH: findingsPathRel,
                        DEFAULT_BRANCH,
                    },
                    maxIterations: config.max_iterations ?? 1,
                    completionSignal: "<promise>AUDIT_COMPLETE</promise>",
                    hooks,
                    copyToWorktree,
                    timeouts: phaseTimeouts,
                },
            });
            reportPhase(
                "security_auditor",
                config.model,
                auditResult,
                Boolean(oauthToken),
            );
            const cost = estimateCost(
                config.model,
                sumUsage(auditResult.iterations),
            );
            const counts = parseSecurityFindings(branch, findingsPathRel);
            const baseRecord = recordPhase(
                "security_auditor",
                auditResult,
                cost,
            );
            const haltOnCritical = counts.critical > 0;
            runState.phases.security_auditor = {
                ...baseRecord,
                findings_count: counts,
                findings_path: counts.foundFile ? findingsPathRel : null,
                halt_on_critical: haltOnCritical,
            };
            const finishedAt = new Date().toISOString();
            const usage = sumUsage(auditResult.iterations);
            return {
                kind: "success",
                report: {
                    role: config.role,
                    model: config.model,
                    startedAt,
                    finishedAt,
                    durationMs: Date.now() - t0,
                    iterations: auditResult.iterations.map((it) => ({
                        tokens_in: it.usage?.inputTokens ?? 0,
                        tokens_out: it.usage?.outputTokens ?? 0,
                        cache_read: it.usage?.cacheReadInputTokens ?? 0,
                        cache_create:
                            it.usage?.cacheCreationInputTokens ?? 0,
                    })),
                    usage: {
                        input: usage.inputTokens,
                        output: usage.outputTokens,
                        cache_read: usage.cacheReadInputTokens,
                        cache_create: usage.cacheCreationInputTokens,
                    },
                    costUsd: cost,
                    commits: auditResult.commits.map((c) => c.sha),
                    outputs: {
                        findings_count: counts,
                        findings_path: counts.foundFile ? findingsPathRel : null,
                    },
                    halt: haltOnCritical
                        ? {
                              reason: `${counts.critical} critical security finding(s) — auto-PR suppressed; inspect ${findingsPathRel} before merging.`,
                              severity: "critical",
                          }
                        : undefined,
                },
            };
        };
    }

    // ============================================================
    // PR H — Migration Auditor setup (mirrors security_auditor's
    // pattern). Dual-gated: env flag YUKTICASTLE_MIGRATION_AUDITOR
    // AND the diff must touch a migration path (per
    // diffMatchesMigrationPaths). Avoids burning codex quota on
    // runs that touch no schema changes — most runs.
    //
    // When both auditors fire, all three phases (reviewer + security
    // + migration) join the same parallel group with best_effort.
    // Wall-time becomes max(reviewer, security, migration) instead
    // of the sum.
    // ============================================================
    const migrationAuditorPhase = phaseFor(dagConfig, "migration_auditor");
    const MIGRATION_AUDITOR_ENV_ENABLED =
        migrationAuditorPhase !== undefined &&
        shouldRunPhase(migrationAuditorPhase);

    let migrationTriggerPaths: string[] = [];
    let MIGRATION_AUDITOR_RUNS = false;
    if (MIGRATION_AUDITOR_ENV_ENABLED && migrationAuditorPhase) {
        migrationTriggerPaths = diffMatchesMigrationPaths(branch);
        MIGRATION_AUDITOR_RUNS = migrationTriggerPaths.length > 0;
        if (!MIGRATION_AUDITOR_RUNS) {
            console.log(
                `[yukticastle] migration_auditor: env enabled but diff touches no migration paths; skipping`,
            );
        } else {
            console.log(
                `[yukticastle] migration_auditor: triggered by ${migrationTriggerPaths.length} path(s): ${migrationTriggerPaths.slice(0, 5).join(", ")}${migrationTriggerPaths.length > 5 ? ", …" : ""}`,
            );
        }
    }

    const migrationFindingsPathRel = `.yukticastle/migration-findings/${branch.replace(/\//g, "-")}.md`;
    let runMigrationAuditorPhase: DagExecutor.PhaseHandler | null = null;

    if (MIGRATION_AUDITOR_RUNS && migrationAuditorPhase) {
        const migrationPromptPath = ".yukticastle/migration-auditor-prompt.md";
        // Independent codex refresh from reviewer/security_auditor —
        // each path manages its own freshness. Cheap when codexAuth
        // is already fresh (ensureCodexAuthFreshForReviewer is
        // idempotent + short-circuits when not stale).
        const migrationFreshCodexAuth = ensureCodexAuthFreshForReviewer(
            codexAuth,
            migrationAuditorPhase.model,
        );
        const migrationEnv: Record<string, string> = { ...agentEnv };
        if (migrationFreshCodexAuth) {
            migrationEnv.CODEX_AUTH_JSON = migrationFreshCodexAuth.rawJson;
            delete migrationEnv.OPENAI_API_KEY;
            const planLabel = migrationFreshCodexAuth.planType
                ? ` (ChatGPT ${migrationFreshCodexAuth.planType})`
                : "";
            console.log(
                `[yukticastle] forwarding codex OAuth to migration_auditor container${planLabel} (OPENAI_API_KEY stripped)`,
            );
        }

        // Snapshot the trigger paths for the handler closure.
        const triggerPathsForHandler = migrationTriggerPaths.slice();

        runMigrationAuditorPhase = async (config, _ctx) => {
            const startedAt = new Date().toISOString();
            const t0 = Date.now();
            console.log(``);
            console.log(
                `=== Phase 2c: migration_auditor (codex → ${config.model}, parallel) ===`,
            );
            console.log(``);
            const migrationAgent = yukticastle.codex(config.model, {
                env: migrationEnv,
            });
            // PR #63 — idle-completion-recovery wrapper.
            const auditResult = await runWithIdleCompletionRecovery({
                phase: "migration_auditor",
                branch,
                runOptions: {
                    name: "migration_auditor",
                    agent: migrationAgent,
                    sandbox: sandboxConfig,
                    branchStrategy: { type: "branch", branch },
                    promptFile: `./${migrationPromptPath}`,
                    promptArgs: {
                        TASK_DESCRIPTION: TASK,
                        BRANCH: branch,
                        CONTEXT: CONTEXT_PACK,
                        FINDINGS_PATH: migrationFindingsPathRel,
                        DEFAULT_BRANCH,
                        TRIGGER_PATHS: triggerPathsForHandler.join("\n- "),
                    },
                    maxIterations: config.max_iterations ?? 1,
                    completionSignal: "<promise>MIGRATION_AUDIT_COMPLETE</promise>",
                    hooks,
                    copyToWorktree,
                    timeouts: phaseTimeouts,
                },
            });
            reportPhase(
                "migration_auditor",
                config.model,
                auditResult,
                Boolean(oauthToken),
            );
            const cost = estimateCost(
                config.model,
                sumUsage(auditResult.iterations),
            );
            const counts = parseMigrationFindings(
                branch,
                migrationFindingsPathRel,
            );
            const baseRecord = recordPhase(
                "migration_auditor",
                auditResult,
                cost,
            );
            const haltOnCritical = counts.critical > 0;
            runState.phases.migration_auditor = {
                ...baseRecord,
                findings_count: counts,
                findings_path: counts.foundFile
                    ? migrationFindingsPathRel
                    : null,
                halt_on_critical: haltOnCritical,
                triggered_by_paths: triggerPathsForHandler,
            };
            const finishedAt = new Date().toISOString();
            const usage = sumUsage(auditResult.iterations);
            return {
                kind: "success",
                report: {
                    role: config.role,
                    model: config.model,
                    startedAt,
                    finishedAt,
                    durationMs: Date.now() - t0,
                    iterations: auditResult.iterations.map((it) => ({
                        tokens_in: it.usage?.inputTokens ?? 0,
                        tokens_out: it.usage?.outputTokens ?? 0,
                        cache_read: it.usage?.cacheReadInputTokens ?? 0,
                        cache_create:
                            it.usage?.cacheCreationInputTokens ?? 0,
                    })),
                    usage: {
                        input: usage.inputTokens,
                        output: usage.outputTokens,
                        cache_read: usage.cacheReadInputTokens,
                        cache_create: usage.cacheCreationInputTokens,
                    },
                    costUsd: cost,
                    commits: auditResult.commits.map((c) => c.sha),
                    outputs: {
                        findings_count: counts,
                        findings_path: counts.foundFile
                            ? migrationFindingsPathRel
                            : null,
                        triggered_by_paths: triggerPathsForHandler,
                    },
                    halt: haltOnCritical
                        ? {
                              reason: `${counts.critical} critical migration finding(s) — auto-PR suppressed; inspect ${migrationFindingsPathRel} before merging.`,
                              severity: "critical",
                          }
                        : undefined,
                },
            };
        };
    }

    // ============================================================
    // PR J — Test Engineer setup (parallel sibling to reviewer +
    // auditors). Reads the implementer's diff, identifies code
    // without tests, writes tests, commits them as a single
    // "test: coverage" commit.
    //
    // Distinct from auditors: WRITES CODE (test files), not
    // read-only findings markdown. Same parallel-group placement,
    // same best_effort failure policy — each phase's value is
    // independent.
    //
    // No halt path. A test gap is forgivable; missing tests don't
    // block the PR from opening (operator can request more tests
    // in code review).
    // ============================================================
    const testEngineerPhase = phaseFor(dagConfig, "test_engineer");
    const TEST_ENGINEER_ENABLED =
        testEngineerPhase !== undefined &&
        shouldRunPhase(testEngineerPhase);

    let runTestEngineerPhase: DagExecutor.PhaseHandler | null = null;

    if (TEST_ENGINEER_ENABLED && testEngineerPhase) {
        const testEngineerPromptPath = ".yukticastle/test-engineer-prompt.md";

        runTestEngineerPhase = async (config, _ctx) => {
            const startedAt = new Date().toISOString();
            const t0 = Date.now();
            console.log(``);
            console.log(
                `=== Phase 2d: test_engineer (${config.model}, parallel) ===`,
            );
            console.log(``);
            const testEngineerAgent = yukticastle.claudeCode(config.model, {
                effort: config.effort ?? "medium",
                env: agentEnv,
            });

            // PR #63 — wrap with idle-completion-recovery. Test
            // Engineer's completion signal is TESTS_COMPLETE so the
            // wrapper's multi-signal logic handles it correctly.
            const testResult = await runWithIdleCompletionRecovery({
                phase: "test_engineer",
                branch,
                runOptions: {
                    name: "test_engineer",
                    agent: testEngineerAgent,
                    sandbox: sandboxConfig,
                    branchStrategy: { type: "branch", branch },
                    promptFile: `./${testEngineerPromptPath}`,
                    promptArgs: {
                        TASK_DESCRIPTION: TASK,
                        BRANCH: branch,
                        CONTEXT: CONTEXT_PACK,
                        DEFAULT_BRANCH,
                    },
                    maxIterations: config.max_iterations ?? 2,
                    completionSignal: "<promise>TESTS_COMPLETE</promise>",
                    hooks,
                    copyToWorktree,
                    timeouts: phaseTimeouts,
                },
            });

            reportPhase(
                "test_engineer",
                config.model,
                testResult,
                Boolean(oauthToken),
            );

            const cost = estimateCost(
                config.model,
                sumUsage(testResult.iterations),
            );

            // Inspect the engineer's commits to extract test-file
            // additions + line counts. Each commit's diff vs its
            // parent yields the added paths; we filter to common
            // test paths.
            const testFiles: string[] = [];
            let testLinesAdded = 0;
            for (const commit of testResult.commits) {
                const numstat = spawnSync(
                    "git",
                    [
                        "diff-tree",
                        "--no-commit-id",
                        "--numstat",
                        "-r",
                        commit.sha,
                    ],
                    { encoding: "utf8", timeout: 5000 },
                );
                if (numstat.status !== 0 || !numstat.stdout) continue;
                for (const line of numstat.stdout.split("\n")) {
                    if (!line.trim()) continue;
                    const [insStr, _delStr, ...pathParts] = line.split("\t");
                    const path = pathParts.join("\t");
                    if (
                        path.includes("/test") ||
                        path.includes("/__tests__") ||
                        path.endsWith(".spec.ts") ||
                        path.endsWith(".spec.tsx") ||
                        path.endsWith(".test.ts") ||
                        path.endsWith(".test.tsx") ||
                        path.endsWith("_test.go") ||
                        path.endsWith("_test.py") ||
                        path.endsWith("_spec.rb")
                    ) {
                        if (!testFiles.includes(path)) testFiles.push(path);
                        testLinesAdded += Number(insStr) || 0;
                    }
                }
            }

            const baseRecord = recordPhase(
                "test_engineer",
                testResult,
                cost,
            );
            runState.phases.test_engineer = {
                ...baseRecord,
                test_files_committed: testFiles,
                test_lines_added: testLinesAdded,
            };

            const finishedAt = new Date().toISOString();
            const usage = sumUsage(testResult.iterations);
            return {
                kind: "success",
                report: {
                    role: config.role,
                    model: config.model,
                    startedAt,
                    finishedAt,
                    durationMs: Date.now() - t0,
                    iterations: testResult.iterations.map((it) => ({
                        tokens_in: it.usage?.inputTokens ?? 0,
                        tokens_out: it.usage?.outputTokens ?? 0,
                        cache_read: it.usage?.cacheReadInputTokens ?? 0,
                        cache_create:
                            it.usage?.cacheCreationInputTokens ?? 0,
                    })),
                    usage: {
                        input: usage.inputTokens,
                        output: usage.outputTokens,
                        cache_read: usage.cacheReadInputTokens,
                        cache_create: usage.cacheCreationInputTokens,
                    },
                    costUsd: cost,
                    commits: testResult.commits.map((c) => c.sha),
                    outputs: {
                        test_files_committed: testFiles,
                        test_lines_added: testLinesAdded,
                    },
                    // No halt path on this role — test gaps are
                    // forgivable. Operator can request tests in code
                    // review if the engineer missed coverage.
                },
            };
        };
    }

    // Build the plan dynamically based on which auditors are firing.
    // Reviewer is always present; auditors are appended conditionally.
    // When NO auditors fire, fall through to the existing single-phase
    // sequential plan so the byte-identical pre-PR-G path is preserved.
    const parallelPhases: typeof reviewerPhaseConfig[] = [];
    const handlersMap: Record<string, DagExecutor.PhaseHandler> = {
        reviewer: runReviewerPhase,
    };
    if (
        SECURITY_AUDITOR_ENABLED &&
        securityAuditorPhase &&
        runSecurityAuditorPhase
    ) {
        parallelPhases.push(securityAuditorPhase);
        handlersMap.security_auditor = runSecurityAuditorPhase;
    }
    if (
        MIGRATION_AUDITOR_RUNS &&
        migrationAuditorPhase &&
        runMigrationAuditorPhase
    ) {
        parallelPhases.push(migrationAuditorPhase);
        handlersMap.migration_auditor = runMigrationAuditorPhase;
    }
    if (
        TEST_ENGINEER_ENABLED &&
        testEngineerPhase &&
        runTestEngineerPhase
    ) {
        parallelPhases.push(testEngineerPhase);
        handlersMap.test_engineer = runTestEngineerPhase;
    }

    const reviewerDagResult =
        parallelPhases.length > 0
            ? await reviewerDagExecutor.executeDag(
                  {
                      groups: [
                          {
                              kind: "parallel",
                              phases: [reviewerPhaseConfig, ...parallelPhases],
                              // best_effort: each auditor's findings are
                              // independently valuable; one auditor's
                              // crash shouldn't suppress another's results.
                              failurePolicy: "best_effort",
                          },
                      ],
                  },
                  reviewerCtx,
                  handlersMap,
              )
            : await reviewerDagExecutor.executeDag(
                  reviewerDagExecutor.compileFlatToPlan(
                      [reviewerPhaseConfig],
                      () => true,
                  ),
                  reviewerCtx,
                  handlersMap,
              );

    // Crash recovery: if the executor's overall outcome is "failed"
    // (e.g. unknown role, halt-on-failure policy fired), re-throw so
    // the existing catch block fires identically to pre-refactor.
    // For reviewer + auditor specifically with best_effort, this won't
    // fire on individual phase failures — those go to outputs.error
    // and we detect them below.
    if (
        reviewerDagResult.outcome.kind === "failed" &&
        reviewerDagResult.outcome.reason
    ) {
        throw reviewerDagResult.outcome.reason;
    }

    // Look reviewer up by role name (not index) — parallel-group
    // results are in COMPLETION order, not phase-list order, so
    // index-based lookup is wrong.
    const reviewerReport = reviewerDagResult.reports.find(
        (r) => r.role === "reviewer",
    );
    review = (reviewerReport?.outputs.run_result as
        | yukticastle.RunResult
        | undefined) ?? null;

    // Defensive: if we reached this point with review=null, the handler
    // returned success without populating run_result. Throw so the
    // existing catch block surfaces this as "reviewer_crashed" with
    // FF-merge recovery instructions — same UX as a real crash. Should
    // never fire with the bundled handler; this branch protects future
    // handler authors from a silent data-loss footgun.
    if (review === null) {
        throw new Error(
            "Reviewer handler reported success but produced no RunResult — bundled handler bug or replacement handler missing outputs.run_result.",
        );
    }

    // Self-improving learnings — capture each reviewer fix-up commit as
    // a JSONL entry. Next run's implementer surfaces these as
    // {{RECENT_PATTERNS}}. Best-effort: failures don't crash the run.
    if (review.commits.length > 0) {
        const captured = captureReviewerLearnings(branch, review.commits);
        if (captured > 0) {
            console.log(
                `[yukticastle] learnings: captured ${captured} reviewer fix-up(s) → .yukticastle/learnings.jsonl`,
            );
        }
    }

    const reviewerSilentFailure =
        review.iterations.length > 0 &&
        review.commits.length === 0 &&
        review.completionSignal === undefined;

    if (reviewerSilentFailure) {
        runState.result = "reviewer_silent_failure";
        console.warn(``);
        console.warn(`[yukticastle] ⚠️  Reviewer ran ${review.iterations.length} iteration(s) without signaling completion or committing fixes.`);
        console.warn(`[yukticastle]    The implementer's work is preserved on the branch — proceed with manual review.`);
        // Don't exit 2 here: the implementer succeeded, the reviewer just
        // didn't add value. Operator can still merge the implementer's commits.
    }

    // If we got here without flipping to a failure mode, the run is "ok"
    // (or "reviewer_silent_failure" already set above).
    if (runState.result === "error") runState.result = "ok";

    // ============================================================
    // PR #53 — post-reviewer deletion guard.
    //
    // Reviewer signaled COMPLETE, but the reviewer's `git diff main..branch`
    // measures the WRONG baseline when the agent branched off a stale
    // local main (operator hadn't pulled, OR same-day re-fire reused an
    // existing branch). The actual diff vs origin/main could be
    // catastrophic — net deletions wiping out intervening merges.
    //
    // This guard re-diffs vs `origin/${DEFAULT_BRANCH}` (the actual
    // remote tip) and trips on:
    //   - deletions/insertions ratio over threshold (default 3×, floor 50)
    //   - any file deleted under a protected path
    //
    // If tripped, mark the run failed with `deletion_guard_tripped`.
    // The auto-PR flow (if/when wired) skips, leaving the operator to
    // inspect by hand. Branch + commits are intact for recovery.
    // ============================================================
    if (runState.result === "ok") {
        const guardResult = checkDeletionGuard(branch);
        if (guardResult.tripped) {
            runState.result = "deletion_guard_tripped";
            console.error(``);
            console.error(
                `[yukticastle] ⛔ DELETION GUARD TRIPPED — refusing to mark this run successful.`,
            );
            console.error(``);
            console.error(
                `[yukticastle]    Diff vs origin/${DEFAULT_BRANCH} on branch \`${branch}\`:`,
            );
            console.error(
                `[yukticastle]      ${guardResult.totalInsertions} insertions / ${guardResult.totalDeletions} deletions`,
            );
            console.error(
                `[yukticastle]      ${guardResult.deletedFiles.length} file(s) deleted total`,
            );
            console.error(``);
            console.error(`[yukticastle]    Reason(s):`);
            for (const r of guardResult.reasons) {
                console.error(`[yukticastle]      • ${r}`);
            }
            console.error(``);
            console.error(
                `[yukticastle]    Most likely cause: agent branched off a stale base. The reviewer's`,
            );
            console.error(
                `[yukticastle]    \`git diff main..branch\` saw a small acceptable change because the`,
            );
            console.error(
                `[yukticastle]    branch's local \`main\` was the stale snapshot — meanwhile other PRs`,
            );
            console.error(
                `[yukticastle]    merged into \`origin/${DEFAULT_BRANCH}\` that this branch's commits don't include.`,
            );
            console.error(``);
            console.error(`[yukticastle]    Inspect by hand BEFORE merging anywhere:`);
            console.error(``);
            console.error(
                `      git diff --stat origin/${DEFAULT_BRANCH}..${branch}`,
            );
            console.error(
                `      git log --oneline origin/${DEFAULT_BRANCH}..${branch}`,
            );
            console.error(
                `      git log --oneline ${branch}..origin/${DEFAULT_BRANCH}    # what's missing from this branch`,
            );
            console.error(``);
            console.error(
                `[yukticastle]    If the deletions ARE intentional (legit big-deletion PR), override:`,
            );
            console.error(
                `      YUKTICASTLE_ALLOW_BULK_DELETIONS=true npm run agents:run -- ...   # re-run`,
            );
            console.error(`      # or merge by hand once you've reviewed`);
            console.error(``);
        } else if (
            guardResult.totalInsertions + guardResult.totalDeletions >
            0
        ) {
            console.log(
                `[yukticastle] ✓ deletion guard: ${guardResult.totalInsertions} insertions / ${guardResult.totalDeletions} deletions vs origin/${DEFAULT_BRANCH} (within threshold)`,
            );
        }
    }

    // ============================================================
    // PR G — process the auditor's parallel-group result.
    //
    // At this point the executor has settled both reviewer and
    // auditor. The auditor's PhaseReport is in
    // `reviewerDagResult.reports`. Three cases:
    //   1. Auditor wasn't enabled — nothing to do (block skipped).
    //   2. Auditor completed cleanly — runState already populated
    //      inside the handler; print summary line.
    //   3. Auditor crashed (handler threw) — outputs.error set,
    //      run_result missing. Mark security_auditor_crashed.
    //   4. Auditor reported critical findings → halt PhaseReport
    //      already triggered HaltSignal handling in the executor
    //      (since defaultFailurePolicy auditor onHaltSignal=halt).
    //      Wait — best_effort doesn't halt, so we need to check the
    //      halt info on the report itself.
    //
    // Severity priority (highest wins):
    //   deletion_guard_tripped (set just above)
    //   > security_halt (set here on critical findings)
    //   > security_auditor_crashed (set here on handler crash)
    //   > reviewer_silent_failure / ok (already set)
    // ============================================================
    if (SECURITY_AUDITOR_ENABLED) {
        const auditorReport = reviewerDagResult.reports.find(
            (r) => r.role === "security_auditor",
        );
        if (!auditorReport) {
            // Auditor was scheduled but didn't produce a report.
            // Shouldn't happen with the executor's invokeHandler
            // discipline (synthesizes failure on throw), but defend
            // anyway.
            if (runState.result === "ok") {
                runState.result = "security_auditor_crashed";
                runState.error =
                    "security_auditor produced no report — executor invariant violated";
            }
            console.warn(
                `[yukticastle] ⚠️  security_auditor produced no report (parallel-group invariant violation).`,
            );
        } else if (auditorReport.outputs.error) {
            // Handler threw; failure synthesized with outputs.error.
            if (runState.result === "ok") {
                runState.result = "security_auditor_crashed";
                runState.error = String(auditorReport.outputs.error);
            }
            console.warn(``);
            console.warn(
                `[yukticastle] ⚠️  Security Auditor crashed: ${auditorReport.outputs.error}`,
            );
            console.warn(
                `[yukticastle]    Implementer + reviewer work intact on ${branch}.`,
            );
            console.warn(
                `[yukticastle]    Re-run with YUKTICASTLE_SECURITY_AUDITOR=false to bypass.`,
            );
            console.warn(``);
        } else if (auditorReport.halt) {
            // Critical findings — halt the run. Don't override a
            // higher-priority failure mode (deletion_guard_tripped).
            if (runState.result === "ok") {
                runState.result = "security_halt";
            }
            const counts = runState.phases.security_auditor?.findings_count ?? {
                critical: 0, high: 0, medium: 0, low: 0, info: 0,
            };
            console.error(``);
            console.error(
                `[yukticastle] ⛔ SECURITY HALT — auditor found ${counts.critical} critical finding(s).`,
            );
            console.error(`[yukticastle]    Auto-PR flow suppressed. Inspect:`);
            console.error(``);
            console.error(`      git show ${branch}:${findingsPathRel}`);
            console.error(`      git diff --stat origin/${DEFAULT_BRANCH}..${branch}`);
            console.error(``);
            console.error(
                `[yukticastle]    Critical: ${counts.critical}   High: ${counts.high}   Medium: ${counts.medium}   Low: ${counts.low}   Info: ${counts.info}`,
            );
            console.error(``);
        } else {
            // Clean audit — counts already set in runState by the
            // handler; print the summary line.
            const rec = runState.phases.security_auditor;
            if (rec) {
                console.log(
                    `[yukticastle] ✓ security_auditor: ${rec.findings_count.critical} critical, ${rec.findings_count.high} high, ${rec.findings_count.medium} medium, ${rec.findings_count.low} low, ${rec.findings_count.info} info`,
                );
                if (rec.findings_path) {
                    console.log(
                        `[yukticastle]   findings: git show ${branch}:${rec.findings_path}`,
                    );
                }
            }
        }
    }

    // ============================================================
    // PR H — process the migration_auditor's parallel-group result.
    //
    // Mirror of the security_auditor post-execution block above.
    // Severity priority — `migration_halt` OUTRANKS `security_halt`
    // because data loss is irreversible (security breaches are
    // sometimes recoverable; deleted data is not). Each level only
    // sets `runState.result` if the current value is `"ok"`, so
    // higher-priority failures (deletion_guard_tripped) still win.
    //
    // Final priority order:
    //   deletion_guard_tripped > migration_halt > security_halt
    //     > migration_auditor_crashed / security_auditor_crashed
    //     > reviewer_silent_failure > ok
    // ============================================================
    if (MIGRATION_AUDITOR_RUNS) {
        const migrationReport = reviewerDagResult.reports.find(
            (r) => r.role === "migration_auditor",
        );
        if (!migrationReport) {
            if (runState.result === "ok") {
                runState.result = "migration_auditor_crashed";
                runState.error =
                    "migration_auditor produced no report — executor invariant violation";
            }
            console.warn(
                `[yukticastle] ⚠️  migration_auditor produced no report (parallel-group invariant violation).`,
            );
        } else if (migrationReport.outputs.error) {
            if (runState.result === "ok") {
                runState.result = "migration_auditor_crashed";
                runState.error = String(migrationReport.outputs.error);
            }
            console.warn(``);
            console.warn(
                `[yukticastle] ⚠️  Migration Auditor crashed: ${migrationReport.outputs.error}`,
            );
            console.warn(
                `[yukticastle]    Implementer + reviewer work intact on ${branch}.`,
            );
            console.warn(
                `[yukticastle]    The migration paths the auditor would have inspected:`,
            );
            for (const p of migrationTriggerPaths.slice(0, 5)) {
                console.warn(`[yukticastle]      • ${p}`);
            }
            console.warn(
                `[yukticastle]    Re-run with YUKTICASTLE_MIGRATION_AUDITOR=false to bypass.`,
            );
            console.warn(``);
        } else if (migrationReport.halt) {
            // Critical finding: data-loss risk. Outrank security_halt
            // but never override deletion_guard_tripped.
            if (
                runState.result === "ok" ||
                runState.result === "security_halt"
            ) {
                runState.result = "migration_halt";
            }
            const counts =
                runState.phases.migration_auditor?.findings_count ?? {
                    critical: 0,
                    high: 0,
                    medium: 0,
                    low: 0,
                    info: 0,
                };
            console.error(``);
            console.error(
                `[yukticastle] ⛔ MIGRATION HALT — auditor found ${counts.critical} critical migration finding(s).`,
            );
            console.error(
                `[yukticastle]    Auto-PR flow suppressed. Data-loss risk — inspect by hand:`,
            );
            console.error(``);
            console.error(
                `      git show ${branch}:${migrationFindingsPathRel}`,
            );
            console.error(
                `      git diff --stat origin/${DEFAULT_BRANCH}..${branch}`,
            );
            console.error(``);
            console.error(
                `[yukticastle]    Critical: ${counts.critical}   High: ${counts.high}   Medium: ${counts.medium}   Low: ${counts.low}   Info: ${counts.info}`,
            );
            console.error(``);
        } else {
            const rec = runState.phases.migration_auditor;
            if (rec) {
                console.log(
                    `[yukticastle] ✓ migration_auditor: ${rec.findings_count.critical} critical, ${rec.findings_count.high} high, ${rec.findings_count.medium} medium, ${rec.findings_count.low} low, ${rec.findings_count.info} info`,
                );
                if (rec.findings_path) {
                    console.log(
                        `[yukticastle]   findings: git show ${branch}:${rec.findings_path}`,
                    );
                }
            }
        }
    }

    // ============================================================
    // PR J — process the test_engineer's parallel-group result.
    //
    // Simpler than the auditor branches because there's no halt
    // path. Two cases:
    //   1. Engineer succeeded → log file count + lines added.
    //   2. Engineer crashed → set test_engineer_crashed, warn,
    //      continue. Run can still succeed; other phases unaffected.
    // ============================================================
    if (TEST_ENGINEER_ENABLED) {
        const testReport = reviewerDagResult.reports.find(
            (r) => r.role === "test_engineer",
        );
        if (!testReport) {
            if (runState.result === "ok") {
                runState.result = "test_engineer_crashed";
                runState.error =
                    "test_engineer produced no report — executor invariant violation";
            }
            console.warn(
                `[yukticastle] ⚠️  test_engineer produced no report (parallel-group invariant violation).`,
            );
        } else if (testReport.outputs.error) {
            if (runState.result === "ok") {
                runState.result = "test_engineer_crashed";
                runState.error = String(testReport.outputs.error);
            }
            console.warn(``);
            console.warn(
                `[yukticastle] ⚠️  Test Engineer crashed: ${testReport.outputs.error}`,
            );
            console.warn(
                `[yukticastle]    Implementer + reviewer + auditor results all intact on ${branch}.`,
            );
            console.warn(
                `[yukticastle]    Re-run with YUKTICASTLE_TEST_ENGINEER=false to bypass, or add tests by hand.`,
            );
            console.warn(``);
        } else {
            const rec = runState.phases.test_engineer;
            if (rec) {
                if (rec.test_files_committed.length > 0) {
                    console.log(
                        `[yukticastle] ✓ test_engineer: ${rec.test_files_committed.length} test file(s), +${rec.test_lines_added} lines`,
                    );
                    for (const f of rec.test_files_committed.slice(0, 5)) {
                        console.log(`[yukticastle]   + ${f}`);
                    }
                    if (rec.test_files_committed.length > 5) {
                        console.log(
                            `[yukticastle]   + (${rec.test_files_committed.length - 5} more)`,
                        );
                    }
                } else {
                    console.log(
                        `[yukticastle] ✓ test_engineer: ran but added no tests (likely judged existing coverage adequate; check logs).`,
                    );
                }
            }
        }
    }
} catch (e) {
    // Phase 2 crashed. Implementer's work is still on `branch` — the
    // operator can FF-merge it manually after inspecting. Don't re-throw
    // so the `finally` block (printFinalSummary) gets to run.
    const msg = (e as Error).message ?? String(e);
    console.error(``);
    console.error(`[yukticastle] ⛔ Phase 2 (reviewer) crashed: ${msg}`);
    console.error(`[yukticastle]    Implementer's commits are intact on branch \`${branch}\`.`);
    console.error(`[yukticastle]    Inspect + FF-merge once you've diagnosed:`);
    console.error(``);
    console.error(`      git log main..${branch} --oneline`);
    console.error(`      git diff main..${branch}`);
    console.error(`      git checkout main && git merge --ff-only ${branch}`);
    console.error(``);
    if ((e as Error).stack) {
        console.error((e as Error).stack);
    }
    runState.result = "reviewer_crashed";
    runState.error = msg;
}


// ============================================================
// Final summary — ALWAYS runs after Phase 2, success OR crash.
// ============================================================

printFinalSummary(implement, review);

function printFinalSummary(
    impl: yukticastle.RunResult,
    rev: yukticastle.RunResult | null,
): void {
    // `autoCommitSha` is module-scoped. When set, the implementer
    // emitted COMPLETE with uncommitted work and we recovered it on
    // its behalf — count toward impl commits in the summary so the
    // operator sees the real branch state rather than a misleading
    // "0 impl commits" line.
    const autoCommitShaList = autoCommitSha ? [autoCommitSha] : [];
    const allShas = [
        ...autoCommitShaList,
        ...impl.commits.map((c) => c.sha),
        ...(rev?.commits.map((c) => c.sha) ?? []),
    ];
    const effectiveImplCount = impl.commits.length + autoCommitShaList.length;
    console.log(`\n=== [yukticastle] DONE ===`);
    console.log(`[yukticastle] branch:        ${branch}`);
    console.log(
        `[yukticastle] impl commits:  ${effectiveImplCount}` +
            (autoCommitSha ? ` (incl. 1 auto-commit ${autoCommitSha.slice(0, 12)})` : ""),
    );
    console.log(`[yukticastle] review commits:${rev?.commits.length ?? "(skipped)"}`);

    if (allShas.length > 0) {
        console.log(``);
        console.log(`Commits on this branch (most recent first):`);
        try {
            // Show the actual commit subjects + diff stats from the host's
            // git (yukticastle has merged the branch back into the worktree
            // already, so `git log` works without the sandbox).
            const log = execSync(
                `git log --oneline ${allShas[allShas.length - 1]}^..${allShas[0]}`,
                { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] },
            ).trim();
            for (const line of log.split("\n")) {
                console.log(`  ${line}`);
            }
            console.log(``);
            const stat = execSync(
                `git diff --stat main..${branch} 2>/dev/null || git diff --stat HEAD~${allShas.length}..HEAD`,
                { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], shell: "/bin/bash" },
            ).trim();
            if (stat) {
                console.log(`Diff stat:`);
                for (const line of stat.split("\n")) {
                    console.log(`  ${line}`);
                }
                // Extract files-changed count from the last line:
                //   "12 files changed, 345 insertions(+), 67 deletions(-)"
                const lastLine = stat.split("\n").pop() ?? "";
                const m = /(\d+)\s+files?\s+changed/.exec(lastLine);
                if (m) runState.files_changed = Number(m[1]);
            }
        } catch {
            // Best-effort — if git isn't available or the branch isn't
            // resolvable from the host (rare), skip the summary.
            console.log(`  ${allShas.join("\n  ")}`);
        }
    }

    const totalUsage = sumUsage([...impl.iterations, ...(rev?.iterations ?? [])]);
    const totalCost =
        estimateCost(IMPLEMENTER_MODEL, sumUsage(impl.iterations)) +
        estimateCost(REVIEWER_MODEL, sumUsage(rev?.iterations ?? []));
    runState.total_cost_usd = Number(totalCost.toFixed(6));
    runState.total_cost_billed_usd = oauthToken ? 0 : Number(totalCost.toFixed(6));
    console.log(``);
    console.log(`Total tokens:  in=${formatTokens(totalUsage.inputTokens)} out=${formatTokens(totalUsage.outputTokens)} cache-read=${formatTokens(totalUsage.cacheReadInputTokens)}`);
    if (oauthToken) {
        console.log(`Total cost:    $0 (MAX-OAuth) — would have been $${totalCost.toFixed(3)} on API-key`);
    } else {
        console.log(`Total cost:    $${totalCost.toFixed(3)} (API-key billed)`);
    }
    console.log(``);
    console.log(`Inspect:  git log ${branch} && git diff main..${branch}`);
    console.log(`Merge:    git checkout main && git merge --ff-only ${branch}`);
    console.log(``);
}
