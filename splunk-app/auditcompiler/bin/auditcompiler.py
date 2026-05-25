#!/usr/bin/env python
"""Splunk custom search command: | auditcompiler

Pipes Splunk search results through the AEC three-agent panel debate
and returns rows enriched with verdict, severity, and root cause.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from typing import Any

# Vendored deps live in bin/lib/
_bin_dir = os.path.dirname(os.path.abspath(__file__))
_lib_dir = os.path.join(_bin_dir, "lib")
if _lib_dir not in sys.path:
    sys.path.insert(0, _lib_dir)

from splunklib.searchcommands import (
    Configuration,
    EventingCommand,
    Option,
    dispatch,
    validators,
)

log = logging.getLogger("auditcompiler")

SEVERITY_MAP = {
    "PASS": "info",
    "PARTIAL": "medium",
    "FAIL": "high",
    "INSUFFICIENT": "critical",
}

FRAMEWORK_ALIASES = {
    "SOC2": "SOC2",
    "ISO": "ISO27001",
    "ISO27001": "ISO27001",
    "NIST": "NIST_CSF",
    "NIST-CSF": "NIST_CSF",
    "NIST_CSF": "NIST_CSF",
}


def _build_snapshot_from_events(
    events: list[dict[str, Any]],
    control_id: str,
    framework: str,
) -> dict[str, Any]:
    """Aggregate Splunk event rows into the snapshot dict the panel expects."""
    sample_events = events[:10]
    aggregations: dict[str, Any] = {
        "event_count": len(events),
        "result_row_count": len(events),
    }

    numeric_sums: dict[str, float] = {}
    for row in events:
        for key, value in row.items():
            if key.startswith("_"):
                continue
            try:
                num = float(value)
                numeric_sums[key] = numeric_sums.get(key, 0.0) + num
            except (ValueError, TypeError):
                pass

    for key, total in sorted(numeric_sums.items()):
        metric_name = "result_count_sum" if key == "count" else f"{key}_sum"
        aggregations[metric_name] = int(total) if total == int(total) else round(total, 4)

    return {
        "control_id": control_id,
        "framework": framework,
        "snapshot_name": f"{framework.lower()}-{control_id.lower().replace('.', '')}",
        "event_count": len(events),
        "sample_events": sample_events,
        "aggregations": aggregations,
    }


def _run_panel_sync(
    snapshot: dict[str, Any],
    control_id: str,
    framework: str,
) -> dict[str, Any]:
    """Run the panel debate synchronously; returns a dict with verdict fields."""
    try:
        from aec.agent.panel import run_panel
    except ImportError as exc:
        log.error("Failed to import aec.agent.panel: %s", exc)
        return {
            "verdict": "INSUFFICIENT",
            "severity": "critical",
            "root_cause": f"Import error: {exc}",
            "consensus_method": "error",
            "transcript": "",
            "panel_mode": "error",
        }

    spl_executed = snapshot.get("search", f"[Splunk pipeline for {control_id}]")
    control_text = f"Control {control_id} ({framework})"

    try:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                run_panel(
                    snapshot=snapshot,
                    control_text=control_text,
                    spl_executed=spl_executed,
                )
            )
        finally:
            loop.close()
    except Exception as exc:
        log.error("Panel debate failed: %s", exc)
        return {
            "verdict": "INSUFFICIENT",
            "severity": "critical",
            "root_cause": f"Panel error: {exc}",
            "consensus_method": "error",
            "transcript": "",
            "panel_mode": "error",
        }

    critiques_summary = []
    for c in result.critiques:
        critiques_summary.append({
            "persona": c.persona,
            "verdict": c.verdict,
            "model": c.model,
            "confidence": c.confidence,
            "rationale": c.rationale,
        })

    adversary = next((c for c in result.critiques if c.persona == "adversary"), None)
    auditor = next((c for c in result.critiques if c.persona == "auditor"), None)
    engineer = next((c for c in result.critiques if c.persona == "engineer"), None)

    consensus_rationale = _build_consensus_rationale(result.critiques)

    return {
        "verdict": result.final_verdict,
        "severity": SEVERITY_MAP.get(result.final_verdict, "unknown"),
        "root_cause": consensus_rationale,
        "consensus_method": result.consensus_method,
        "transcript": result.transcript,
        "panel_mode": result.mode,
        "auditor_verdict": auditor.verdict if auditor else "N/A",
        "engineer_verdict": engineer.verdict if engineer else "N/A",
        "adversary_verdict": adversary.verdict if adversary else "N/A",
        "critiques": json.dumps(critiques_summary),
    }


def _build_consensus_rationale(critiques: list) -> str:
    """Combine persona rationales into a single root-cause string."""
    parts = []
    for c in critiques:
        parts.append(f"[{c.persona}] {c.rationale}")
    return " | ".join(parts) if parts else "No rationale available"


@Configuration()
class AuditCompilerCommand(EventingCommand):
    """Evaluate Splunk evidence against compliance controls using AI panel debate.

    Three AI models (Auditor, Engineer, Adversary) independently evaluate the
    evidence and produce a consensus verdict via lowest-of-three rule.

    Usage:
        | auditcompiler control=CC6.1
        | auditcompiler control=CC6.1 framework=SOC2 mode=summary
    """

    control = Option(
        require=True,
        doc="Framework control ID (e.g., CC6.1, A.9.2.1, DE.CM-1)",
    )
    framework = Option(
        default="SOC2",
        doc="Framework: SOC2 | ISO27001 | NIST_CSF",
    )
    mode = Option(
        default="enrich",
        validate=validators.Set("enrich", "summary"),
        doc="Output mode: enrich (per-row) or summary (single row)",
    )

    def transform(self, events):
        start_time = time.monotonic()
        control_id = self.control
        framework = FRAMEWORK_ALIASES.get(self.framework, self.framework)
        output_mode = self.mode or "enrich"

        rows = list(events)
        if not rows:
            yield {
                "_raw": "No events to evaluate",
                "verdict": "INSUFFICIENT",
                "severity": "critical",
                "root_cause": "No input events provided to auditcompiler",
            }
            return

        snapshot = _build_snapshot_from_events(rows, control_id, framework)

        panel_result = _run_panel_sync(snapshot, control_id, framework)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if output_mode == "summary":
            yield {
                "control_id": control_id,
                "framework": framework,
                "consensus": panel_result["verdict"],
                "auditor_verdict": panel_result.get("auditor_verdict", "N/A"),
                "engineer_verdict": panel_result.get("engineer_verdict", "N/A"),
                "adversary_verdict": panel_result.get("adversary_verdict", "N/A"),
                "root_cause": panel_result["root_cause"],
                "event_count": str(len(rows)),
                "panel_mode": panel_result.get("panel_mode", "unknown"),
                "elapsed_ms": str(elapsed_ms),
            }
        else:
            for row in rows:
                row["verdict"] = panel_result["verdict"]
                row["severity"] = panel_result["severity"]
                row["root_cause"] = panel_result["root_cause"]
                row["control_id"] = control_id
                row["framework"] = framework
                yield row


if __name__ == "__main__":
    dispatch(AuditCompilerCommand, sys.argv, sys.stdin, sys.stdout, __name__)
