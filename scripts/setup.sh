#!/usr/bin/env bash
# =============================================================================
# Audit Evidence Auto-Compiler — One-Shot VM Setup Script
#
# Tested on: Ubuntu 22.04 / 24.04 / 26.04 (Vultr, DigitalOcean, AWS)
# Run as:    bash scripts/setup.sh [OPTIONS]
#
# Options:
#   --license  /path/to/Splunk.License   Apply Splunk developer license
#   --password <splunk-password>         Splunk admin password (default: changeme123)
#   --webhook  <url>                     AEC incident webhook URL
#   --cf-token <token>                   Cloudflare Tunnel token (from dash.cloudflare.com)
#   --hf-token <token>                   HuggingFace token for Foundation-Sec-8B
#
# What it does:
#   1.  Installs Docker, Python, git
#   2.  Clones / updates the repo
#   3.  Creates Python venv + installs AEC
#   4.  Starts Splunk Enterprise
#   5.  Applies developer license (if provided)
#   6.  Creates botsv3 index + ingests synthetic MFA data via HEC
#   7.  Creates Splunk REST token + wires into .env
#   8.  Creates MFA bypass scheduled alert → AEC webhook
#   9.  Starts AEC web service (systemd)
#   10. Sets up Cloudflare Tunnel (if --cf-token provided)
#   11. Runs smoke test
# =============================================================================

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
REPO_URL="https://github.com/sandhipveera/audit-evidence-compiler"
REPO_DIR="${HOME}/audit-evidence-compiler"
SPLUNK_PASSWORD="${SPLUNK_PASSWORD:-changeme123}"
SPLUNK_PORT_WEB=8001
SPLUNK_PORT_MGMT=8089
SPLUNK_PORT_HEC=8088
AEC_PORT=8000
AEC_WEBHOOK_URL="${AEC_WEBHOOK_URL:-https://aec.accessquint.com/api/incident}"
LICENSE_FILE=""
CF_TOKEN=""
HF_TOKEN=""

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}⚠${NC}  $*"; }
die()  { echo -e "${RED}✗${NC} $*"; exit 1; }
h1()   { echo; echo -e "\033[1;36m━━━  $*  ━━━\033[0m"; echo; }

# ── Args ──────────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case $1 in
    --license)  LICENSE_FILE="$2";    shift 2 ;;
    --password) SPLUNK_PASSWORD="$2"; shift 2 ;;
    --webhook)  AEC_WEBHOOK_URL="$2"; shift 2 ;;
    --cf-token) CF_TOKEN="$2";        shift 2 ;;
    --hf-token) HF_TOKEN="$2";        shift 2 ;;
    *) warn "Unknown arg: $1"; shift ;;
  esac
done

# ── 1. System dependencies ────────────────────────────────────────────────────
h1 "1 / 10  System dependencies"

if ! command -v docker &>/dev/null; then
  ok "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  warn "Added $USER to docker group. If docker commands fail, log out and back in."
  # Use sg to run remaining docker commands in the new group without re-login
  DOCKER="sg docker -c docker"
else
  ok "Docker already installed: $(docker --version)"
  DOCKER="docker"
fi

if ! command -v python3 &>/dev/null; then
  ok "Installing Python..."
  sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv
else
  # Ensure venv and pip are present even if python3 is already installed
  sudo apt-get install -y -qq python3-venv python3-pip 2>/dev/null || true
fi

if ! command -v git &>/dev/null; then
  sudo apt-get update -qq && sudo apt-get install -y -qq git
fi

ok "System deps ready"

# ── 2. Repo ───────────────────────────────────────────────────────────────────
h1 "2 / 10  Repository"

if [[ -d "$REPO_DIR/.git" ]]; then
  ok "Repo exists — pulling latest"
  git -C "$REPO_DIR" pull --ff-only
else
  ok "Cloning $REPO_URL"
  git clone "$REPO_URL" "$REPO_DIR"
fi

# ── 3. Python venv ────────────────────────────────────────────────────────────
h1 "3 / 10  Python venv"

cd "$REPO_DIR"

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  ok "Created .venv"
fi

source .venv/bin/activate

pip install -q --upgrade pip
pip install -q -e ".[web]"
ok "AEC installed: $(aec_demo --version 2>/dev/null || echo 'ok')"

# ── 4. Write docker-compose ───────────────────────────────────────────────────
h1 "4 / 10  Docker Compose"

cat > "$REPO_DIR/infra/docker-compose.mcp.yml" << EOF
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
    healthcheck:
      test: ["CMD", "/opt/splunk/bin/splunk", "status"]
      interval: 30s
      timeout: 10s
      retries: 20
      start_period: 180s

volumes:
  splunk-data:
EOF

ok "docker-compose.mcp.yml written"

# ── 5. Start Splunk ───────────────────────────────────────────────────────────
h1 "5 / 10  Starting Splunk Enterprise"

# Stop any existing container with same name
docker stop aec-splunk 2>/dev/null || true
docker rm   aec-splunk 2>/dev/null || true

docker compose -f "$REPO_DIR/infra/docker-compose.mcp.yml" up -d splunk
ok "Container started — waiting for Splunk to be ready (up to 5 min)..."

READY=false
for i in $(seq 1 60); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' aec-splunk 2>/dev/null || echo "unknown")
  if [[ "$STATUS" == "healthy" ]]; then
    READY=true
    break
  fi
  # Also check via REST
  if curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
      2>/dev/null | grep -q '"version"'; then
    READY=true
    break
  fi
  sleep 5
  printf "."
done
echo

if [[ "$READY" != "true" ]]; then
  warn "Splunk health check timed out — checking logs..."
  docker logs aec-splunk 2>&1 | tail -10
  die "Splunk did not start in time. Check logs above."
fi

SPLUNK_VER=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'][0]['content']['version'])" 2>/dev/null || echo "unknown")
ok "Splunk $SPLUNK_VER is ready"

# ── 6. Apply developer license ────────────────────────────────────────────────
h1 "6 / 10  License"

if [[ -n "$LICENSE_FILE" && -f "$LICENSE_FILE" ]]; then
  docker cp "$LICENSE_FILE" aec-splunk:/tmp/splunk.lic
  docker exec -u splunk aec-splunk \
    /opt/splunk/bin/splunk add licenses /tmp/splunk.lic \
    -auth "admin:${SPLUNK_PASSWORD}" 2>&1 | grep -v WARNING || true
  ok "Developer license applied — restarting Splunk..."
  docker exec -u splunk aec-splunk \
    /opt/splunk/bin/splunk restart 2>/dev/null &
  sleep 30
  # Wait for restart
  for i in $(seq 1 30); do
    curl -sk -u "admin:${SPLUNK_PASSWORD}" \
      "https://localhost:${SPLUNK_PORT_MGMT}/services/server/info?output_mode=json" \
      2>/dev/null | grep -q '"version"' && break
    sleep 5
  done
  ok "Splunk restarted with developer license"
else
  warn "No license file provided — running on free license (500MB/day limit)"
  warn "Re-run with: bash scripts/setup.sh --license /path/to/Splunk.License"
fi

# ── 7. Create botsv3 index + ingest MFA data ─────────────────────────────────
h1 "7 / 10  BOTS v3 index + synthetic MFA data"

# Create index
docker exec -u splunk aec-splunk \
  /opt/splunk/bin/splunk add index botsv3 \
  -auth "admin:${SPLUNK_PASSWORD}" 2>&1 | grep -v WARNING || true
ok "botsv3 index ready"

# Enable HEC
curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  -X POST "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http/http" \
  -d "disabled=0" > /dev/null

# Create or fetch HEC token
HEC_RESPONSE=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  -X POST "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http" \
  -d "name=aec-ingest&index=botsv3&sourcetype=o365:management:activity&output_mode=json")

HEC_TOKEN=$(echo "$HEC_RESPONSE" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d['entry'][0]['content']['token'])" 2>/dev/null || true)

if [[ -z "$HEC_TOKEN" ]]; then
  # Already exists — fetch it
  HEC_TOKEN=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
    "https://localhost:${SPLUNK_PORT_MGMT}/servicesNS/admin/splunk_httpinput/data/inputs/http/http%3A%2F%2Faec-ingest?output_mode=json" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['entry'][0]['content']['token'])" 2>/dev/null || true)
fi

if [[ -z "$HEC_TOKEN" ]]; then
  warn "Could not create/fetch HEC token — skipping data ingestion"
else
  ok "HEC token: ${HEC_TOKEN:0:8}..."

  # Ingest 500 synthetic MFA events
  python3 - << PYEOF
import json, datetime, random, urllib.request, ssl, sys

HEC_TOKEN = "${HEC_TOKEN}"
users = ["alice@contoso.com", "bob@contoso.com", "charlie@contoso.com",
         "diana@contoso.com", "svc-backup@contoso.com",
         "svc-deploy@contoso.com", "svc-monitor@contoso.com"]
ips   = ["192.168.1.10","10.0.0.55","203.0.113.42","198.51.100.7","172.16.0.8"]
base  = datetime.datetime(2018, 8, 1, 8, 0, 0)

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

sent = 0
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
        print(f"HEC error at event {i}: {e}", file=sys.stderr)
        break

print(f"Ingested {sent} synthetic MFA events into botsv3")
PYEOF
  ok "Synthetic BOTS v3 data ingested"
fi

# ── 8. Create REST API token + write .env ─────────────────────────────────────
h1 "8 / 10  REST token + .env"

SPLUNK_TOKEN=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  -X POST "https://localhost:${SPLUNK_PORT_MGMT}/services/auth/login" \
  -d "username=admin&password=${SPLUNK_PASSWORD}&output_mode=json" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['sessionKey'])" 2>/dev/null || true)

cat > "$REPO_DIR/.env" << ENV
# Auto-generated by scripts/setup.sh
SPLUNK_HOST=https://localhost:${SPLUNK_PORT_MGMT}
SPLUNK_TOKEN=${SPLUNK_TOKEN}
SPLUNK_PASSWORD=${SPLUNK_PASSWORD}
SPLUNK_VERIFY_SSL=false
AEC_SPLUNK_MCP_SERVER=rest
HEC_TOKEN=${HEC_TOKEN:-}
HF_TOKEN=${HF_TOKEN:-}
PYTHONWARNINGS=ignore::urllib3.exceptions.InsecureRequestWarning
ENV

ok ".env written"

# ── 9. Create scheduled alert ─────────────────────────────────────────────────
h1 "9 / 10  Splunk alert → AEC webhook"

ALERT_RESULT=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
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
  -d "output_mode=json" 2>/dev/null)

if echo "$ALERT_RESULT" | grep -q "already exists\|entry"; then
  ok "Alert 'AEC - MFA Bypass Detected' configured (fires every 5 min → ${AEC_WEBHOOK_URL})"
else
  warn "Alert setup may have failed — check Splunk Web at http://$(hostname -I | awk '{print $1}'):${SPLUNK_PORT_WEB}"
fi

# ── 10. AEC web service ───────────────────────────────────────────────────────
h1 "10 / 10  AEC web service"

# Write systemd service
sudo tee /etc/systemd/system/aec-web.service > /dev/null << SERVICE
[Unit]
Description=Audit Evidence Compiler Web Dashboard
After=network.target

[Service]
Type=simple
User=${USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=-${REPO_DIR}/.env
ExecStart=${REPO_DIR}/.venv/bin/uvicorn web.main:app --host 0.0.0.0 --port ${AEC_PORT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable aec-web
sudo systemctl restart aec-web
sleep 3

if curl -s "http://localhost:${AEC_PORT}/api/controls" | grep -q "control_id"; then
  ok "AEC web dashboard running at http://$(hostname -I | awk '{print $1}'):${AEC_PORT}"
else
  warn "AEC web not responding yet — check: sudo journalctl -u aec-web -n 20"
fi

# ── 10. Cloudflare Tunnel ─────────────────────────────────────────────────────
h1 "10 / 11  Cloudflare Tunnel"

if [[ -z "$CF_TOKEN" ]]; then
  warn "No --cf-token provided — skipping Cloudflare Tunnel setup"
  warn "To add later: bash scripts/setup.sh --cf-token <token>"
  warn "Get token from: https://one.dash.cloudflare.com → Networks → Tunnels"
else
  # Install cloudflared if not present
  if ! command -v cloudflared &>/dev/null; then
    ok "Installing cloudflared..."
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
      | sudo tee /usr/share/keyrings/cloudflare-main.gpg > /dev/null
    echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' \
      | sudo tee /etc/apt/sources.list.d/cloudflared.list
    sudo apt-get update -qq && sudo apt-get install -y -qq cloudflared
    ok "cloudflared installed: $(cloudflared --version 2>&1 | head -1)"
  else
    ok "cloudflared already installed: $(cloudflared --version 2>&1 | head -1)"
  fi

  # Remove any existing tunnel service
  sudo cloudflared service uninstall 2>/dev/null || true

  # Install tunnel as systemd service using token
  sudo cloudflared service install "$CF_TOKEN"
  sudo systemctl enable cloudflared
  sudo systemctl restart cloudflared
  sleep 5

  if sudo systemctl is-active --quiet cloudflared; then
    ok "Cloudflare Tunnel active — aec.accessquint.com → localhost:${AEC_PORT}"
  else
    warn "Tunnel service not running — check: sudo journalctl -u cloudflared -n 20"
  fi
fi

# ── 11. Smoke test ────────────────────────────────────────────────────────────
h1 "Smoke test"

source .venv/bin/activate
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

EVENT_COUNT=$(curl -sk -u "admin:${SPLUNK_PASSWORD}" \
  "https://localhost:${SPLUNK_PORT_MGMT}/services/search/jobs" \
  -d "search=search index=botsv3 mfa_used=false | stats count&output_mode=json&exec_mode=oneshot" \
  | python3 -c "import sys,json; r=json.load(sys.stdin).get('results',[]); print(r[0]['count'] if r else '0')" 2>/dev/null || echo "0")

ok "botsv3 events with mfa_used=false: ${EVENT_COUNT}"

echo
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo
echo "  Splunk Web:      http://$(hostname -I | awk '{print $1}'):${SPLUNK_PORT_WEB}  (admin / ${SPLUNK_PASSWORD})"
echo "  AEC Dashboard:   http://$(hostname -I | awk '{print $1}'):${AEC_PORT}"
echo "  Splunk REST API: https://localhost:${SPLUNK_PORT_MGMT}"
if [[ -n "$CF_TOKEN" ]]; then
echo "  Public URL:      https://aec.accessquint.com  (via Cloudflare Tunnel)"
fi
echo
echo "  Quick test:"
echo "    source ${REPO_DIR}/.venv/bin/activate"
echo "    aec_demo --sample soc2-cc61"
echo
echo "  Live Splunk test:"
echo "    aec_demo --control CC6.1 --mcp rest"
echo
if [[ -z "$LICENSE_FILE" ]]; then
  echo -e "  ${YELLOW}⚠  Apply developer license for scheduled alerts:${NC}"
  echo "    bash scripts/setup.sh --license /path/to/Splunk.License"
  echo
fi
