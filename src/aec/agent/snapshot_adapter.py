"""Convert PanelResult + Splunk snapshot into EvidenceSnapshot dicts and GapFindings."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from aec.agent.models import PanelResult, PanelResultWithRecurrence
from aec.formatter.audit_findings import GapFinding


def panel_result_to_snapshots(
    result: PanelResult,
    splunk_snapshot: dict[str, Any],
    control_id: str,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Convert panel critiques + consensus into EvidenceSnapshot dicts for chaining."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    base: dict[str, Any] = {
        "control_id": control_id,
        "spl_executed": splunk_snapshot.get("search", ""),
        "row_count": splunk_snapshot.get("event_count", 0),
        "timestamp": ts,
        "mcp_server": splunk_snapshot.get("mcp_server"),
    }

    snapshots = []
    for critique in result.critiques:
        snap = {
            **base,
            "snapshot_id": f"{control_id}-{critique.persona}",
            "panel_verdict": critique.verdict,
            "persona": critique.persona,
            "model": critique.model,
            "transport": critique.transport,
            "confidence": critique.confidence,
            "rationale": critique.rationale,
            "panel_transcript_hash": _text_hash(critique.rationale),
        }
        snapshots.append(snap)

    snapshots.append({
        **base,
        "snapshot_id": f"{control_id}-consensus",
        "panel_verdict": result.final_verdict,
        "persona": "consensus",
        "consensus_method": result.consensus_method,
        "panel_transcript_hash": _text_hash(result.transcript),
    })

    return snapshots


def extract_gap_findings(
    result: PanelResult,
    splunk_snapshot: dict[str, Any],
    control_id: str,
    trail_path: str,
) -> list[GapFinding]:
    """Build GapFinding records when the verdict indicates gaps."""
    if result.final_verdict == "PASS":
        return []

    adversary = next((c for c in result.critiques if c.persona == "adversary"), None)

    severity = "High" if result.final_verdict in ("FAIL", "INSUFFICIENT") else "Medium"
    root_cause = adversary.rationale if adversary else (result.transcript or "See audit trail")

    remediation = "Review and remediate identified gaps"
    if adversary and adversary.recommended_additional_searches:
        remediation = f"Investigate: {adversary.recommended_additional_searches[0]}"

    return [GapFinding(
        finding_id=f"AEC-{control_id}-001",
        audit_type="Internal",
        framework=splunk_snapshot.get("framework", ""),
        audit_reference=control_id,
        finding_description=f"Panel verdict: {result.final_verdict} for {control_id}",
        finding_category="Access Control",
        severity=severity,
        root_cause=root_cause,
        affected_system=splunk_snapshot.get("snapshot_name", ""),
        remediation_action=remediation,
        current_status="Open",
        evidence_reference=trail_path,
    )]


def recurrence_result_to_snapshots(
    result: PanelResultWithRecurrence,
    splunk_snapshot: dict[str, Any],
    control_id: str,
    timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Convert a 2-round PanelResultWithRecurrence into chained EvidenceSnapshot dicts.

    Layout: persona critiques (round 1) + 1 consensus (round 1)
          + persona critiques (round 2) + 1 consensus (round 2)
          + 1 final verdict snapshot.
    When round 2 is None, returns only the round 1 snapshots + final.
    """
    ts = timestamp or datetime.now(timezone.utc).isoformat()

    snapshots = []

    r1_snaps = panel_result_to_snapshots(result.round_1, splunk_snapshot, control_id, ts)
    for snap in r1_snaps:
        snap["iteration"] = 1
        snapshots.append(snap)

    if result.round_2 is not None:
        r2_snaps = panel_result_to_snapshots(result.round_2, splunk_snapshot, control_id, ts)
        for snap in r2_snaps:
            snap["snapshot_id"] = snap["snapshot_id"] + "-r2"
            snap["iteration"] = 2
            snapshots.append(snap)

    snapshots.append({
        "control_id": control_id,
        "snapshot_id": f"{control_id}-final",
        "timestamp": ts,
        "iteration": result.iteration_count,
        "panel_verdict": result.final_verdict,
        "persona": "final",
        "final_consensus_round": result.final_consensus_round,
        "counter_searches_count": len(result.counter_searches),
        "panel_transcript_hash": _text_hash(result.transcript),
    })

    return snapshots


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
