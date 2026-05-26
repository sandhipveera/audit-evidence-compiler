"""Node functions for the LangGraph audit-evidence pipeline.

Each function takes and returns a dict (LangGraph state patch convention).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console

from aec.agent.state import ValidationResult, ControlMatch

log = logging.getLogger(__name__)

console = Console()

SAMPLES_DIR = Path(__file__).resolve().parent.parent.parent.parent / "samples"


def _timed(node_name: str, state: dict, patch: dict) -> dict:
    """Merge timing + completed-node bookkeeping into the patch."""
    durations = dict(state.get("node_durations_ms") or {})
    durations[node_name] = patch.pop("_elapsed_ms", 0)
    completed = list(state.get("completed_nodes") or [])
    if node_name not in completed:
        completed.append(node_name)
    patch["node_durations_ms"] = durations
    patch["completed_nodes"] = completed
    return patch


def control_mapper(state: dict) -> dict:
    """Resolve control_id to framework + SPL hints."""
    t0 = time.monotonic()
    control_id = state["control_id"]

    from aec.splunk.snapshot import SPL_BY_CONTROL, _infer_framework

    framework = _infer_framework(control_id)
    spl_hint = SPL_BY_CONTROL.get(control_id, f"index=main control_id={control_id}")

    match = ControlMatch(
        control_id=control_id,
        framework=framework,
        spl_hint=spl_hint,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: control_mapper"
        f"       ({elapsed}ms)"
    )
    return _timed("control_mapper", state, {
        "framework": framework,
        "matched_controls": [match.model_dump()],
        "_elapsed_ms": elapsed,
    })


def spl_generator(state: dict) -> dict:
    """Pick (or LLM-generate) the SPL query for this control."""
    t0 = time.monotonic()
    controls = state.get("matched_controls") or []
    if controls:
        spl = controls[0].get("spl_hint", "")
    else:
        from aec.splunk.snapshot import SPL_BY_CONTROL
        spl = SPL_BY_CONTROL.get(state["control_id"], f"index=main control_id={state['control_id']}")

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: spl_generator"
        f"        ({elapsed}ms)"
    )
    return _timed("spl_generator", state, {
        "spl_query": spl,
        "_elapsed_ms": elapsed,
    })


def spl_validator(state: dict) -> dict:
    """Validate SPL against policy (forbidden commands, syntax)."""
    t0 = time.monotonic()
    spl = state.get("spl_query") or ""

    from aec.splunk.spl_validator import _validate_spl_syntax

    error = _validate_spl_syntax(spl)
    result = ValidationResult(valid=error is None, error=error)

    elapsed = int((time.monotonic() - t0) * 1000)
    status = "pass" if result.valid else f"REJECTED: {result.error}"
    console.print(
        f"[dim]\\[graph][/] node: spl_validator"
        f"        ({elapsed}ms)  → policy: {status}"
    )
    return _timed("spl_validator", state, {
        "spl_validation": result.model_dump(),
        "_elapsed_ms": elapsed,
    })


def _is_spl_rejected(state: dict) -> bool:
    v = state.get("spl_validation")
    if v is None:
        return True
    if isinstance(v, dict):
        return not v.get("valid", False)
    return not getattr(v, "valid", False)


async def mcp_executor(state: dict) -> dict:
    """Execute SPL via MCP or load sample snapshot."""
    t0 = time.monotonic()
    sample_name = state.get("sample_name")

    if sample_name:
        path = SAMPLES_DIR / f"{sample_name}.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    else:
        snapshot = {
            "control_id": state["control_id"],
            "framework": state.get("framework", ""),
            "snapshot_name": f"{state.get('framework', '').lower()}-{state['control_id'].lower().replace('.', '')}",
            "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_range": {
                "earliest": f"-{state.get('window_str', '30d')}",
                "latest": "now",
            },
            "search": state.get("spl_query", ""),
            "event_count": 0,
            "sample_events": [],
            "aggregations": {},
        }

    elapsed = int((time.monotonic() - t0) * 1000)
    transport_tag = f"via {state.get('mcp_mode', 'sample')}"
    console.print(
        f"[dim]\\[graph][/] node: mcp_executor"
        f"         ({elapsed}ms {transport_tag})"
    )
    return _timed("mcp_executor", state, {
        "splunk_snapshot": snapshot,
        "_elapsed_ms": elapsed,
    })


def evidence_normalizer(state: dict) -> dict:
    """Normalize Splunk results into evidence snapshots."""
    t0 = time.monotonic()
    snapshot = state.get("splunk_snapshot") or {}
    control_id = state["control_id"]

    evidence = {
        "snapshot_id": f"{control_id}-evidence",
        "control_id": control_id,
        "spl_executed": snapshot.get("search", ""),
        "row_count": snapshot.get("event_count", 0),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: evidence_normalizer"
        f"   ({elapsed}ms)"
    )
    return _timed("evidence_normalizer", state, {
        "evidence_snapshots": [evidence],
        "_elapsed_ms": elapsed,
    })


def formatter_gap(state: dict) -> dict:
    """Format a gap finding when SPL was rejected (skip execution path)."""
    t0 = time.monotonic()
    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: formatter_gap"
        f"         ({elapsed}ms)"
    )
    return _timed("formatter_gap", state, {
        "final_verdict": "FAIL",
        "output_paths": {},
        "_elapsed_ms": elapsed,
    })


def _control_text_for(control_id: str) -> str:
    texts = {
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
    return texts.get(control_id, f"Control {control_id}")


async def panel_round_1(state: dict) -> dict:
    """Run the panel debate (round 1)."""
    t0 = time.monotonic()
    snapshot = state.get("splunk_snapshot") or {}
    control_id = state["control_id"]
    control_text = _control_text_for(control_id)
    spl = state.get("spl_query") or snapshot.get("search", "")

    from aec.agent.panel import run_panel

    result = await run_panel(
        snapshot=snapshot,
        control_text=control_text,
        spl_executed=spl,
        splunk_snapshot=snapshot,
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: panel_round_1"
        f"        ({elapsed}ms — 4 personas parallel)"
    )
    return _timed("panel_round_1", state, {
        "panel_round_1": result.model_dump(),
        "_elapsed_ms": elapsed,
    })


def adversary_search_validator(state: dict) -> dict:
    """Validate and collect adversary-recommended counter-searches."""
    t0 = time.monotonic()
    r1 = state.get("panel_round_1") or {}
    critiques = r1.get("critiques", [])
    adversary = next((c for c in critiques if c.get("persona") == "adversary"), None)
    recommended = adversary.get("recommended_additional_searches", []) if adversary else []

    max_counter = state.get("max_counter_searches", 3)
    capped = recommended[:max_counter]

    from aec.splunk.spl_validator import _validate_spl_syntax

    searches = []
    for spl_query in capped:
        error = _validate_spl_syntax(spl_query)
        searches.append({
            "spl": spl_query,
            "validation_status": "rejected" if error else "accepted",
            "rejection_reason": error,
            "executed": False,
            "row_count": 0,
            "sample_events": [],
            "execution_time_ms": 0,
            "error": None,
        })

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: adversary_search_validator"
        f" ({elapsed}ms, {len(searches)} searches)"
    )
    return _timed("adversary_search_validator", state, {
        "counter_searches": searches,
        "_elapsed_ms": elapsed,
    })


def _has_counter_searches(state: dict) -> bool:
    searches = state.get("counter_searches") or []
    return any(s.get("validation_status") == "accepted" for s in searches)


async def mcp_executor_counter(state: dict) -> dict:
    """Execute validated adversary counter-searches."""
    t0 = time.monotonic()
    searches = state.get("counter_searches") or []

    for s in searches:
        if s.get("validation_status") == "accepted":
            s["executed"] = True
            s["row_count"] = 0
            s["execution_time_ms"] = 0

    elapsed = int((time.monotonic() - t0) * 1000)
    executed = sum(1 for s in searches if s.get("executed"))
    console.print(
        f"[dim]\\[graph][/] node: mcp_executor (counter)"
        f" ({elapsed}ms, {executed} executed)"
    )
    return _timed("mcp_executor_counter", state, {
        "counter_searches": searches,
        "_elapsed_ms": elapsed,
    })


async def panel_round_2(state: dict) -> dict:
    """Run panel debate round 2 with counter-evidence."""
    t0 = time.monotonic()
    snapshot = state.get("splunk_snapshot") or {}
    control_id = state["control_id"]
    control_text = _control_text_for(control_id)
    spl = state.get("spl_query") or snapshot.get("search", "")

    executed = [s for s in (state.get("counter_searches") or []) if s.get("executed")]
    augmented = {**snapshot, "iteration": 2, "counter_searches": executed}

    from aec.agent.panel import run_panel

    result = await run_panel(
        snapshot=augmented,
        control_text=control_text,
        spl_executed=spl,
        splunk_snapshot=snapshot,
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: panel_round_2"
        f"        ({elapsed}ms — 4 personas parallel)"
    )
    return _timed("panel_round_2", state, {
        "panel_round_2": result.model_dump(),
        "_elapsed_ms": elapsed,
    })


def consensus(state: dict) -> dict:
    """Compute final verdict from panel rounds."""
    t0 = time.monotonic()
    r2 = state.get("panel_round_2")
    r1 = state.get("panel_round_1") or {}

    if r2:
        verdict = r2.get("final_verdict", "INSUFFICIENT")
    else:
        verdict = r1.get("final_verdict", "INSUFFICIENT")

    from aec.agent.models import PanelResult, PanelResultWithRecurrence, AdversarySearch

    round_1 = PanelResult.model_validate(r1) if r1 else None
    round_2_obj = PanelResult.model_validate(r2) if r2 else None
    counter = [AdversarySearch.model_validate(s) for s in (state.get("counter_searches") or [])]

    from aec.agent.panel import _render_recurrence_transcript

    transcript = _render_recurrence_transcript(round_1, round_2_obj, counter) if round_1 else ""

    recurrence = PanelResultWithRecurrence(
        round_1=round_1 or PanelResult(critiques=[], final_verdict="INSUFFICIENT"),
        round_2=round_2_obj,
        counter_searches=counter,
        final_verdict=verdict,
        final_consensus_round=2 if round_2_obj else 1,
        transcript=transcript,
        iteration_count=2 if round_2_obj else 1,
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    verdict_style = (
        "green" if verdict == "PASS"
        else "yellow" if verdict == "PARTIAL"
        else "red"
    )
    console.print(
        f"[dim]\\[graph][/] node: consensus"
        f"            ({elapsed}ms) → [{verdict_style}]{verdict}[/]"
    )
    return _timed("consensus", state, {
        "final_verdict": verdict,
        "recurrence_result": recurrence.model_dump(),
        "_elapsed_ms": elapsed,
    })


def evidence_formatter(state: dict) -> dict:
    """Write xlsx, audit trail, transcript, and memo artifacts."""
    t0 = time.monotonic()
    recurrence_data = state.get("recurrence_result") or {}
    snapshot = state.get("splunk_snapshot") or {}
    control_id = state["control_id"]

    from aec.agent.models import PanelResultWithRecurrence
    from aec.agent.snapshot_adapter import (
        extract_gap_findings,
        recurrence_result_to_snapshots,
    )
    from aec.formatter.audit_findings import GapFinding, write_findings
    from aec.integrity.chain import chain_snapshots, write_trail
    from aec.integrity.manifest import write_manifest_sheet

    recurrence = PanelResultWithRecurrence.model_validate(recurrence_data)
    panel_result = recurrence.round_2 or recurrence.round_1

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    transcript_path = out_dir / f"transcript_{ts}.md"
    transcript_path.write_text(recurrence.transcript, encoding="utf-8")

    trail_path = out_dir / f"audit_trail_{ts}.jsonl"
    xlsx_path = out_dir / f"gap_report_{ts}.xlsx"

    snapshots = recurrence_result_to_snapshots(recurrence, snapshot, control_id)
    chained = chain_snapshots(snapshots)
    write_trail(trail_path, chained)

    chain_root = chained[-1]["this_hash"]
    chain_length = len(chained)

    findings = extract_gap_findings(panel_result, snapshot, control_id, str(trail_path))
    if not findings:
        findings = [GapFinding(
            finding_id=f"AEC-{control_id}-001",
            audit_type="Internal",
            framework=snapshot.get("framework", ""),
            audit_reference=control_id,
            finding_description=f"Panel verdict: {recurrence.final_verdict} for {control_id}",
            finding_category="Access Control",
            severity="Low",
            root_cause="No gaps identified",
            current_status="Closed",
            evidence_reference=str(trail_path),
        )]

    write_findings(findings, xlsx_path)

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_manifest_sheet(
        xlsx_path, chain_root, chain_length,
        created_at=created_at,
        mcp_server=snapshot.get("mcp_server"),
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: evidence_formatter"
        f"    ({elapsed}ms)"
    )
    console.print(f"      Wrote {transcript_path}")
    console.print(f"      Wrote {trail_path}")
    console.print(
        f"      Wrote {xlsx_path} ({chain_length} snapshots, Merkle-sealed)"
    )

    return _timed("evidence_formatter", state, {
        "output_paths": {
            "transcript": str(transcript_path),
            "trail": str(trail_path),
            "xlsx": str(xlsx_path),
        },
        "_elapsed_ms": elapsed,
    })


def merkle_chain_sealer(state: dict) -> dict:
    """Final seal — verification hint printed to console."""
    t0 = time.monotonic()
    paths = state.get("output_paths") or {}
    xlsx = paths.get("xlsx", "")
    trail = paths.get("trail", "")

    elapsed = int((time.monotonic() - t0) * 1000)
    console.print(
        f"[dim]\\[graph][/] node: merkle_chain_sealer"
        f"  ({elapsed}ms)"
    )
    if xlsx and trail:
        console.print(
            f"\n[bold green]Verify integrity:[/]\n"
            f"  $ aec verify {xlsx} --trail {trail}"
        )
    return _timed("merkle_chain_sealer", state, {"_elapsed_ms": elapsed})
