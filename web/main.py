"""FastAPI server for the Audit Evidence Compiler live web dashboard.

Exposes a single-page UI where anyone with the link can run a panel debate
and watch it stream live via WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Audit Evidence Compiler")

STATIC_DIR = Path(__file__).resolve().parent / "static"
SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
OUT_DIR = Path(__file__).resolve().parent.parent / "out"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------------------------------------------------------------------------
# Rate limiter — per-IP, max 3 debates per minute
# ---------------------------------------------------------------------------

RATE_LIMIT = int(os.environ.get("AEC_WEB_RATE_LIMIT", "3"))
RATE_WINDOW = int(os.environ.get("AEC_WEB_RATE_WINDOW", "60"))

_ip_timestamps: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is within the rate limit."""
    now = time.monotonic()
    window_start = now - RATE_WINDOW
    _ip_timestamps[ip] = [t for t in _ip_timestamps[ip] if t > window_start]
    if len(_ip_timestamps[ip]) >= RATE_LIMIT:
        return False
    _ip_timestamps[ip].append(now)
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/controls")
def list_controls():
    """Return available demo controls from samples/."""
    controls = []
    label_map = {
        "soc2-cc61": {"control_id": "CC6.1", "framework": "SOC 2", "label": "CC6.1 — MFA enforcement"},
        "soc2-cc72": {"control_id": "CC7.2", "framework": "SOC 2", "label": "CC7.2 — Incident response"},
        "iso27001-a921": {
            "control_id": "A.9.2.1",
            "framework": "ISO 27001",
            "label": "A.9.2.1 — User registration",
        },
        "soc2-cc61-q2": {
            "control_id": "CC6.1",
            "framework": "SOC 2",
            "label": "CC6.1 Q2 — MFA enforcement (drift)",
        },
    }
    for path in sorted(SAMPLES_DIR.glob("*.json")):
        sample_name = path.stem
        info = label_map.get(sample_name, {
            "control_id": sample_name,
            "framework": "Unknown",
            "label": sample_name,
        })
        controls.append({"sample": sample_name, **info})
    return controls


@app.websocket("/ws/run")
async def run_debate(ws: WebSocket):
    await ws.accept()

    client_ip = "unknown"
    if ws.client:
        client_ip = ws.client.host

    try:
        cfg = await ws.receive_json()
    except WebSocketDisconnect:
        return

    if not _check_rate_limit(client_ip):
        await ws.send_json({
            "type": "error",
            "message": f"Rate limit exceeded — max {RATE_LIMIT} debates per {RATE_WINDOW}s",
        })
        await ws.close()
        return

    sample_name = cfg.get("sample", "soc2-cc61")
    run_id = str(uuid.uuid4())

    await ws.send_json({"type": "run_start", "run_id": run_id, "sample": sample_name})

    try:
        await _run_debate_pipeline(ws, sample_name, run_id)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass


async def _run_debate_pipeline(ws: WebSocket, sample_name: str, run_id: str) -> None:
    """Run the panel debate pipeline and stream JSON events over WebSocket."""
    await ws.send_json({"type": "phase", "name": "snapshot_fetch", "status": "start"})

    sample_path = SAMPLES_DIR / f"{sample_name}.json"
    if not sample_path.exists():
        await ws.send_json({"type": "error", "message": f"Sample '{sample_name}' not found"})
        return

    snapshot = json.loads(sample_path.read_text(encoding="utf-8"))
    control_id = snapshot["control_id"]

    await ws.send_json({
        "type": "phase",
        "name": "snapshot_fetch",
        "status": "done",
        "control_id": control_id,
        "event_count": snapshot.get("event_count", 0),
        "framework": snapshot.get("framework", ""),
    })

    control_texts = {
        "CC6.1": (
            "CC6.1: Logical and physical access controls — the entity implements "
            "logical access security software, infrastructure, and architectures "
            "over protected information assets."
        ),
        "CC7.2": (
            "CC7.2: The entity monitors system components for anomalies indicative "
            "of malicious acts, natural disasters, and errors."
        ),
        "A.9.2.1": (
            "A.9.2.1: User registration and de-registration — a formal process "
            "shall be implemented to enable assignment of access rights."
        ),
    }
    control_text = control_texts.get(control_id, f"Control {control_id}")

    await ws.send_json({"type": "phase", "name": "panel_debate", "status": "start"})

    try:
        from aec.agent.panel import run_panel
        from aec.agent.models import PanelResult

        class WebSocketPanelView:
            """A panel view that sends updates over WebSocket instead of Rich TUI."""

            def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop):
                self._ws = ws
                self._loop = loop

            def start(self) -> None:
                pass

            def update(self, persona: str, status: str, verdict: str | None = None) -> None:
                msg: dict = {"type": "panel", "persona": persona, "status": "thinking"}
                if verdict:
                    msg["status"] = "complete"
                    msg["verdict"] = verdict
                msg["rationale"] = status
                asyncio.run_coroutine_threadsafe(
                    self._ws.send_json(msg), self._loop
                )

            def finish(self, final_verdict: str, consensus_method: str) -> None:
                asyncio.run_coroutine_threadsafe(
                    self._ws.send_json({
                        "type": "consensus",
                        "verdict": final_verdict,
                        "method": consensus_method,
                    }),
                    self._loop,
                )

            def stop(self) -> None:
                pass

        loop = asyncio.get_event_loop()
        view = WebSocketPanelView(ws, loop)

        for persona in ("auditor", "engineer", "adversary"):
            await ws.send_json({
                "type": "panel",
                "persona": persona,
                "status": "thinking",
                "rationale": "Analyzing evidence...",
            })

        panel_result: PanelResult = await run_panel(
            snapshot=snapshot,
            control_text=control_text,
            spl_executed=snapshot.get("search", ""),
            splunk_snapshot=snapshot,
            view=view,
        )

        for critique in panel_result.critiques:
            await ws.send_json({
                "type": "panel",
                "persona": critique.persona,
                "status": "complete",
                "verdict": critique.verdict,
                "confidence": critique.confidence,
                "rationale": critique.rationale,
                "concerns": critique.concerns,
                "model": critique.model,
                "latency_ms": critique.latency_ms,
            })

        await ws.send_json({
            "type": "consensus",
            "verdict": panel_result.final_verdict,
            "method": panel_result.consensus_method,
            "mode": panel_result.mode,
            "degraded": panel_result.degraded,
        })

    except Exception as exc:
        await ws.send_json({
            "type": "error",
            "message": f"Panel debate failed: {exc}",
        })
        return

    await ws.send_json({"type": "phase", "name": "panel_debate", "status": "done"})

    # Write artifacts
    await ws.send_json({"type": "phase", "name": "artifacts", "status": "start"})

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    transcript_path = OUT_DIR / f"transcript_{run_id[:8]}_{ts}.md"
    transcript_path.write_text(panel_result.transcript, encoding="utf-8")

    memo_lines = [
        f"# Audit Memo — {control_id} ({snapshot.get('framework', '')})",
        f"Generated: {ts}",
        f"Run ID: {run_id}",
        "",
        "## Verdict",
        "",
        f"**{panel_result.final_verdict}** (consensus: {panel_result.consensus_method})",
        "",
        "## Panel Summary",
        "",
    ]
    for c in panel_result.critiques:
        memo_lines.append(f"### {c.persona.capitalize()} ({c.model})")
        memo_lines.append(f"- Verdict: {c.verdict} (confidence: {c.confidence:.0%})")
        memo_lines.append(f"- Rationale: {c.rationale}")
        if c.concerns:
            memo_lines.append("- Concerns:")
            for concern in c.concerns:
                memo_lines.append(f"  - {concern}")
        memo_lines.append("")

    memo_path = OUT_DIR / f"audit_memo_{run_id[:8]}_{ts}.md"
    memo_path.write_text("\n".join(memo_lines) + "\n", encoding="utf-8")

    artifacts = {
        "transcript": str(transcript_path.name),
        "memo": str(memo_path.name),
    }

    await ws.send_json({
        "type": "phase",
        "name": "artifacts",
        "status": "done",
        "artifacts": artifacts,
    })

    await ws.send_json({
        "type": "done",
        "run_id": run_id,
        "verdict": panel_result.final_verdict,
        "artifacts": artifacts,
    })


_incident_results: dict[str, dict] = {}


@app.post("/api/incident")
async def handle_incident(payload: dict):
    """Splunk alert action webhook. Maps alert to controls and queues a panel run."""
    import threading

    from aec.agent.incident_mapper import map_alert_to_controls

    alert_name = payload.get("alert_name", "")
    alert_body = payload.get("result", {}).get("message", "")
    controls = map_alert_to_controls(alert_name, alert_body)

    run_id = str(uuid.uuid4())
    _incident_results[run_id] = {"status": "queued", "controls": controls}

    def _bg():
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_run_incident_panel(run_id, controls, payload))
        loop.close()

    thread = threading.Thread(target=_bg, daemon=True)
    thread.start()

    return {"run_id": run_id, "controls": controls, "status": "queued"}


async def _run_incident_panel(rid: str, ctrls: list[str], alert: dict):
    try:
        _incident_results[rid]["status"] = "running"
        panel_results = []
        for control_id in ctrls:
            sample_key = _incident_sample_for_control(control_id)
            if not sample_key:
                panel_results.append({
                    "control_id": control_id,
                    "verdict": "INSUFFICIENT",
                    "confidence": 0.0,
                    "rationale": f"No evidence source for {control_id}.",
                    "recommendations": [],
                })
                continue

            sample_path = SAMPLES_DIR / f"{sample_key}.json"
            if not sample_path.exists():
                panel_results.append({
                    "control_id": control_id,
                    "verdict": "INSUFFICIENT",
                    "confidence": 0.0,
                    "rationale": f"Sample {sample_key} not found.",
                    "recommendations": [],
                })
                continue

            snapshot = json.loads(sample_path.read_text(encoding="utf-8"))
            snapshot["control_id"] = control_id

            control_texts = {
                "CC6.1": "CC6.1: Logical and physical access controls.",
                "CC7.2": "CC7.2: Monitoring for anomalies and security events.",
                "A.9.2.3": "A.9.2.3: Management of privileged access rights.",
                "PR.AC-1": "PR.AC-1: Identities and credentials are managed.",
            }
            control_text = control_texts.get(control_id, f"Control {control_id}")

            try:
                from aec.agent.panel import run_panel
                result = await run_panel(
                    snapshot=snapshot,
                    control_text=control_text,
                    spl_executed=snapshot.get("search", ""),
                    splunk_snapshot=snapshot,
                )
                panel_results.append({
                    "control_id": control_id,
                    "verdict": result.final_verdict,
                    "confidence": (
                        sum(c.confidence for c in result.critiques) / len(result.critiques)
                        if result.critiques else 0.0
                    ),
                    "rationale": result.critiques[0].rationale if result.critiques else "",
                    "recommendations": [],
                })
            except Exception:
                panel_results.append({
                    "control_id": control_id,
                    "verdict": "INSUFFICIENT",
                    "confidence": 0.0,
                    "rationale": "Panel execution failed.",
                    "recommendations": [],
                })

        from aec.agent.incident_mapper import build_incident_report

        report = build_incident_report(alert, ctrls, panel_results, 0.0)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
        report_path = OUT_DIR / f"incident_{rid[:8]}_{ts}.md"
        report_path.write_text(report, encoding="utf-8")

        _incident_results[rid] = {
            "status": "complete",
            "controls": ctrls,
            "panel_results": panel_results,
            "report_path": str(report_path.name),
        }
    except Exception as exc:
        _incident_results[rid] = {
            "status": "error",
            "controls": ctrls,
            "error": str(exc),
        }


@app.get("/api/incident/{run_id}")
def get_incident_status(run_id: str):
    """Poll for incident run status."""
    if run_id not in _incident_results:
        return JSONResponse(status_code=404, content={"error": "Run not found"})
    return _incident_results[run_id]


def _incident_sample_for_control(control_id: str) -> str | None:
    mapping = {
        "CC6.1": "soc2-cc61",
        "CC7.2": "soc2-cc72",
        "A.9.2.1": "iso27001-a921",
        "A.9.2.3": "iso27001-a921",
    }
    return mapping.get(control_id)


@app.get("/api/artifact/{filename}")
def get_artifact(filename: str):
    """Serve an artifact file for download."""
    safe_name = Path(filename).name
    artifact_path = OUT_DIR / safe_name
    if not artifact_path.exists() or not artifact_path.is_file():
        return JSONResponse(status_code=404, content={"error": "Artifact not found"})
    return FileResponse(
        str(artifact_path),
        filename=safe_name,
        media_type="application/octet-stream",
    )
