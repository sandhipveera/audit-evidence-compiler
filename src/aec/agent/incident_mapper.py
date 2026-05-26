"""Alert-to-control mapper for SOC incident response mode.

Maps Splunk alert keywords to the compliance controls they implicate,
enabling automatic evidence collection when a SIEM alert fires.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

ALERT_TO_CONTROLS: dict[str, list[str]] = {
    "mfa": ["CC6.1", "A.9.2.3", "PR.AC-1"],
    "login": ["CC6.1", "CC7.2"],
    "privilege": ["CC6.1", "A.9.2.3"],
    "ransomware": ["CC7.2", "RC.RP-1"],
    "data_exfil": ["CC6.1", "A.9.2.3"],
    "data exfil": ["CC6.1", "A.9.2.3"],
    "anomal": ["CC7.2"],
    "brute": ["CC6.1", "CC7.2"],
    "lateral": ["CC6.1", "CC7.2"],
    "phish": ["CC6.1", "CC7.2"],
    "exfiltrat": ["CC6.1", "A.9.2.3"],
    "unauthori": ["CC6.1", "CC7.2"],
}

CONTROL_TO_SAMPLE: dict[str, str] = {
    "CC6.1": "soc2-cc61",
    "CC7.2": "soc2-cc72",
    "A.9.2.1": "iso27001-a921",
    "A.9.2.3": "iso27001-a921",
    "PR.AC-1": "soc2-cc61",
    "RC.RP-1": "soc2-cc72",
}

CONTROL_TEXTS: dict[str, str] = {
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
    "A.9.2.3": "A.9.2.3: Management of privileged access rights.",
    "PR.AC-1": "PR.AC-1: Identities and credentials are managed.",
    "RC.RP-1": "RC.RP-1: Response plans are executed during or after an incident.",
}


def map_alert_to_controls(alert_title: str, alert_body: str = "") -> list[str]:
    """Return control IDs most relevant to this alert.

    Scans the combined alert title + body for known keywords and returns
    the union of all matched control IDs. Falls back to CC6.1 (access
    control) if no keywords match.
    """
    text = (alert_title + " " + alert_body).lower()
    matched: list[str] = []
    for keyword, controls in ALERT_TO_CONTROLS.items():
        if keyword in text:
            for control in controls:
                if control not in matched:
                    matched.append(control)
    return matched or ["CC6.1"]


def alert_fields_from_payload(alert_payload: dict[str, Any]) -> tuple[str, str]:
    """Extract title/body text from a Splunk-style alert payload."""
    alert_name = str(
        alert_payload.get("alert_name")
        or alert_payload.get("search_name")
        or alert_payload.get("name")
        or ""
    )
    body_parts = [
        _textify(alert_payload.get("message")),
        _textify(alert_payload.get("description")),
        _textify(alert_payload.get("severity")),
    ]
    result = alert_payload.get("result")
    if isinstance(result, dict):
        body_parts.extend(_textify(value) for value in result.values())
    else:
        body_parts.append(_textify(result))
    return alert_name, " ".join(part for part in body_parts if part)


def sample_for_control(control_id: str) -> str | None:
    """Return the best bundled sample for a mapped incident control."""
    return CONTROL_TO_SAMPLE.get(control_id)


def control_text_for_incident(control_id: str) -> str:
    """Return a concise control description for incident-mode panel prompts."""
    return CONTROL_TEXTS.get(control_id, f"Control {control_id}")


def _textify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except TypeError:
        return str(value)


def build_incident_report(
    alert_payload: dict,
    controls: list[str],
    panel_results: list[dict],
    elapsed: float,
) -> str:
    """Render an incident compliance report as markdown."""
    alert_name = (
        alert_payload.get("alert_name")
        or alert_payload.get("search_name")
        or alert_payload.get("name")
        or "Unknown Alert"
    )
    severity = alert_payload.get("severity", "medium")
    triggered = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    raw_result = alert_payload.get("result", {})
    result_info = raw_result if isinstance(raw_result, dict) else {}
    count = alert_payload.get("count", result_info.get("count", "N/A"))
    user = alert_payload.get("user", result_info.get("user", "N/A"))

    lines = [
        "# Incident Compliance Report",
        "",
        f"**Alert:** {alert_name}",
        f"**Severity:** {severity}",
        f"**Triggered:** {triggered}",
        f"**Context:** {count} events, user={user}",
        f"**Controls evaluated:** {', '.join(controls)}",
        "",
    ]

    for pr in panel_results:
        control_id = pr["control_id"]
        verdict = pr["verdict"]
        confidence = pr.get("confidence", 0.0)
        rationale = pr.get("rationale", "No rationale provided.")

        lines.append(f"## {control_id}")
        lines.append(f"**Verdict: {verdict}** (confidence: {confidence:.2f})")
        lines.append(f"{rationale}")
        lines.append("")

    lines.append("## Recommended Actions")
    lines.append("")

    action_num = 1
    for pr in panel_results:
        if pr["verdict"] in ("FAIL", "PARTIAL"):
            for rec in pr.get("recommendations", []):
                lines.append(f"{action_num}. {rec}")
                action_num += 1

    if action_num == 1:
        lines.append("No immediate actions required based on current evidence.")

    lines.append("")
    lines.append("---")
    lines.append(f"*Generated by audit-evidence-compiler incident mode in {elapsed:.1f}s*")

    return "\n".join(lines) + "\n"
