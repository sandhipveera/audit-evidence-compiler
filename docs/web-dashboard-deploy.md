# Web Dashboard Deployment Runbook

## Overview

The Audit Evidence Compiler web dashboard is a FastAPI server that streams
panel debates live to any browser via WebSocket. No installation required
for the end user — just a URL.

**Live demo:** `https://aec.accessquint.com`

## Architecture

```
Browser ──── HTTPS ──── Caddy (reverse proxy + TLS) ──── FastAPI (uvicorn)
                                                              │
                                                       runs panel debate
                                                       streams via WebSocket
```

## Prerequisites

- Python 3.11+
- A Vultr VM (or any Linux server) with a public IP
- DNS A record pointing `aec.accessquint.com` to the VM IP
- API keys for panel debate (ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY)

## Local Development

```bash
# Install with web extras
pip install -e ".[web]"

# Run the dev server
uvicorn web.main:app --reload --port 8000

# Open http://localhost:8000
```

## Production Deployment

### 1. Install Caddy

```bash
sudo apt install -y caddy
sudo cp infra/Caddyfile /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
```

Caddy auto-provisions a Let's Encrypt TLS certificate.

### 2. Install the systemd service

```bash
sudo cp infra/aec-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aec-web
```

### 3. Verify

```bash
sudo systemctl status aec-web
curl -s https://aec.accessquint.com/api/controls | python3 -m json.tool
```

### 4. DNS

Create an A record:

```
aec.accessquint.com  →  <VM public IP>
```

## Configuration

Environment variables (set in `.env` on the VM):

| Variable | Default | Description |
|---|---|---|
| `AEC_WEB_RATE_LIMIT` | `3` | Max debates per IP per window |
| `AEC_WEB_RATE_WINDOW` | `60` | Rate limit window in seconds |
| `ANTHROPIC_API_KEY` | — | Required for Auditor persona |
| `OPENAI_API_KEY` | — | Required for Engineer persona |
| `GEMINI_API_KEY` | — | Required for Adversary persona |

## Rate Limiting

The server enforces per-IP rate limiting (default: 3 debates per 60 seconds).
This prevents abuse since each debate triggers LLM API calls.

## Monitoring

```bash
# Check service status
sudo systemctl status aec-web

# View logs
sudo journalctl -u aec-web -f

# Check Caddy
sudo systemctl status caddy
sudo journalctl -u caddy -f
```

## Troubleshooting

- **502 Bad Gateway**: uvicorn isn't running. Check `systemctl status aec-web`.
- **WebSocket disconnects**: Check that Caddy config allows WebSocket upgrades
  (it does by default with `reverse_proxy`).
- **Rate limited**: Wait 60 seconds or adjust `AEC_WEB_RATE_LIMIT`.
