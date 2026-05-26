# Task 021 — SOC Incident Response Mode

**Goal:** Bridge compliance + security operations. When a Splunk alert fires (failed logins spike, privilege escalation detected, anomalous MFA bypass), the agent automatically maps the alert to affected compliance controls, runs a targeted evidence collection, and produces an incident-linked audit report. Reframes the product from "audit tool" to "always-on compliance intelligence layer for the SOC."

**Budget:** ~200 LOC + tests. ~2 days.

## Why this matters for the hackathon

The security track judges want SOC tooling. Compliance-only submissions read as "GRC tool, not security tool." Adding incident-response mode bridges both:

*"When your SIEM fires an alert, this agent automatically asks: which compliance controls does this incident touch? It collects the evidence, runs the four-vendor debate, and produces a report the auditor can cite — in the same 30 seconds the SOC analyst is triaging."*

This is the "agentic SOC" pattern Splunk specifically demoed at RSAC 2026.

## How it works

```
Splunk Alert fires (saved search threshold exceeded)
  → Alert action: webhook POST to aec-web /api/incident
  → Agent maps alert type to control IDs (via priors catalog)
  → Runs aec_demo --sample <mapped_control> (or --live if Splunk available)
  → Produces: incident_report_<alert_id>.md + gap_report.xlsx
  → Returns JSON with verdict + artifact paths
  → (Optional) Posts summary back to Splunk HEC or incident ticket
```

## Alert → control mapping

`src/aec/agent/incident_mapper.py`:

```python
ALERT_TO_CONTROLS = {
    # Alert keyword → list of control IDs to evaluate
    "mfa": ["CC6.1", "A.9.2.3", "PR.AC-1"],
    "login": ["CC6.1", "CC7.2"],
    "privilege": ["CC6.1", "A.9.2.3"],
    "ransomware": ["CC7.2", "RC.RP-1"],
    "data_exfil": ["CC6.1", "A.9.2.3"],
    "anomal": ["CC7.2"],
    "brute": ["CC6.1"],
    "lateral": ["CC6.1", "CC7.2"],
}

def map_alert_to_controls(alert_title: str, alert_body: str) -> list[str]:
    """Return control IDs most relevant to this alert."""
    text = (alert_title + " " + alert_body).lower()
    matched = set()
    for keyword, controls in ALERT_TO_CONTROLS.items():
        if keyword in text:
            matched.update(controls)
    return list(matched) or ["CC6.1"]  # default to access control if no match
```

## CLI mode

```bash
# Pipe a Splunk alert JSON payload
echo '{"alert_name": "Brute Force Detected", "count": 847, "user": "svc_account"}' \
  | aec_demo --mode incident --alert-json -

# Or from a file (Splunk alert action drops JSON here)
aec_demo --mode incident --alert-json /tmp/splunk_alert_payload.json
```

## Web endpoint

```python
# web/main.py addition
@app.post("/api/incident")
async def handle_incident(payload: dict, background_tasks: BackgroundTasks):
    """Splunk alert action webhook. Maps alert → controls → runs panel."""
    controls = map_alert_to_controls(
        payload.get("alert_name", ""), 
        payload.get("result", {}).get("message", "")
    )
    run_id = str(uuid.uuid4())
    background_tasks.add_task(_run_incident_panel, run_id, controls, payload)
    return {"run_id": run_id, "controls": controls, "status": "queued"}
```

## Splunk alert action setup (in Splunk Web)

In the Splunk dashboard saved search, add a webhook alert action:
```
URL: https://aec.accessquint.com/api/incident
Method: POST
Body: {"alert_name": "$name$", "result": $result$}
```

This wires Splunk's native alert system to our agent automatically.

## Incident report format

`out/incident_<alert_id>_<ts>.md`:
```markdown
# Incident Compliance Report

**Alert:** Brute Force Detected (847 events, svc_account)
**Triggered:** 2026-05-25T20:14:32Z
**Controls evaluated:** CC6.1, CC7.2

## CC6.1 — MFA Enforcement
**Verdict: FAIL** (confidence: 0.91)
This incident directly implicates CC6.1: 847 failed login attempts
from svc_account indicate MFA is either absent or bypassable...

## CC7.2 — Incident Response
**Verdict: PARTIAL** (confidence: 0.74)
Evidence shows alert fired correctly (detection working) but no
automated containment action was taken within the SLA window...

## Recommended Actions
1. Immediately enforce MFA for svc_account
2. Review privileged account inventory against CC6.1 requirements
3. Document this incident per CC7.2 response procedures
```

## Files to create / modify

- `src/aec/agent/incident_mapper.py` (~60 LOC)
- `cli/aec_demo.py` — add `--mode incident --alert-json` flags (~40 LOC)
- `web/main.py` — add `/api/incident` endpoint + background task (~50 LOC)
- `web/static/index.html` — add "Incident" section showing recent incident reports
- `tests/test_incident_mapper.py` (~50 LOC) — keyword mapping + multi-control output

## Definition of done

- `echo '{"alert_name":"Brute Force"}' | aec_demo --mode incident --alert-json -` produces an incident report
- `/api/incident` POST endpoint accepts a Splunk alert payload and queues a panel run
- `map_alert_to_controls("Brute Force Detected", "...")` returns `["CC6.1", "CC7.2"]`
- Tests pass

## Demo cue (15 seconds)

Show Splunk Web → saved search → alert action → webhook to aec.accessquint.com. Then:
```bash
# What the webhook triggers:
echo '{"alert_name": "MFA Bypass Detected — 23 accounts", "severity": "high"}' \
  | aec_demo --mode incident --alert-json -

→ Controls implicated: CC6.1, A.9.2.3, PR.AC-1
→ Panel debate running (4 vendors)...
→ Incident report: out/incident_<id>.md
→ Done in 34s.
```

Voice-over: "When Splunk fires an alert, the agent asks: which compliance controls does this incident touch? Four vendors debate the evidence. The auditor gets a report."
