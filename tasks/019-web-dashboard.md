# Task 019 — Live web dashboard at a public URL

**Goal:** A small FastAPI server on the Vultr VM that exposes a single-page web UI where anyone with the link can run a panel debate and watch it stream live. No installation required for the judge. Just a URL.

**Budget:** ~600 LOC (400 server + 200 vanilla JS frontend). ~3 days of YC time. Stretch goal — only fire if everything else lands with time to spare.

## Why this is worth doing (if there's time)

The submission portal probably has a "live demo URL" field. A working dashboard at `https://aec.accessquint.com` (or similar) means a judge clicks once and sees the agent run, without cloning the repo. That converts a passive submission into an active experience.

## Architecture

```
Browser (judge) ──── HTTPS ─── FastAPI server on VM ─── runs aec_demo as subprocess
                                       │                        ↓
                                       │                 streams logs + panel
                                       │                 reasoning over WebSocket
                                       │                        │
                                       └────── WebSocket ◄──────┘

Frontend: vanilla HTML/JS/CSS — no framework
  - Single page
  - Form: control_id dropdown (SOC 2 CC6.1, ISO A.9.2.3, NIST PR.AC-1)
  - "Run debate" button
  - 3-column live panel TUI (matches the local Rich TUI visually)
  - Final verdict + downloadable artifacts (transcript.md, audit_memo.md, xlsx)
```

## Server (FastAPI)

`web/main.py`:

```python
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import asyncio, json, uuid

app = FastAPI(title="Audit Evidence Compiler")
app.mount("/static", StaticFiles(directory="web/static"), name="static")

@app.get("/")
def root():
    return FileResponse("web/static/index.html")

@app.get("/api/controls")
def list_controls():
    """Return available demo controls (read from samples/)."""
    ...

@app.websocket("/ws/run")
async def run_debate(ws: WebSocket):
    await ws.accept()
    cfg = await ws.receive_json()
    run_id = str(uuid.uuid4())

    # Spawn aec_demo as subprocess with --json-stream flag
    proc = await asyncio.create_subprocess_exec(
        "aec_demo", "--control", cfg["control"], "--sample", cfg["sample"],
        "--json-stream",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # Stream stdout (one JSON event per line) to browser
    async for line in proc.stdout:
        try:
            event = json.loads(line)
            await ws.send_json(event)
        except json.JSONDecodeError:
            continue

    await proc.wait()
    await ws.send_json({"type": "done", "run_id": run_id})

@app.get("/api/artifact/{run_id}/{kind}")
def get_artifact(run_id: str, kind: str):
    """Serve transcript/memo/xlsx for download."""
    ...
```

## CLI extension: --json-stream

`aec_demo --json-stream` emits structured JSON events instead of Rich TUI:

```json
{"type": "phase", "name": "snapshot_fetch", "status": "start"}
{"type": "phase", "name": "snapshot_fetch", "status": "done", "duration_ms": 280}
{"type": "panel", "persona": "auditor", "status": "thinking"}
{"type": "panel", "persona": "auditor", "status": "complete",
 "verdict": "INSUFFICIENT", "confidence": 0.85, "rationale": "..."}
{"type": "consensus", "verdict": "INSUFFICIENT"}
{"type": "artifact", "kind": "transcript", "path": "out/transcript_<ts>.md"}
{"type": "done", "verdict": "INSUFFICIENT"}
```

Reuses the same internal state events; just a different sink than the Rich Live renderer.

## Frontend (vanilla JS)

`web/static/index.html` + `web/static/app.js` + `web/static/style.css`:

```html
<div class="hero">
  <h1>Audit Evidence Auto-Compiler</h1>
  <p>Three AI vendors debate compliance evidence from Splunk in real time.</p>
</div>

<form id="run-form">
  <select id="control">
    <option value="CC6.1">SOC 2 CC6.1 — MFA enforcement</option>
    <option value="CC7.2">SOC 2 CC7.2 — Incident response</option>
    <option value="A.9.2.3">ISO 27001 A.9.2.3 — Privileged access</option>
  </select>
  <button type="submit">Run debate</button>
</form>

<div id="panel" class="panel-3col">
  <div class="persona" data-name="auditor">
    <h3>Auditor (Claude Sonnet 4)</h3>
    <div class="status">idle</div>
    <div class="reasoning"></div>
    <div class="verdict"></div>
  </div>
  <div class="persona" data-name="engineer">...</div>
  <div class="persona" data-name="adversary">...</div>
</div>

<div id="consensus"></div>
<div id="downloads"></div>
```

WebSocket-driven updates. ~150 LOC of vanilla JS.

Style aims for the same visual energy as the Rich TUI — three columns, monospace font, color-coded verdict pills.

## Deployment on the Vultr VM

```bash
# Process supervisor: systemd unit at infra/aec-web.service
sudo cp infra/aec-web.service /etc/systemd/system/
sudo systemctl enable --now aec-web

# Reverse proxy: Caddy (simpler than nginx for one-app HTTPS)
sudo cp infra/Caddyfile /etc/caddy/
sudo systemctl reload caddy

# DNS: A record aec.accessquint.com → VM public IP
# Caddy auto-provisions Let's Encrypt cert
```

`infra/Caddyfile`:
```
aec.accessquint.com {
    reverse_proxy localhost:8000
}
```

`infra/aec-web.service`:
```ini
[Unit]
Description=Audit Evidence Compiler Web
After=network.target

[Service]
Type=simple
User=veera
WorkingDirectory=/home/veera/audit-evidence-compiler
EnvironmentFile=/home/veera/audit-evidence-compiler/.env
ExecStart=/home/veera/audit-evidence-compiler/.venv/bin/uvicorn web.main:app --host 0.0.0.0 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Rate limiting (because public)

Per-IP rate limit: max 3 debates per minute (LLM calls are subscription-paid; abuse drains quota).

Use `slowapi` (FastAPI-compatible) or write a simple in-memory counter.

## Files to create

- `web/main.py` — FastAPI app (~250 LOC)
- `web/static/index.html` (~50 LOC)
- `web/static/app.js` (~150 LOC)
- `web/static/style.css` (~100 LOC)
- `cli/aec_demo.py` — add `--json-stream` flag (~50 LOC)
- `infra/Caddyfile`
- `infra/aec-web.service`
- `docs/web-dashboard-deploy.md` — deployment runbook
- `tests/test_web.py` (~80 LOC) — FastAPI test client; WebSocket message shape; rate limiter

## Definition of done

- `https://aec.accessquint.com` (or chosen subdomain) loads the dashboard
- Submitting the form streams panel reasoning in real time
- All 3 personas show verdicts; consensus + downloadable artifacts appear
- Rate limiter blocks 4+ requests/min from one IP
- systemd unit survives reboots
- docs include the demo URL prominently

## Demo cue (15 seconds in the video — could even be the opening)

Cut to a phone screen. Open the public URL. Tap the dropdown. Tap "Run debate." Watch three personas argue in real time. Voice-over:

*"Judges can run this themselves — no clone, no install, no setup. Just a URL."*

That accessibility framing wins a lot of "easy to evaluate" judging points.

## Out of scope

- User accounts / auth (single-tenant demo)
- Persistent run history (one-shot per debate)
- Live Splunk option (always uses canned samples — operators with their own Splunk run locally)
- Custom domain/SSL beyond the one accessquint subdomain
- Mobile-optimized layout (looks fine on tablet/desktop; phone is bonus)
