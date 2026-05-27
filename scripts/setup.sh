#!/usr/bin/env bash
# =============================================================================
# Audit Evidence Auto-Compiler — One-Shot VM Setup Script
#
# Tested on: Ubuntu 22.04 / 24.04 / 26.04 (Vultr, DigitalOcean, AWS)
# Safe to re-run — every step is idempotent.
#
# Usage:
#   bash scripts/setup.sh [OPTIONS]
#
# Options:
#   --license  /path/to/Splunk.License   Apply Splunk developer license
#   --password <password>                Splunk admin password (default: changeme123)
#   --domain   <hostname>                Public hostname (default: aec3.accessquint.com)
#   --hf-token <token>                   HuggingFace token for Foundation-Sec-8B
#   --cf-token <token>                   Cloudflare Tunnel token (skipped if already running)
#   --gh-user  <username>                GitHub username (avoids interactive prompt)
#   --gh-token <token>                   GitHub personal access token (repo scope)
#
# Config file (takes effect before args, args override):
#   Create ~/.aec-config with any of:
#     GH_USER=sandhipveera
#     GH_TOKEN=ghp_xxx
#     HF_TOKEN=hf_xxx
#     CF_TOKEN=eyJ...
#     SPLUNK_PASSWORD=changeme123
#     PUBLIC_DOMAIN=aec3.accessquint.com
#     LICENSE_FILE=/home/veera/Splunk.License
#
# After setup, manage the stack with:
#   bash scripts/manage.sh status|start|stop|restart|logs|update|verify
# =============================================================================

set -euo pipefail

# ── Colours (defined early so config-load warnings can use them) ───────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
ok()    { echo -e "${GREEN}✓${NC} $*"; }
warn()  { echo -e "${YELLOW}⚠${NC}  $*"; }
info()  { echo -e "${CYAN}→${NC} $*"; }
die()   { echo -e "${RED}✗  FATAL:${NC} $*"; exit 1; }
h1()    { echo; echo -e "\033[1;36m━━━  $*  ━━━\033[0m"; echo; }
skip()  { echo -e "  ${YELLOW}(skipped)${NC} $*"; }

# ── Config file (~/.aec-config) ───────────────────────────────────────────────
AEC_CONFIG="${HOME}/.aec-config"
if [[ -f "$AEC_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$AEC_CONFIG"
  ok "Loaded config from $AEC_CONFIG"
fi

# ── Defaults (config file values take precedence over these) ──────────────────
REPO_URL="https://github.com/sandhipveera/audit-evidence-compiler"
REPO_DIR="${HOME}/audit-evidence-compiler"
SPLUNK_PASSWORD="${SPLUNK_PASSWORD:-changeme123}"
SPLUNK_PORT_WEB=8001
SPLUNK_PORT_MGMT=8089
SPLUNK_PORT_HEC=8088
AEC_PORT=8000
PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-aec3.accessquint.com}"
LICENSE_FILE="${LICENSE_FILE:-}"
CF_TOKEN="${CF_TOKEN:-}"
HF_TOKEN="${HF_TOKEN:-}"
GH_USER="${GH_USER:-}"
GH_TOKEN="${GH_TOKEN:-}"

# ── Args (override config file values) ────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --license)  LICENSE_FILE="$2";    shift 2 ;;
    --password) SPLUNK_PASSWORD="$2"; shift 2 ;;
    --domain)   PUBLIC_DOMAIN="$2";   shift 2 ;;
    --hf-token) HF_TOKEN="$2";        shift 2 ;;
    --cf-token) CF_TOKEN="$2";        shift 2 ;;
    --gh-user)  GH_USER="$2";         shift 2 ;;
    --gh-token) GH_TOKEN="$2";        shift 2 ;;
    -h|--help)
      grep '^#' "$0" | grep -v '#!/' | sed 's/^# \{0,2\}//'
      exit 0 ;;
    *) warn "Unknown argument: $1 (ignored)"; shift ;;
  esac
done

# ── Wire GitHub credentials into git so no prompts ever appear ────────────────
if [[ -n "$GH_TOKEN" ]]; then
  # Store credentials in git's credential store (survives re-runs)
  git config --global credential.helper store
  GH_USER_EFFECTIVE="${GH_USER:-git}"
  # Write to credential store
  echo "https://${GH_USER_EFFECTIVE}:${GH_TOKEN}@github.com" > "${HOME}/.git-credentials"
  chmod 600 "${HOME}/.git-credentials"
  # Also embed in REPO_URL for the initial clone (belt + suspenders)
  REPO_URL="https://${GH_USER_EFFECTIVE}:${GH_TOKEN}@github.com/sandhipveera/audit-evidence-compiler"
  ok "GitHub credentials configured (no prompts)"
elif [[ -z "$GH_USER" ]]; then
  warn "No GitHub credentials in config — git clone/pull may prompt for username/password"
  warn "Add to ~/.aec-config:  GH_USER=sandhipveera  GH_TOKEN=ghp_xxx"
fi

# ── Save current config to ~/.aec-config (update with any new values) ─────────
save_config() {
  cat > "$AEC_CONFIG" << CONF
# AEC config — auto-updated by setup.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ)
# Edit this file to avoid re-entering credentials on every run.
GH_USER=${GH_USER:-}
GH_TOKEN=${GH_TOKEN:-}
HF_TOKEN=${HF_TOKEN:-}
CF_TOKEN=${CF_TOKEN:-}
SPLUNK_PASSWORD=${SPLUNK_PASSWORD}
PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
LICENSE_FILE=${LICENSE_FILE:-}
CONF
  chmod 600 "$AEC_CONFIG"
  ok "Saved config to $AEC_CONFIG"
}
save_config

AEC_WEBHOOK_URL="https://${PUBLIC_DOMAIN}/api/incident"

# ── Helper: wait for Splunk REST to respond ────────────────────────────────────
wait_for_splunk() {
  local max="${1:-90}"
  info "Waiting for Splunk REST API (up to ${max}s)..."
  for i in $(seq 1 "$max"); do
    if curl -sk -u "admin:${SPLUNK_PASSWORD}" \
        "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
        2>/dev/null | grep -q '"version"'; then
      ok "Splunk is responding"
      return 0
    fi
    sleep 3
    printf "."
  done
  echo
  return 1
}

# ── Helper: docker exec as splunk user ────────────────────────────────────────
sp() { docker exec -u splunk aec-splunk /opt/splunk/bin/splunk "$@" 2>&1 | grep -v WARNING || true; }

# ─────────────────────────────────────────────────────────────────────────────
# 1. System dependencies
# ─────────────────────────────────────────────────────────────────────────────
h1 "1 / 11  System dependencies"

# Docker
if ! command -v docker &>/dev/null; then
  info "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  warn "Docker installed. You may need to log out/in for group membership."
  warn "If the next step fails with 'permission denied', run: newgrp docker"
else
  ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"
fi

# Ensure current user can talk to Docker without sudo
if ! docker info &>/dev/null 2>&1; then
  # Try with newgrp trick
  exec sg docker "$0 $*" || die "Cannot access Docker. Run: sudo usermod -aG docker $USER && newgrp docker"
fi

# Python + venv
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "3")
sudo apt-get install -y -qq \
  python3-venv python3-pip \
  "python${PYTHON_VER}-venv" 2>/dev/null || \
sudo apt-get install -y -qq python3-venv python3-pip 2>/dev/null || true

ok "Python $(python3 --version)"

# ─────────────────────────────────────────────────────────────────────────────
# 2. Repository
# ─────────────────────────────────────────────────────────────────────────────
h1 "2 / 11  Repository"

if [[ -d "$REPO_DIR/.git" ]]; then
  ok "Repo exists — pulling latest"
  git -C "$REPO_DIR" pull --ff-only || warn "git pull failed — continuing with existing code"
else
  info "Cloning $REPO_URL"
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"

# ─────────────────────────────────────────────────────────────────────────────
# 3. Python venv
# ─────────────────────────────────────────────────────────────────────────────
h1 "3 / 11  Python venv"

if [[ ! -f ".venv/bin/activate" ]]; then
  info "Creating virtual environment..."
  python3 -m venv .venv || die "Failed to create venv. Run: sudo apt install python${PYTHON_VER}-venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q --upgrade pip
pip install -q -e ".[web]" || die "pip install failed — check pyproject.toml"
ok "AEC installed in .venv"

# ─────────────────────────────────────────────────────────────────────────────
# 4. Splunk Enterprise
# ─────────────────────────────────────────────────────────────────────────────
h1 "4 / 11  Splunk Enterprise"

# Check if already running and healthy
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^aec-splunk$"; then
  if curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
      2>/dev/null | grep -q '"version"'; then
    SPLUNK_VER=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'][0]['content']['version'])" 2>/dev/null || echo "unknown")
    ok "Splunk ${SPLUNK_VER} already running — skipping start"
  else
    warn "Container exists but not responding — restarting..."
    docker restart aec-splunk
    wait_for_splunk 120 || die "Splunk failed to start. Check: docker logs aec-splunk"
  fi
else
  # Write compose file
  cat > "$REPO_DIR/infra/docker-compose.mcp.yml" << COMPOSE
services:
  splunk:
    image: splunk/splunk:latest
    container_name: aec-splunk
    ports:
      - "${SPLUNK_PORT_WEB}:8000"
      - "${SPLUNK_PORT_HEC}:8088"
      - "${SPLUNK_PORT_MGMT}:8089"
    environment:
      SPLUNK_START_ARGS: "--accept-license"
      SPLUNK_GENERAL_TERMS: "--accept-sgt-current-at-splunk-com"
      SPLUNK_PASSWORD: "${SPLUNK_PASSWORD}"
    volumes:
      - splunk-data:/opt/splunk/var
    restart: unless-stopped

volumes:
  splunk-data:
COMPOSE

  # Remove dead container if present
  docker rm -f aec-splunk 2>/dev/null || true

  info "Starting Splunk (first boot takes 3-5 min)..."
  docker compose -f "$REPO_DIR/infra/docker-compose.mcp.yml" up -d splunk

  wait_for_splunk 300 || die "Splunk failed to start after 5 min. Check: docker logs aec-splunk"

  SPLUNK_VER=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
    "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'][0]['content']['version'])" 2>/dev/null || echo "unknown")
  ok "Splunk ${SPLUNK_VER} started"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. Developer license
# ─────────────────────────────────────────────────────────────────────────────
h1 "5 / 11  Splunk license"

if [[ -n "$LICENSE_FILE" && -f "$LICENSE_FILE" ]]; then
  # Check if already enterprise
  LIC_TYPE=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
    "https://localhost:${SPLUNK_PORT_MGMT}/services/licenser/licenses?output_mode=json" \
    | python3 -c "import sys,json; ls=json.load(sys.stdin)['entry']; print(ls[0]['content'].get('type','free') if ls else 'free')" 2>/dev/null || echo "free")

  if [[ "$LIC_TYPE" == "enterprise" || "$LIC_TYPE" == "download-trial" ]]; then
    ok "Enterprise/developer license already active (type: ${LIC_TYPE})"
  else
    docker cp "$LICENSE_FILE" aec-splunk:/tmp/splunk.lic
    sp add licenses /tmp/splunk.lic -auth "admin:${SPLUNK_PASSWORD}" | grep -i "license\|error" || true
    ok "License applied — restarting Splunk..."
    sp restart -auth "admin:${SPLUNK_PASSWORD}" > /dev/null 2>&1 &
    sleep 20
    wait_for_splunk 120 || warn "Splunk slow to restart — continuing anyway"
    ok "Splunk restarted with developer license"
  fi
else
  skip "No license file — running on free tier (500 MB/day)"
  [[ -n "$LICENSE_FILE" ]] && warn "License file not found: $LICENSE_FILE"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 6. BOTS v3 index + synthetic MFA data
# ─────────────────────────────────────────────────────────────────────────────
h1 "6 / 11  BOTS v3 index + MFA data"

# Create index (idempotent)
sp add index botsv3 -auth "admin:${SPLUNK_PASSWORD}" 2>&1 | grep -iv "already exists\|WARNING" || true
ok "botsv3 index ready"

# Check existing event count
EXISTING=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  "https://localhost:${SPLUNK_PORT_MGMT}/services/search/jobs" \
  -d "search=search index=botsv3 | stats count&output_mode=json&exec_mode=oneshot" \
  | python3 -c "import sys,json; r=json.load(sys.stdin).get('results',[]); print(int(r[0]['count']) if r else 0)" 2>/dev/null || echo "0")

if [[ "$EXISTING" -ge 100 ]]; then
  ok "botsv3 already has ${EXISTING} events — skipping ingestion"
else
  info "Ingesting synthetic MFA data via HEC (${EXISTING} events found, need ≥100)..."

  # Enable HEC
  curl -sk -u "admin:${SPLUNK_PASSWORD}" \
    -X POST "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http/http" \
    -d "disabled=0" > /dev/null 2>&1 || true

  # Create or fetch HEC token
  HEC_RESP=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
    -X POST "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http" \
    -d "name=aec-ingest&index=botsv3&sourcetype=o365:management:activity&output_mode=json" 2>/dev/null || echo "{}")

  HEC_TOKEN=$(echo "$HEC_RESP" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('entry',[{}])[0].get('content',{}).get('token',''))" 2>/dev/null || true)

  if [[ -z "$HEC_TOKEN" ]]; then
    HEC_TOKEN=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http/http%3A%2F%2Faec-ingest?output_mode=json" \
      | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'][0]['content']['token'])" 2>/dev/null || true)
  fi

  if [[ -z "$HEC_TOKEN" ]]; then
    warn "Could not get HEC token — skipping synthetic data ingestion"
    warn "Run manually: curl -sk -u admin:${SPLUNK_PASSWORD} https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http/http%3A%2F%2Faec-ingest?output_mode=json"
  else
    ok "HEC token: ${HEC_TOKEN:0:8}..."
    python3 - << PYEOF
import json, datetime, random, urllib.request, ssl, sys

HEC_TOKEN = "${HEC_TOKEN}"
users = ["alice@contoso.com","bob@contoso.com","charlie@contoso.com",
         "diana@contoso.com","eve@contoso.com",
         "svc-backup@contoso.com","svc-deploy@contoso.com","svc-monitor@contoso.com"]
ips   = ["192.168.1.10","10.0.0.55","203.0.113.42","198.51.100.7","172.16.0.8"]
base  = datetime.datetime(2018, 8, 1, 8, 0, 0)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

sent = errors = 0
for i in range(500):
    ts   = base + datetime.timedelta(minutes=i*2)
    user = random.choice(users)
    mfa  = "false" if user.startswith("svc-") or random.random() < 0.17 else "true"
    payload = json.dumps({
        "time": ts.timestamp(), "index": "botsv3",
        "sourcetype": "o365:management:activity",
        "event": {"user": user, "action": "Login", "mfa_used": mfa,
                  "src_ip": random.choice(ips), "result": "Success",
                  "ClientIP": random.choice(ips)}
    }).encode()
    req = urllib.request.Request(
        "https://localhost:${SPLUNK_PORT_HEC}/services/collector/event",
        data=payload, headers={"Authorization": f"Splunk {HEC_TOKEN}"})
    try:
        urllib.request.urlopen(req, context=ctx)
        sent += 1
    except Exception as e:
        errors += 1
        if errors > 5:
            print(f"Too many HEC errors ({e}) — stopping early", file=sys.stderr)
            break

print(f"Sent {sent} events via HEC ({errors} errors)")
PYEOF
    ok "Synthetic BOTS v3 data ingested"
    HEC_TOKEN_SAVED="$HEC_TOKEN"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 7. REST token + .env
# ─────────────────────────────────────────────────────────────────────────────
h1 "7 / 11  REST token + .env"

SPLUNK_TOKEN=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  -X POST "https://localhost:${SPLUNK_PORT_MGMT}/services/auth/login" \
  -d "username=admin&password=${SPLUNK_PASSWORD}&output_mode=json" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sessionKey'])" 2>/dev/null || true)

[[ -z "$SPLUNK_TOKEN" ]] && warn "Could not get Splunk session key — live queries may fail"

cat > "$REPO_DIR/.env" << ENV
# Auto-generated by scripts/setup.sh — $(date -u +%Y-%m-%dT%H:%M:%SZ)
SPLUNK_HOST=https://localhost:${SPLUNK_PORT_MGMT}
SPLUNK_TOKEN=${SPLUNK_TOKEN:-}
SPLUNK_PASSWORD=${SPLUNK_PASSWORD}
SPLUNK_VERIFY_SSL=false
AEC_SPLUNK_MCP_SERVER=rest
HEC_TOKEN=${HEC_TOKEN_SAVED:-${HEC_TOKEN:-}}
HF_TOKEN=${HF_TOKEN:-}
PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
PYTHONWARNINGS=ignore::urllib3.exceptions.InsecureRequestWarning
ENV

ok ".env written"

# ─────────────────────────────────────────────────────────────────────────────
# 8. Splunk alert → AEC webhook
# ─────────────────────────────────────────────────────────────────────────────
h1 "8 / 11  Splunk alert → AEC webhook"

ALERT_RESP=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  -X POST "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/search/saved/searches" \
  -d "name=AEC - MFA Bypass Detected" \
  -d "search=index=botsv3 sourcetype=\"o365:management:activity\" mfa_used=false | stats count by user | where count > 2" \
  -d "cron_schedule=*/5 * * * *" \
  -d "is_scheduled=1" \
  -d "alert_type=number of events" \
  -d "alert_comparator=greater than" \
  -d "alert_threshold=0" \
  -d "actions=webhook" \
  -d "action.webhook.param.url=${AEC_WEBHOOK_URL}" \
  -d "output_mode=json" 2>/dev/null || echo "{}")

if echo "$ALERT_RESP" | grep -q "already exists\|\"entry\""; then
  ok "Alert configured — fires every 5 min → ${AEC_WEBHOOK_URL}"
else
  warn "Alert setup uncertain — check Splunk Web: http://$(hostname -I | awk '{print $1}'):${SPLUNK_PORT_WEB}/en-US/manager/search/savedsearches"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. AEC web service (systemd)
# ─────────────────────────────────────────────────────────────────────────────
h1 "9 / 11  AEC web service (systemd)"

sudo tee /etc/systemd/system/aec-web.service > /dev/null << SERVICE
[Unit]
Description=Audit Evidence Compiler Web Dashboard
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=${USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${REPO_DIR}/.env
ExecStart=${REPO_DIR}/.venv/bin/uvicorn web.main:app --host 0.0.0.0 --port ${AEC_PORT} --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SERVICE

# Splunk Docker container auto-restart on VM reboot
sudo tee /etc/systemd/system/aec-splunk.service > /dev/null << SERVICE
[Unit]
Description=AEC Splunk Enterprise Container
After=docker.service network.target
Requires=docker.service

[Service]
Type=simple
User=${USER}
ExecStart=docker start -a aec-splunk
ExecStop=docker stop aec-splunk
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable aec-web aec-splunk
sudo systemctl restart aec-web
sleep 4

if curl -s --max-time 5 "http://localhost:${AEC_PORT}/api/controls" 2>/dev/null | grep -q "control_id"; then
  ok "AEC web dashboard running on port ${AEC_PORT}"
else
  warn "AEC web not responding yet"
  warn "Check logs: sudo journalctl -u aec-web -n 30 --no-pager"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 10. Cloudflare Tunnel
# ─────────────────────────────────────────────────────────────────────────────
h1 "10 / 11  Cloudflare Tunnel"

# Check if already running
if systemctl is-active --quiet cloudflared 2>/dev/null; then
  ok "cloudflared already running — skipping"
elif [[ -z "$CF_TOKEN" ]]; then
  skip "No --cf-token provided"
  info "If you have a token, run: sudo cloudflared service install <TOKEN>"
  info "Or set up manually at: https://one.dash.cloudflare.com → Networks → Tunnels"
else
  # Install cloudflared
  if ! command -v cloudflared &>/dev/null; then
    info "Installing cloudflared..."
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
      | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null 2>&1
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
      | sudo tee /etc/apt/sources.list.d/cloudflared.list > /dev/null
    sudo apt-get update -qq && sudo apt-get install -y -qq cloudflared
    ok "cloudflared installed"
  fi

  # Uninstall any stale service first
  sudo cloudflared service uninstall 2>/dev/null || true

  # Try installing with token
  if sudo cloudflared service install "$CF_TOKEN" 2>/dev/null; then
    sudo systemctl enable cloudflared
    sudo systemctl restart cloudflared
    sleep 5
    if systemctl is-active --quiet cloudflared; then
      ok "Cloudflare Tunnel running → https://${PUBLIC_DOMAIN}"
    else
      warn "Tunnel service not active after install"
      sudo journalctl -u cloudflared -n 10 --no-pager || true
    fi
  else
    warn "cloudflared service install failed — token may be invalid or already used"
    warn "Manual steps:"
    warn "  1. Go to: https://one.dash.cloudflare.com → Networks → Tunnels"
    warn "  2. Create a new tunnel → copy the 'cloudflared service install <TOKEN>' command"
    warn "  3. Run: sudo cloudflared service install <TOKEN>"
    warn "  4. Configure public hostname: ${PUBLIC_DOMAIN} → http://localhost:${AEC_PORT}"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 11. Smoke test
# ─────────────────────────────────────────────────────────────────────────────
h1 "11 / 11  Smoke test"

source "$REPO_DIR/.venv/bin/activate"

# Count MFA bypass events
MFA_COUNT=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  "https://localhost:${SPLUNK_PORT_MGMT}/services/search/jobs" \
  -d "search=search index=botsv3 mfa_used=false | stats count&output_mode=json&exec_mode=oneshot" \
  | python3 -c "import sys,json; r=json.load(sys.stdin).get('results',[]); print(r[0]['count'] if r else '0')" 2>/dev/null || echo "0")

# Check web dashboard
WEB_OK=$(curl -s --max-time 5 "http://localhost:${AEC_PORT}/api/controls" 2>/dev/null | grep -c "control_id" || echo "0")

# Check aec_demo
DEMO_OK=$(aec_demo --version 2>/dev/null && echo "ok" || echo "fail")

# Check HF token
HF_STATUS="not set"
[[ -n "$HF_TOKEN" ]] && HF_STATUS="set (Foundation-Sec-8B enabled)"

echo
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo
VM_IP=$(hostname -I | awk '{print $1}')
echo "  Splunk Web:      http://${VM_IP}:${SPLUNK_PORT_WEB}  (admin / ${SPLUNK_PASSWORD})"
echo "  AEC Dashboard:   http://${VM_IP}:${AEC_PORT}"
echo "  Public URL:      https://${PUBLIC_DOMAIN}"
echo "  MFA bypass events in botsv3: ${MFA_COUNT}"
echo "  Web dashboard:   $([[ "$WEB_OK" -gt 0 ]] && echo '✓ responding' || echo '⚠ not responding')"
echo "  aec_demo:        ${DEMO_OK}"
echo "  HF_TOKEN:        ${HF_STATUS}"
echo
echo "  Manage the stack:"
echo "    bash ${REPO_DIR}/scripts/manage.sh status"
echo "    bash ${REPO_DIR}/scripts/manage.sh restart"
echo "    bash ${REPO_DIR}/scripts/manage.sh logs"
echo
echo "  Quick test:"
echo "    source ${REPO_DIR}/.venv/bin/activate"
echo "    aec_demo --sample soc2-cc61"
echo
