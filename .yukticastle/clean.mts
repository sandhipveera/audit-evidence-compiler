// YuktiCastle cleanup — delete stale agent branches + orphaned worktrees.
//
// What gets cleaned:
//
//   1. Branches matching `agent/*` older than --age (default 7d).
//      The agent/* prefix is what main.mts creates per-task; nothing
//      else matches it, so this is safe.
//
//   2. Directories under .yukticastle/worktrees/ that either (a) have no
//      git worktree entry (orphaned by a crashed run) or (b) point at a
//      branch that's also being deleted.
//
// Defaults are dry-run. Pass --force to actually delete. The script
// NEVER deletes:
//   - the currently checked-out branch
//   - branches outside the agent/* prefix
//   - worktrees with uncommitted changes (yukticastle preserves those
//     intentionally for operator review)
//
// Usage:
//   npm run agents:clean              # dry-run, default 7-day cutoff
//   npm run agents:clean -- --age=1d  # cleanup yesterday's runs onward
//   npm run agents:clean -- --force   # actually delete
//   npm run agents:clean -- --age=0d --force  # nuke everything

import { execSync } from "node:child_process";
import { existsSync, rmSync, statSync } from "node:fs";
import { readdirSync } from "node:fs";
import { join, resolve } from "node:path";

// ============================================================
// CLI args
// ============================================================

const args = process.argv.slice(2);
const force = args.includes("--force");
const ageArg = args.find((a) => a.startsWith("--age="))?.split("=")[1] ?? "7d";
const ageMatch = /^(\d+)d$/.exec(ageArg);
if (!ageMatch) {
    console.error(`Invalid --age: ${ageArg}. Use Nd format (e.g. 7d, 1d, 0d).`);
    process.exit(1);
}
const ageDays = Number(ageMatch[1]);
const cutoffMs = Date.now() - ageDays * 24 * 60 * 60 * 1000;

console.log(`[agents:clean] mode:    ${force ? "DELETE" : "DRY-RUN (pass --force to delete)"}`);
console.log(`[agents:clean] cutoff:  branches older than ${ageDays}d (before ${new Date(cutoffMs).toISOString()})`);
console.log(``);

// ============================================================
// Helpers
// ============================================================

function sh(cmd: string): string {
    return execSync(cmd, { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"] }).trim();
}

function shSafe(cmd: string): { ok: boolean; output: string } {
    try {
        return { ok: true, output: sh(cmd) };
    } catch (err) {
        return { ok: false, output: err instanceof Error ? err.message : String(err) };
    }
}

const currentBranch = sh("git rev-parse --abbrev-ref HEAD");
const repoRoot = sh("git rev-parse --show-toplevel");

// ============================================================
// 1. Stale agent branches
// ============================================================

interface BranchInfo {
    name: string;
    committerDate: number;
    sha: string;
}

const branchListing = sh(
    "git for-each-ref refs/heads/agent --format='%(refname:short)|%(committerdate:unix)|%(objectname)'",
);

const branches: BranchInfo[] = branchListing
    .split("\n")
    .filter(Boolean)
    .map((line) => {
        const [name, ts, sha] = line.split("|");
        return {
            name: name ?? "",
            committerDate: Number(ts) * 1000,
            sha: sha ?? "",
        };
    })
    .filter((b) => b.name !== currentBranch && b.committerDate < cutoffMs);

console.log(`=== Stale agent/* branches ===`);
if (branches.length === 0) {
    console.log(`(none)`);
} else {
    for (const b of branches) {
        const ageDaysActual = Math.floor((Date.now() - b.committerDate) / (24 * 60 * 60 * 1000));
        console.log(`  ${b.name}  (${ageDaysActual}d old, ${b.sha.slice(0, 7)})`);
    }
}
console.log(``);

// ============================================================
// 2. Worktrees under .yukticastle/worktrees/
// ============================================================

const yukticastleWtDir = resolve(repoRoot, ".yukticastle/worktrees");
interface RegisteredWorktree {
    worktree: string;
    branch: string | undefined;
}

const registeredWorktrees: RegisteredWorktree[] = sh("git worktree list --porcelain")
    .split("\n\n")
    .map((block): RegisteredWorktree | null => {
        const lines = block.split("\n");
        const wt = lines.find((l) => l.startsWith("worktree "))?.slice("worktree ".length);
        if (!wt) return null;
        const branch = lines
            .find((l) => l.startsWith("branch "))
            ?.slice("branch ".length)
            .replace(/^refs\/heads\//, "");
        return { worktree: wt, branch };
    })
    .filter((w): w is RegisteredWorktree => w !== null);

interface WorktreeInfo {
    path: string;
    branch?: string;
    registered: boolean;
    hasUncommittedChanges: boolean;
    branchScheduledForDelete: boolean;
}

const worktrees: WorktreeInfo[] = [];

if (existsSync(yukticastleWtDir)) {
    for (const entry of readdirSync(yukticastleWtDir)) {
        const full = join(yukticastleWtDir, entry);
        if (!statSync(full).isDirectory()) continue;

        const reg = registeredWorktrees.find((w) => w.worktree === full);
        const branch = reg?.branch;

        // Check uncommitted changes — only meaningful if registered.
        let hasUncommittedChanges = false;
        if (reg) {
            const status = shSafe(`git -C "${full}" status --porcelain`);
            hasUncommittedChanges = status.ok && status.output.length > 0;
        }

        worktrees.push({
            path: full,
            branch,
            registered: Boolean(reg),
            hasUncommittedChanges,
            branchScheduledForDelete: Boolean(
                branch && branches.some((b) => b.name === branch),
            ),
        });
    }
}

console.log(`=== Worktrees under .yukticastle/worktrees/ ===`);
if (worktrees.length === 0) {
    console.log(`(none)`);
} else {
    for (const w of worktrees) {
        const tags: string[] = [];
        if (!w.registered) tags.push("orphaned");
        if (w.hasUncommittedChanges) tags.push("dirty (will skip)");
        if (w.branchScheduledForDelete) tags.push("branch will be deleted");
        if (w.branch) tags.push(`branch=${w.branch}`);
        console.log(`  ${w.path}  [${tags.join(", ") || "registered, clean"}]`);
    }
}
console.log(``);

// ============================================================
// 3. Decide what's actually deletable
// ============================================================

// A worktree is deletable if it's either orphaned OR its branch is
// being deleted. Skip if it has uncommitted changes (operator's call).
const deletableWorktrees = worktrees.filter(
    (w) => !w.hasUncommittedChanges && (!w.registered || w.branchScheduledForDelete),
);

const skippedDirty = worktrees.filter((w) => w.hasUncommittedChanges);
if (skippedDirty.length > 0) {
    console.log(`⚠️  ${skippedDirty.length} worktree(s) skipped due to uncommitted changes:`);
    for (const w of skippedDirty) {
        console.log(`    ${w.path}`);
    }
    console.log(`    Inspect with: git -C "<path>" status`);
    console.log(``);
}

// ============================================================
// 4. Execute (or dry-run)
// ============================================================

if (!force) {
    console.log(`=== Plan ===`);
    console.log(`  branches to delete:  ${branches.length}`);
    console.log(`  worktrees to remove: ${deletableWorktrees.length}`);
    console.log(``);
    console.log(`Re-run with --force to actually delete.`);
    process.exit(0);
}

console.log(`=== Executing ===`);
let branchesDeleted = 0;
let worktreesDeleted = 0;

// Worktrees first (must come BEFORE branch delete so git worktree
// references resolve cleanly).
for (const w of deletableWorktrees) {
    if (w.registered) {
        const r = shSafe(`git worktree remove --force "${w.path}"`);
        if (!r.ok) {
            console.warn(`  ⚠️  git worktree remove failed for ${w.path}: ${r.output}`);
            continue;
        }
        console.log(`  ✓ removed worktree ${w.path}`);
    } else {
        // Orphaned dir — just rm
        try {
            rmSync(w.path, { recursive: true, force: true });
            console.log(`  ✓ removed orphan dir ${w.path}`);
        } catch (err) {
            console.warn(`  ⚠️  rmSync failed for ${w.path}: ${err}`);
            continue;
        }
    }
    worktreesDeleted++;
}

// Then branches.
for (const b of branches) {
    const r = shSafe(`git branch -D "${b.name}"`);
    if (!r.ok) {
        console.warn(`  ⚠️  git branch -D failed for ${b.name}: ${r.output}`);
        continue;
    }
    console.log(`  ✓ deleted branch ${b.name}`);
    branchesDeleted++;
}

// Prune any worktree refs that may have been left dangling.
shSafe("git worktree prune");

console.log(``);
console.log(`=== Done ===`);
console.log(`  branches deleted:  ${branchesDeleted} / ${branches.length}`);
console.log(`  worktrees removed: ${worktreesDeleted} / ${deletableWorktrees.length}`);
