#!/usr/bin/env bash
# Install the auditcompiler Splunk app into the aec-splunk container and make the
# Compliance Posture dashboard demo-ready (index + app + seeded verdict history).
#
# Idempotent: safe to re-run. Reads SPLUNK_PASSWORD / HEC_TOKEN from ~/.aec-config
# and the project .env (same as setup.sh).
#
#   bash scripts/install_splunk_app.sh            # install + restart + seed
#   bash scripts/install_splunk_app.sh --no-seed  # install only (no posture data)
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="aec-splunk"
MGMT="https://localhost:8089"
HEC_URL="${HEC_URL:-https://localhost:8088}"
POSTURE_INDEX="auditcompiler_posture"
SEED=1
[ "${1:-}" = "--no-seed" ] && SEED=0

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗ FATAL:${NC} $*"; exit 1; }

# Load config (password, HEC token) without printing secrets.
set -a
[ -f "$HOME/.aec-config" ] && . "$HOME/.aec-config"
[ -f "$REPO_DIR/.env" ] && . "$REPO_DIR/.env"
set +a
PW="${SPLUNK_PASSWORD:-}"
[ -n "$PW" ] || die "SPLUNK_PASSWORD not set (check ~/.aec-config / .env)"

docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$" || die "container ${CONTAINER} not running"
api() { curl -sk -u "admin:${PW}" "$@"; }

# 1. Posture index ----------------------------------------------------------
if api "${MGMT}/services/data/indexes/${POSTURE_INDEX}?output_mode=json" | grep -q '"name"'; then
  ok "index ${POSTURE_INDEX} already exists"
else
  api -X POST "${MGMT}/services/data/indexes" -d name="${POSTURE_INDEX}" \
      -d frozenTimePeriodInSecs=7776000 -d output_mode=json >/dev/null
  ok "created index ${POSTURE_INDEX}"
fi

# 2. App into the container -------------------------------------------------
info "copying app → ${CONTAINER}:/opt/splunk/etc/apps/auditcompiler"
docker exec "$CONTAINER" rm -rf /opt/splunk/etc/apps/auditcompiler 2>/dev/null || true
docker cp "$REPO_DIR/splunk-app/auditcompiler" "${CONTAINER}:/opt/splunk/etc/apps/auditcompiler"
docker exec -u root "$CONTAINER" rm -rf /opt/splunk/etc/apps/auditcompiler/bin/__pycache__ 2>/dev/null || true
docker exec -u root "$CONTAINER" chown -R splunk:splunk /opt/splunk/etc/apps/auditcompiler
ok "app installed"

# 3. Restart so command/dashboard/nav register ------------------------------
# NB: `splunk restart` rejects -auth on Splunk 10.x (prints usage + fails the
# start), so restart without it.
info "restarting splunkd (~1-3 min)…"
docker exec -u splunk "$CONTAINER" /opt/splunk/bin/splunk restart >/dev/null 2>&1 || true
for i in $(seq 1 60); do
  [ "$(api -o /dev/null -w '%{http_code}' "${MGMT}/services/server/info")" = "200" ] && { ok "splunkd up"; break; }
  sleep 5
done

# 4. Seed posture history ---------------------------------------------------
if [ "$SEED" = "1" ]; then
  [ -n "${HEC_TOKEN:-}" ] || die "HEC_TOKEN not set — needed to seed (or pass --no-seed)"
  info "seeding ${POSTURE_INDEX}…"
  HEC_TOKEN="$HEC_TOKEN" HEC_URL="$HEC_URL" POSTURE_INDEX="$POSTURE_INDEX" \
    python3 "$REPO_DIR/scripts/seed_posture.py"
  ok "posture seeded"
else
  warn "skipped seeding (--no-seed) — dashboard stays empty until saved searches run"
fi

echo
ok "Done. Open Splunk Web → app 'Audit Evidence Compiler' → Compliance Posture."
