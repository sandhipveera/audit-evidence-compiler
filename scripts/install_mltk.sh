#!/usr/bin/env bash
# Install the Splunk Machine Learning Toolkit (MLTK) + Python for Scientific
# Computing (PSC) into the aec-splunk container, so the pipeline's
# `splunk_ml_anomaly` node can run MLTK `fit`/`apply` at runtime (it falls back
# to the built-in `anomalydetection` command when MLTK is absent).
#
# MLTK is a free but login-gated Splunkbase download — this script does NOT
# download it. Get the two .tgz packages (matching Linux 64-bit + your Splunk
# version) from Splunkbase and point this script at them:
#
#   Splunk Machine Learning Toolkit       https://splunkbase.splunk.com/app/2890
#   Python for Scientific Computing (Lx)  https://splunkbase.splunk.com/app/2882
#
# Usage:
#   bash scripts/install_mltk.sh /path/to/mltk.tgz /path/to/psc.tgz
#   bash scripts/install_mltk.sh          # auto-discovers both in ~/.aec-data, ~/Downloads
#
# Idempotent: re-running reinstalls/overwrites the apps and restarts Splunk.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="aec-splunk"
APPS_DIR="/opt/splunk/etc/apps"
MGMT="https://localhost:8089"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗ FATAL:${NC} $*"; exit 1; }

set -a
[ -f "$HOME/.aec-config" ] && . "$HOME/.aec-config"
[ -f "$REPO_DIR/.env" ] && . "$REPO_DIR/.env"
set +a
PW="${SPLUNK_PASSWORD:-}"
[ -n "$PW" ] || die "SPLUNK_PASSWORD not set (check ~/.aec-config / .env)"

docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$" || die "container ${CONTAINER} not running"

# --- locate the two packages ----------------------------------------------
MLTK_TGZ="${1:-}"
PSC_TGZ="${2:-}"

find_pkg() {  # $@ = glob patterns, first match wins
  local hit pat
  for dir in "$HOME/.aec-data" "$HOME/Downloads" "$REPO_DIR" /tmp; do
    for pat in "$@"; do
      hit="$(ls -1t "$dir"/$pat 2>/dev/null | head -1 || true)"
      [ -n "$hit" ] && { echo "$hit"; return 0; }
    done
  done
  return 1
}

# MLTK ships under several Splunkbase filenames: the classic
# "splunk-machine-learning-toolkit" and the newer "splunk-ai-toolkit" (same
# Splunk_ML_Toolkit app id; provides fit/apply/score plus the ai/aiagent LLM
# commands).
[ -z "$MLTK_TGZ" ] && MLTK_TGZ="$(find_pkg \
  'splunk-machine-learning-toolkit*.tgz' 'splunk-ai-toolkit*.tgz' 'Splunk_ML_Toolkit*.tgz' || true)"
[ -z "$PSC_TGZ" ]  && PSC_TGZ="$(find_pkg 'python-for-scientific-computing*.tgz' || true)"

[ -n "$MLTK_TGZ" ] && [ -f "$MLTK_TGZ" ] || die "MLTK .tgz not found — pass it as arg 1 (download from splunkbase.splunk.com/app/2890)"
[ -n "$PSC_TGZ" ]  && [ -f "$PSC_TGZ" ]  || die "Python-for-Scientific-Computing .tgz not found — pass it as arg 2 (splunkbase.splunk.com/app/2882)"

ok "MLTK package: $MLTK_TGZ"
ok "PSC  package: $PSC_TGZ"

# --- install both into the container ---------------------------------------
install_tgz() {  # $1 = host path to .tgz
  local tgz="$1" base
  base="$(basename "$tgz")"
  info "copying ${base} → ${CONTAINER}:/tmp/"
  docker cp "$tgz" "${CONTAINER}:/tmp/${base}"
  info "extracting into ${APPS_DIR}"
  docker exec -u root "$CONTAINER" tar -xzf "/tmp/${base}" -C "$APPS_DIR"
  docker exec -u root "$CONTAINER" chown -R splunk:splunk "$APPS_DIR"
  docker exec -u root "$CONTAINER" rm -f "/tmp/${base}"
}

install_tgz "$PSC_TGZ"     # PSC first — MLTK depends on its Python runtime
install_tgz "$MLTK_TGZ"

# --- restart so the apps + search commands register ------------------------
# NB: `splunk restart` rejects -auth on Splunk 10.x; restart without it.
info "restarting splunkd (~1-3 min)…"
docker exec -u splunk "$CONTAINER" /opt/splunk/bin/splunk restart >/dev/null 2>&1 || true

# --- verify `fit` is now a known command -----------------------------------
info "waiting for splunkd + verifying MLTK fit command…"
api() { curl -sk -u "admin:${PW}" "$@"; }
for i in $(seq 1 30); do
  sleep 6
  resp="$(api -X POST "${MGMT}/services/search/jobs/export" \
    -d search='| makeresults count=20 | streamstats count as n | fit LinearRegression n from n' \
    -d output_mode=json -d exec_mode=oneshot 2>/dev/null || true)"
  if echo "$resp" | grep -q '"predicted(n)"\|"n"'; then
    ok "MLTK installed — \`fit\` runs in Splunk"
    exit 0
  fi
  if echo "$resp" | grep -qi "Unknown search command 'fit'"; then
    : # not ready yet
  fi
done
warn "Could not confirm \`fit\` within timeout. Check: bash scripts/manage.sh logs-splunk"
warn "MLTK may still be starting; the pipeline falls back to built-in anomalydetection meanwhile."
