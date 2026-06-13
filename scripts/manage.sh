#!/usr/bin/env bash
# =============================================================================
# Audit Evidence Auto-Compiler — Stack Management Script
#
# Usage:
#   bash scripts/manage.sh <command>
#
# Commands:
#   status    Show status of all services
#   start     Start all services
#   stop      Stop all services
#   restart   Restart all services
#   logs      Tail live logs (all services)
#   logs-web  Tail AEC web logs only
#   logs-splunk  Tail Splunk logs only
#   update    git pull + reinstall + restart
#   verify    Run a quick smoke test
#   shell     Open a shell with venv activated
# =============================================================================

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
info() { echo -e "${CYAN}→${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; }
h1()   { echo; echo -e "\033[1;36m━━━  $*  ━━━\033[0m"; echo; }

# ── Load config + env (config file first, .env overrides) ─────────────────────
AEC_CONFIG="${HOME}/.aec-config"
[[ -f "$AEC_CONFIG" ]] && source "$AEC_CONFIG" 2>/dev/null || true
[[ -f "$ENV_FILE"   ]] && export $(grep -v '^#' "$ENV_FILE" | grep -v '^$' | xargs) 2>/dev/null || true
SPLUNK_PASSWORD="${SPLUNK_PASSWORD:-changeme123}"
SPLUNK_PORT_MGMT="${SPLUNK_PORT_MGMT:-8089}"
AEC_PORT="${AEC_PORT:-8000}"

# ── Service check helpers ──────────────────────────────────────────────────────
splunk_running() { docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^aec-splunk$"; }
splunk_healthy() {
  curl -sk -u "admin:${SPLUNK_PASSWORD}" \
    "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
    2>/dev/null | grep -q '"version"'
}
web_running()    { systemctl is-active --quiet aec-web 2>/dev/null; }
web_healthy()    { curl -s --max-time 3 "http://localhost:${AEC_PORT}/api/controls" 2>/dev/null | grep -q "control_id"; }
tunnel_running() { systemctl is-active --quiet cloudflared 2>/dev/null; }

# ─────────────────────────────────────────────────────────────────────────────
cmd_status() {
  h1 "AEC Stack Status"

  echo -e "  \033[1mService\033[0m              \033[1mSystemd\033[0m    \033[1mHealthy\033[0m"
  echo    "  ─────────────────────────────────────────────"

  # Splunk
  if splunk_running; then
    SPLUNK_STATE="${GREEN}running${NC}"
    if splunk_healthy; then
      SPLUNK_VER=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
        "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'][0]['content']['version'])" 2>/dev/null || echo "?")
      SPLUNK_HEALTH="${GREEN}✓ v${SPLUNK_VER}${NC}"
    else
      SPLUNK_HEALTH="${YELLOW}starting...${NC}"
    fi
  else
    SPLUNK_STATE="${RED}stopped${NC}"; SPLUNK_HEALTH="${RED}✗${NC}"
  fi
  echo -e "  Splunk               ${SPLUNK_STATE}     ${SPLUNK_HEALTH}"

  # AEC web
  if web_running; then
    WEB_STATE="${GREEN}active${NC}"
    WEB_HEALTH=$(web_healthy && echo -e "${GREEN}✓ port ${AEC_PORT}${NC}" || echo -e "${YELLOW}starting...${NC}")
  else
    WEB_STATE="${RED}stopped${NC}"; WEB_HEALTH="${RED}✗${NC}"
  fi
  echo -e "  AEC web              ${WEB_STATE}      ${WEB_HEALTH}"

  # Cloudflare tunnel
  if tunnel_running; then
    CF_STATE="${GREEN}active${NC}"; CF_HEALTH="${GREEN}✓${NC}"
  else
    CF_STATE="${YELLOW}inactive${NC}"; CF_HEALTH="${YELLOW}(manual/not installed)${NC}"
  fi
  echo -e "  Cloudflare Tunnel    ${CF_STATE}    ${CF_HEALTH}"

  # botsv3 event count
  if splunk_healthy; then
    EVENT_COUNT=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/services/search/jobs" \
      -d "search=search index=botsv3 | stats count&output_mode=json&exec_mode=oneshot" \
      | python3 -c "import sys,json; r=json.load(sys.stdin).get('results',[]); print(r[0]['count'] if r else '0')" 2>/dev/null || echo "?")
    echo
    echo -e "  botsv3 events: ${EVENT_COUNT}"
  fi

  # HF token
  echo -e "  HF_TOKEN: $([[ -n "${HF_TOKEN:-}" ]] && echo -e "${GREEN}set${NC}" || echo -e "${YELLOW}not set (Foundation-Sec disabled)${NC}")"

  echo
  VM_IP=$(hostname -I | awk '{print $1}')
  echo "  Splunk Web:    http://${VM_IP}:8001"
  echo "  AEC Dashboard: http://${VM_IP}:${AEC_PORT}"
  PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-aec3.accessquint.com}"
  echo "  Public URL:    https://${PUBLIC_DOMAIN}"
  echo
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_start() {
  h1 "Starting AEC Stack"

  # Splunk
  if splunk_running; then
    ok "Splunk container already running"
  else
    info "Starting Splunk container..."
    docker start aec-splunk 2>/dev/null || \
      docker compose -f "$REPO_DIR/infra/docker-compose.mcp.yml" up -d splunk
    info "Waiting for Splunk to respond..."
    for i in $(seq 1 60); do
      splunk_healthy && break
      sleep 5; printf "."
    done
    echo
    splunk_healthy && ok "Splunk is ready" || warn "Splunk may still be starting"
  fi

  # AEC web
  info "Starting AEC web service..."
  sudo systemctl start aec-web
  sleep 3
  web_healthy && ok "AEC web running on port ${AEC_PORT}" || warn "AEC web not responding yet"

  # Cloudflare
  if systemctl list-unit-files cloudflared.service &>/dev/null 2>&1; then
    sudo systemctl start cloudflared 2>/dev/null || true
    tunnel_running && ok "Cloudflare Tunnel running" || warn "Cloudflare Tunnel not active"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_stop() {
  h1 "Stopping AEC Stack"
  info "Stopping AEC web service..."
  sudo systemctl stop aec-web 2>/dev/null || true; ok "AEC web stopped"

  info "Stopping Splunk container..."
  docker stop aec-splunk 2>/dev/null || true; ok "Splunk stopped"

  if systemctl list-unit-files cloudflared.service &>/dev/null 2>&1; then
    sudo systemctl stop cloudflared 2>/dev/null || true; ok "Cloudflare Tunnel stopped"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_restart() {
  h1 "Restarting AEC Stack"

  info "Restarting Splunk container..."
  docker restart aec-splunk 2>/dev/null || true

  info "Restarting AEC web service..."
  sudo systemctl restart aec-web
  sleep 4
  web_healthy && ok "AEC web restarted on port ${AEC_PORT}" || warn "AEC web not responding yet — try: manage.sh logs-web"

  if systemctl list-unit-files cloudflared.service &>/dev/null 2>&1; then
    sudo systemctl restart cloudflared 2>/dev/null || true
    tunnel_running && ok "Cloudflare Tunnel restarted" || warn "Cloudflare Tunnel not active"
  fi

  info "Waiting for Splunk..."
  for i in $(seq 1 40); do splunk_healthy && break; sleep 5; printf "."; done; echo
  splunk_healthy && ok "Splunk ready" || warn "Splunk still starting"
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_logs() {
  h1 "Live logs (Ctrl+C to stop)"
  sudo journalctl -u aec-web -u cloudflared -f --no-pager 2>/dev/null || \
  sudo journalctl -u aec-web -f --no-pager
}

cmd_logs_web() {
  sudo journalctl -u aec-web -f --no-pager
}

cmd_logs_splunk() {
  docker logs -f aec-splunk 2>&1
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_update() {
  h1 "Updating AEC"

  info "Pulling latest code..."
  git -C "$REPO_DIR" pull --ff-only || warn "git pull failed — continuing"

  info "Reinstalling Python package..."
  source "$REPO_DIR/.venv/bin/activate"
  pip install -q -e ".[web]"

  info "Restarting services..."
  cmd_restart

  ok "Update complete"
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_verify() {
  h1 "Smoke Test"

  source "$REPO_DIR/.venv/bin/activate"

  # aec_demo sample run
  info "Running aec_demo --sample soc2-cc61..."
  if timeout 120 aec_demo --sample soc2-cc61 2>/dev/null | grep -q "CONSENSUS"; then
    VERDICT=$(timeout 120 aec_demo --sample soc2-cc61 2>/dev/null | grep "CONSENSUS" | head -1 || echo "see output")
    ok "Panel debate completed: ${VERDICT}"
  else
    warn "Panel debate did not complete — check API keys in .env"
  fi

  # Verify latest artifacts
  LATEST_XLSX=$(ls -t "$REPO_DIR/out/"gap_report_*.xlsx 2>/dev/null | head -1 || true)
  LATEST_TRAIL=$(ls -t "$REPO_DIR/out/"audit_trail_*.jsonl 2>/dev/null | head -1 || true)
  if [[ -n "$LATEST_XLSX" && -n "$LATEST_TRAIL" ]]; then
    if aec verify "$LATEST_XLSX" --trail "$LATEST_TRAIL" 2>/dev/null | grep -q "verified"; then
      ok "Merkle chain integrity: verified"
    else
      warn "Chain verification failed"
    fi
  else
    skip "No artifacts to verify yet — run aec_demo first"
  fi

  # Web dashboard
  web_healthy && ok "Web dashboard: responding" || err "Web dashboard: not responding"

  # Splunk live query
  if splunk_healthy; then
    COUNT=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/services/search/jobs" \
      -d "search=search index=botsv3 mfa_used=false | stats count&output_mode=json&exec_mode=oneshot" \
      | python3 -c "import sys,json; r=json.load(sys.stdin).get('results',[]); print(r[0]['count'] if r else '0')" 2>/dev/null || echo "0")
    ok "Live Splunk query: ${COUNT} MFA bypass events in botsv3"
  else
    warn "Splunk not healthy — skipping live query test"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
cmd_shell() {
  info "Activating venv. Type 'exit' to return."
  source "$REPO_DIR/.venv/bin/activate"
  exec bash --rcfile <(echo "source '$REPO_DIR/.venv/bin/activate'; cd '$REPO_DIR'; export \$(grep -v '^#' .env | xargs) 2>/dev/null; PS1='(aec) \u@\h:\w\$ '")
}

# ─────────────────────────────────────────────────────────────────────────────
# Dispatch
# Install the auditcompiler Splunk app + seed the Compliance Posture dashboard.
cmd_install_app() { bash "$REPO_DIR/scripts/install_splunk_app.sh" "${2:-}"; }

# Install / restore the real BOTS v3 dataset (cached on host, idempotent).
cmd_install_bots() { bash "$REPO_DIR/scripts/install_botsv3.sh" "${2:-}"; }

# Install MLTK + Python for Scientific Computing (Splunk's ML engine) from
# local .tgz packages, enabling MLTK fit/apply in the splunk_ml_anomaly node.
cmd_install_mltk() { bash "$REPO_DIR/scripts/install_mltk.sh" "${2:-}" "${3:-}"; }

# ─────────────────────────────────────────────────────────────────────────────
CMD="${1:-status}"

case "$CMD" in
  status)       cmd_status ;;
  start)        cmd_start ;;
  stop)         cmd_stop ;;
  restart)      cmd_restart ;;
  logs)         cmd_logs ;;
  logs-web)     cmd_logs_web ;;
  logs-splunk)  cmd_logs_splunk ;;
  update)       cmd_update ;;
  verify)       cmd_verify ;;
  install-app)  cmd_install_app "$@" ;;
  install-bots) cmd_install_bots "$@" ;;
  install-mltk) cmd_install_mltk "$@" ;;
  shell)        cmd_shell ;;
  *)
    echo "Usage: $0 {status|start|stop|restart|logs|logs-web|logs-splunk|update|verify|install-app|install-bots|install-mltk|shell}"
    exit 1 ;;
esac
