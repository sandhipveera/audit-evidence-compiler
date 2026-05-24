#!/usr/bin/env bash
# yc-unlock-prisma.sh — release a stuck Prisma migration advisory lock.
#
# When two Vercel deploys land within minutes and both run
# `prisma migrate deploy`, the second can fail with:
#
#   P1002: Timed out trying to acquire a postgres advisory lock
#
# Worse: if the first deploy crashes mid-migration, the lock orphans
# and ALL subsequent deploys fail until cleared. On Neon (PgBouncer
# pooler), the orphaned lock survives the Prisma process exit because
# the pool keeps the backend connection alive — `pg_stat_activity`
# may show no matching pid even while `pg_locks` still has the lock
# `granted=t`.
#
# This helper:
#   1. Loads DATABASE_URL from .env if not already in env.
#   2. Tries `pg_advisory_unlock(72707369)` up to 5 times. The pooler
#      hands out different connections each invocation; one will
#      eventually land on the conn holding the orphaned lock.
#   3. Falls back to `pg_terminate_backend(pid)` against whichever
#      pid is shown in `pg_locks` for the advisory lock — for the
#      case where unlock keeps returning 'f'.
#
# Then redeploy:
#
#   vercel redeploy <failed-deploy-url>
#
# Provenance: gextrader 2026-05-21 incident (PR #34 + fixup 34e7727).
# Cleared in ~10s with this recipe. Documented at:
#   docs/runbooks/prisma-lock-recovery.md
#   docs/ENHANCEMENTS.md #43
#
# Knobs:
#   YUKTICASTLE_PRISMA_UNLOCK_ATTEMPTS  default 5 — how many
#                                       pg_advisory_unlock attempts
#                                       before falling back to
#                                       pg_terminate_backend.
#   YUKTICASTLE_PRISMA_LOCK_ID          default 72707369 — Prisma's
#                                       hardcoded advisory lock id
#                                       (0x4501339). Constant across
#                                       Prisma versions; override only
#                                       if a future Prisma changes it.

set -euo pipefail

LOCK_ID="${YUKTICASTLE_PRISMA_LOCK_ID:-72707369}"
ATTEMPTS="${YUKTICASTLE_PRISMA_UNLOCK_ATTEMPTS:-5}"

# ──────────────────────────────────────────────────────────────
# Resolve DATABASE_URL: env first, then .env file
# ──────────────────────────────────────────────────────────────
if [[ -z "${DATABASE_URL:-}" ]]; then
    if [[ -f .env ]]; then
        echo "[yc-unlock-prisma] sourcing .env to find DATABASE_URL…"
        set -a
        # shellcheck disable=SC1091
        source .env
        set +a
    fi
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
    echo ""
    echo "[yc-unlock-prisma] ⛔ DATABASE_URL not found in env or .env"
    echo ""
    echo "  Either run from the project root that has .env with DATABASE_URL,"
    echo "  or export DATABASE_URL=postgres://… before invoking."
    exit 1
fi

if ! command -v psql >/dev/null 2>&1; then
    echo ""
    echo "[yc-unlock-prisma] ⛔ psql not found on PATH."
    echo ""
    echo "  Install postgresql-client first:"
    echo "    macOS:   brew install libpq && brew link --force libpq"
    echo "    Linux:   apt-get install -y postgresql-client"
    exit 1
fi

# ──────────────────────────────────────────────────────────────
# Step 1 — pg_advisory_unlock, up to N attempts
# ──────────────────────────────────────────────────────────────
echo "[yc-unlock-prisma] attempting pg_advisory_unlock(${LOCK_ID}) up to ${ATTEMPTS} times…"
echo "[yc-unlock-prisma] (pooler hands out different conns each invocation; one will hit the holder)"
echo ""

for i in $(seq 1 "${ATTEMPTS}"); do
    RESULT=$(psql "${DATABASE_URL}" -t -A -c "SELECT pg_advisory_unlock(${LOCK_ID});" 2>/dev/null || echo "ERR")
    # Trim whitespace from psql output
    RESULT="${RESULT//[[:space:]]/}"
    echo "  attempt ${i}/${ATTEMPTS}: pg_advisory_unlock returned '${RESULT}'"
    if [[ "${RESULT}" == "t" ]]; then
        echo ""
        echo "[yc-unlock-prisma] ✓ lock released on attempt ${i}"
        echo ""
        echo "  Next step:"
        echo "    vercel redeploy <failed-deploy-url>"
        exit 0
    fi
done

# ──────────────────────────────────────────────────────────────
# Step 2 — pg_terminate_backend fallback
# ──────────────────────────────────────────────────────────────
echo ""
echo "[yc-unlock-prisma] all ${ATTEMPTS} unlock attempts returned 'f'."
echo "[yc-unlock-prisma] falling back to pg_terminate_backend against pg_locks holder…"
echo ""

# Find pids holding the advisory lock, terminate them, and print what we did.
TERMINATE_SQL="
SELECT pid, pg_terminate_backend(pid) AS terminated
FROM pg_locks
WHERE locktype = 'advisory' AND objid = ${LOCK_ID};
"
TERMINATE_OUTPUT=$(psql "${DATABASE_URL}" -P pager=off -c "${TERMINATE_SQL}" 2>&1 || true)
echo "${TERMINATE_OUTPUT}"

# Detect "0 rows" or empty result — means no holder found in pg_locks
if echo "${TERMINATE_OUTPUT}" | grep -Eq "\(0 rows\)|no rows"; then
    echo ""
    echo "[yc-unlock-prisma] ⛔ no advisory lock with objid=${LOCK_ID} found in pg_locks."
    echo ""
    echo "  Either the lock was already released (the unlock attempts above"
    echo "  may have succeeded silently on a conn that pg_locks no longer"
    echo "  shows), OR DATABASE_URL points at the wrong db. Verify:"
    echo "    psql \"\${DATABASE_URL}\" -c 'SELECT current_database();'"
    exit 1
fi

echo ""
echo "[yc-unlock-prisma] ✓ backend(s) terminated; advisory lock should now be released"
echo ""
echo "  Next step:"
echo "    vercel redeploy <failed-deploy-url>"
echo ""
echo "  If the redeploy ALSO fails with P1002, the orphaned backend may"
echo "  have respawned. Restart the Neon compute node from the console"
echo "  (~30s downtime) as the last-resort full reset."
