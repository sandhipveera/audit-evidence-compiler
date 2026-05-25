"""aec_demo — hackathon demo entry point.

Usage:
    aec_demo --sample soc2-cc61
    aec_demo --control CC6.1 --window 30d
    aec_demo --sample soc2-cc61 --no-llm
    aec_demo --control CC6.1 --review interactive
    aec_demo --control CC6.1 --resume <run_id>
    aec_demo --control "SOC2:CC6.1+ISO:A.9.2.3+NIST-CSF:PR.AC-1"
    aec_demo --concept access-control --frameworks "SOC2,ISO,NIST-CSF"
    aec_demo list-checkpoints
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

console = Console()


async def _run_graph(args: argparse.Namespace) -> None:
    """Execute the pipeline through the LangGraph wrapper."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from aec.agent.graph import build_graph, make_initial_state
    from aec.agent.state import read_checkpoint

    review_mode = getattr(args, "review", "auto")
    resume_id = getattr(args, "resume", None)

    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)

    if resume_id:
        saved = read_checkpoint(resume_id)
        if saved is None:
            console.print(f"[red]No checkpoint found for run_id={resume_id}[/]")
            raise SystemExit(1)
        console.print(
            f"[bold cyan]Resuming run {resume_id}[/] "
            f"(control={saved.control_id}, "
            f"completed={', '.join(saved.completed_nodes)})"
        )
        initial = saved.model_dump()
        thread_id = resume_id
    else:
        sample_name = args.sample
        control_id = args.control or (None if not sample_name else _sample_control(sample_name))

        if not control_id:
            console.print("[red]Cannot determine control_id[/]")
            raise SystemExit(1)

        mcp_mode = getattr(args, "mcp", "official")
        enable_recurrence = not getattr(args, "no_recurrence", False)
        max_counter = getattr(args, "max_counter_searches", 3)

        initial = make_initial_state(
            control_id=control_id,
            sample_name=sample_name,
            review_mode=review_mode,
            mcp_mode=mcp_mode,
            window_str=args.window,
            enable_recurrence=enable_recurrence,
            max_counter_searches=max_counter,
        )
        thread_id = initial["run_id"]

    config = {"configurable": {"thread_id": thread_id}}

    console.print(
        f"\n[bold]audit-evidence-compiler[/] "
        f"(run_id={thread_id}, review={review_mode})\n"
    )

    result = await graph.ainvoke(initial, config=config)

    while "__interrupt__" in result:
        interrupts = result["__interrupt__"]
        for intr in interrupts:
            payload = intr.value if hasattr(intr, "value") else intr
            if isinstance(payload, dict):
                prompt_text = payload.get("prompt", "Approve? [a/r]: ")
            else:
                prompt_text = str(payload)
            console.print(prompt_text)

        user_input = input("→ ").strip().lower()
        result = await graph.ainvoke(
            Command(resume=user_input), config=config,
        )

    verdict = result.get("final_verdict")
    if verdict:
        style = (
            "green" if verdict == "PASS"
            else "yellow" if verdict == "PARTIAL"
            else "red"
        )
        console.print(f"\n[bold {style}]Final verdict: {verdict}[/]")

    console.print(f"\n[bold green]Done.[/]  (run_id={thread_id})")


def _sample_control(sample_name: str) -> str | None:
    mapping = {
        "soc2-cc61": "CC6.1",
        "soc2-cc72": "CC7.2",
        "iso27001-a921": "A.9.2.1",
    }
    return mapping.get(sample_name)

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def _load_sample(name: str) -> dict:
    """Load a pre-canned snapshot from samples/."""
    path = SAMPLES_DIR / f"{name}.json"
    if not path.exists():
        available = [p.stem for p in SAMPLES_DIR.glob("*.json")]
        console.print(f"[red]Sample '{name}' not found.[/]")
        console.print(f"Available: {', '.join(available)}")
        raise SystemExit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _load_live(control_id: str, window: str) -> tuple[dict, object]:
    """Fetch live snapshot from Splunk via REST."""
    from aec.splunk.client import SplunkClient
    from aec.splunk.snapshot import fetch_snapshot

    client = SplunkClient(verify_ssl=False)
    return fetch_snapshot(control_id, time_window=window, client=client, live=True), client


async def _load_via_mcp(
    control_id: str,
    window: str,
    mcp_router,
    latest: str = "now",
) -> dict:
    """Fetch live snapshot from Splunk via MCP transport."""
    from aec.splunk.snapshot import SPL_BY_CONTROL, _infer_framework, derive_aggregations
    from aec.splunk.time_window import normalize_earliest

    spl = SPL_BY_CONTROL.get(control_id, f"index=main control_id={control_id}")
    earliest = normalize_earliest(window)

    result = await mcp_router.execute_spl(spl, time_window=earliest, latest=latest)

    from datetime import datetime, timezone

    return {
        "control_id": control_id,
        "framework": _infer_framework(control_id),
        "snapshot_name": (
            f"{_infer_framework(control_id).lower()}-{control_id.lower().replace('.', '')}"
        ),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_range": {"earliest": earliest, "latest": latest},
        "search": spl,
        "event_count": result.get("event_count", 0),
        "sample_events": result.get("results", [])[:10],
        "aggregations": derive_aggregations(result),
        "mcp_server": mcp_router.mcp_server_tag,
    }


def _control_text_for(control_id: str) -> str:
    """Return a human-readable description for common controls."""
    texts = {
        "CC6.1": (
            "CC6.1: Logical and physical access controls — the entity implements "
            "logical access security software, infrastructure, and architectures "
            "over protected information assets to protect them from security events."
        ),
        "CC7.2": (
            "CC7.2: The entity monitors system components and the operation of those "
            "components for anomalies that are indicative of malicious acts, natural "
            "disasters, and errors affecting the entity's ability to meet its objectives; "
            "anomalies are analyzed to determine whether they represent security events."
        ),
        "A.9.2.1": (
            "A.9.2.1: User registration and de-registration — a formal user registration "
            "and de-registration process shall be implemented to enable assignment of access rights."
        ),
    }
    return texts.get(control_id, f"Control {control_id}")


def _write_audit_memo(
    snapshot: dict,
    panel_result,
    out_dir: Path,
    ts: str,
    elapsed: float,
) -> Path:
    """Generate a human-readable audit memo from the panel result."""
    lines = [
        f"# Audit Memo — {snapshot['control_id']} ({snapshot['framework']})",
        f"Generated: {ts}",
        f"Evidence source: {snapshot.get('snapshot_name', 'live')}",
        f"Time range: {snapshot['time_range']['earliest']} to {snapshot['time_range']['latest']}",
        f"Events analyzed: {snapshot['event_count']}",
        "",
        "## Verdict",
        "",
        f"**{panel_result.final_verdict}** (consensus: {panel_result.consensus_method})",
        "",
        "## Panel Summary",
        "",
    ]

    for c in panel_result.critiques:
        lines.append(f"### {c.persona.capitalize()} ({c.model})")
        lines.append(f"- Verdict: {c.verdict} (confidence: {c.confidence:.0%})")
        lines.append(f"- Rationale: {c.rationale}")
        if c.concerns:
            lines.append("- Concerns:")
            for concern in c.concerns:
                lines.append(f"  - {concern}")
        lines.append("")

    lines.append("## Key Evidence")
    lines.append("")
    agg = snapshot.get("aggregations", {})
    if agg:
        for k, v in agg.items():
            if isinstance(v, float):
                lines.append(f"- {k}: {v:.0%}")
            else:
                lines.append(f"- {k}: {v}")
    lines.append("")

    lines.append("## SPL Query")
    lines.append("")
    lines.append(f"```spl\n{snapshot.get('search', 'N/A')}\n```")
    lines.append("")

    adversary = next((c for c in panel_result.critiques if c.persona == "adversary"), None)
    if adversary and adversary.recommended_additional_searches:
        lines.append("## Recommended Follow-up Searches")
        lines.append("")
        for spl in adversary.recommended_additional_searches:
            lines.append(f"- `{spl}`")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by audit-evidence-compiler in {elapsed:.1f}s*")

    memo_path = out_dir / f"audit_memo_{ts}.md"
    memo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return memo_path


def _parse_compare_flag(compare_str: str) -> tuple[str, str]:
    """Parse --compare value into two window specs.

    Accepted formats:
      "start1:end1,start2:end2" — two date ranges
      "sample1,sample2" — two sample names
    """
    parts = [p.strip() for p in compare_str.split(",")]
    if len(parts) != 2:
        raise ValueError(
            f"--compare requires exactly two windows separated by comma, got: {compare_str}"
        )
    return parts[0], parts[1]


def _parse_drift_window(drift_window: str) -> tuple[str, str]:
    """Convert --drift-window 90d into two date ranges relative to now.

    Returns two "earliest:latest" window strings for Splunk.
    """
    if not drift_window.endswith("d"):
        raise ValueError(f"--drift-window must end with 'd' (days), got: {drift_window}")
    days = int(drift_window[:-1])
    return f"-{days * 2}d:-{days}d", f"-{days}d:now"


def _parse_window_range(spec: str) -> tuple[str, str]:
    earliest, sep, latest = spec.partition(":")
    if not sep or not earliest or not latest:
        raise ValueError(f"Invalid window range: {spec}")
    return earliest, latest


def _write_drift_audit_memo(
    snapshot_1: dict,
    snapshot_2: dict,
    drift_analysis,
    panel_result,
    out_dir: Path,
    ts: str,
    elapsed: float,
) -> Path:
    """Generate an audit memo that includes the drift analysis in the executive summary."""
    lines = [
        f"# Audit Memo — {snapshot_2['control_id']} ({snapshot_2['framework']})",
        f"Generated: {ts}",
        f"Evidence source: {snapshot_1.get('snapshot_name', 'window1')} → "
        f"{snapshot_2.get('snapshot_name', 'window2')}",
        f"Window 1: {snapshot_1['time_range']['earliest']} to "
        f"{snapshot_1['time_range']['latest']}",
        f"Window 2: {snapshot_2['time_range']['earliest']} to "
        f"{snapshot_2['time_range']['latest']}",
        "",
        "## Executive Summary — Compliance Trend",
        "",
        f"**Overall direction: {drift_analysis.overall_direction.upper()}**",
        "",
        f"{drift_analysis.summary}",
        "",
    ]

    material = [m for m in drift_analysis.metrics if m.material]
    if material:
        lines.append("Material changes:")
        for m in material:
            sign = "+" if m.delta_pct > 0 else ""
            lines.append(
                f"- {m.name}: {m.value_1} → {m.value_2} ({sign}{m.delta_pct:.1f}%, {m.direction})"
            )
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**{panel_result.final_verdict}** (consensus: {panel_result.consensus_method})")
    lines.append("")
    lines.append("## Panel Summary")
    lines.append("")

    for c in panel_result.critiques:
        lines.append(f"### {c.persona.capitalize()} ({c.model})")
        lines.append(f"- Verdict: {c.verdict} (confidence: {c.confidence:.0%})")
        lines.append(f"- Rationale: {c.rationale}")
        if c.concerns:
            lines.append("- Concerns:")
            for concern in c.concerns:
                lines.append(f"  - {concern}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by audit-evidence-compiler in {elapsed:.1f}s*")

    memo_path = out_dir / f"audit_memo_{ts}.md"
    memo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return memo_path


async def _run_multi_framework(args: argparse.Namespace) -> None:
    """Execute multi-framework pipeline: map controls, run panel per internal control, emit N rows."""
    from aec.priors.framework_mapper import (
        expand_findings_multi_framework,
        map_concept,
        map_controls,
    )

    start = time.monotonic()

    if args.concept and args.frameworks:
        frameworks = [f.strip() for f in args.frameworks.split(",")]
        mapping = map_concept(args.concept, frameworks)
    else:
        refs = [r.strip() for r in args.control.split("+")]
        mapping = map_controls(refs)

    n_refs = len(mapping["parsed_refs"])
    n_internal = len(mapping["internal_controls"])
    n_spl = len(mapping["minimal_spl_set"])
    shared = mapping["shared_controls"]

    console.print(
        f"[bold cyan][1/6][/] Mapping {n_refs} framework controls → "
        f"{n_internal} unique internal controls"
    )
    if shared:
        console.print(
            f"      ({', '.join(shared)} satisfies multiple frameworks)"
        )
    console.print(
        f"[bold cyan][2/6][/] Generated {n_spl} SPL queries "
        f"(instead of {n_refs} — saved {max(0, n_refs - n_spl)}/{n_refs} execution time)"
    )

    for entry in mapping["minimal_spl_set"]:
        console.print(f"      SPL covers {', '.join(entry['covers'])}: {entry['spl'][:80]}...")

    sample_name = getattr(args, "sample", None)
    if sample_name:
        snapshot = _load_sample(sample_name)
        console.print(
            f"[bold cyan][3/6][/] Loaded sample: {sample_name} "
            f"({snapshot['event_count']} events)"
        )
    else:
        first_ctrl = mapping["internal_controls"][0] if mapping["internal_controls"] else "CTRL-003"
        first_ref = mapping["parsed_refs"][0] if mapping["parsed_refs"] else {}
        ctrl_id_for_snapshot = first_ref.get("control_id", first_ctrl)
        snapshot = _load_sample("soc2-cc61")
        snapshot["control_id"] = ctrl_id_for_snapshot
        console.print(
            f"[bold cyan][3/6][/] Using sample snapshot for {ctrl_id_for_snapshot} "
            f"({snapshot['event_count']} events)"
        )

    multi_fw_text = ", ".join(r["display_fw"] for r in mapping["parsed_refs"])
    multi_ctrl_text = ", ".join(r["control_id"] for r in mapping["parsed_refs"])
    control_text = (
        f"This evidence is being evaluated against {n_refs} compliance requirements from "
        f"{multi_fw_text}. Controls: {multi_ctrl_text}. "
    )
    if shared:
        ctrl_names = ", ".join(shared)
        control_text += (
            f"The same underlying controls ({ctrl_names}) appear across multiple "
            f"frameworks. A deficiency triggers findings in all referenced frameworks."
        )

    if args.no_llm:
        console.print("[yellow][4/6] Skipping panel (--no-llm)[/]")
        console.print(Panel(json.dumps(mapping, indent=2), title="Mapping", border_style="cyan"))
        return

    console.print(
        f"[bold cyan][4/6][/] Panel debate ({n_internal} internal controls)..."
    )

    from aec.agent.panel import run_panel_with_recurrence
    from aec.agent.panel_view import PanelView

    view = PanelView(console=console)
    view.start()

    try:
        recurrence_result = await run_panel_with_recurrence(
            snapshot=snapshot,
            control_text=control_text,
            spl_executed=snapshot.get("search", ""),
            splunk_snapshot=snapshot,
            time_window=args.window,
            view=view,
            enable_recurrence=not getattr(args, "no_recurrence", False),
            max_counter_searches=getattr(args, "max_counter_searches", 3),
        )
    finally:
        view.stop()

    panel_result = recurrence_result.round_2 or recurrence_result.round_1

    verdict_style = (
        "green" if recurrence_result.final_verdict == "PASS"
        else "yellow" if recurrence_result.final_verdict == "PARTIAL"
        else "red"
    )
    console.print(
        f"[bold cyan][5/6][/] Consensus: "
        f"[bold {verdict_style}]{recurrence_result.final_verdict}[/]"
        f" → triggers findings in {n_refs} frameworks"
    )

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    transcript_path = out_dir / f"transcript_{ts}.md"
    transcript_path.write_text(recurrence_result.transcript, encoding="utf-8")

    elapsed = time.monotonic() - start
    memo_path = _write_multi_framework_memo(
        snapshot, panel_result, mapping, out_dir, ts, elapsed,
    )

    from aec.agent.snapshot_adapter import (
        extract_gap_findings,
        recurrence_result_to_snapshots,
    )
    from aec.formatter.audit_findings import GapFinding, write_findings
    from aec.integrity.chain import chain_snapshots, write_trail
    from aec.integrity.manifest import write_manifest_sheet

    trail_path = out_dir / f"audit_trail_{ts}.jsonl"
    xlsx_path = out_dir / f"gap_report_{ts}.xlsx"

    control_id = snapshot["control_id"]
    snapshots = recurrence_result_to_snapshots(recurrence_result, snapshot, control_id)
    chained = chain_snapshots(snapshots)
    write_trail(trail_path, chained)
    chain_root = chained[-1]["this_hash"]
    chain_length = len(chained)

    base_findings = extract_gap_findings(
        panel_result, snapshot, control_id, str(trail_path),
    )
    if not base_findings:
        base_findings = [GapFinding(
            finding_id=f"AEC-{control_id}-001",
            audit_type="Internal",
            framework=snapshot.get("framework", ""),
            audit_reference=control_id,
            finding_description=(
                f"Panel verdict: {recurrence_result.final_verdict} for {control_id}"
            ),
            finding_category="Access Control",
            severity="Low",
            root_cause="No gaps identified",
            current_status="Closed",
            evidence_reference=str(trail_path),
        )]

    expanded_findings = expand_findings_multi_framework(base_findings, mapping["parsed_refs"])
    write_findings(expanded_findings, xlsx_path)

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_manifest_sheet(
        xlsx_path, chain_root, chain_length, created_at=created_at,
        mcp_server=snapshot.get("mcp_server"),
    )

    console.print(f"[bold cyan][6/6][/] Wrote {trail_path}")
    console.print(f"      Wrote {transcript_path}")
    console.print(f"      Wrote {memo_path}")
    console.print(
        f"\n[bold green]Wrote {xlsx_path} "
        f"({len(expanded_findings)} findings: {n_refs} frameworks × "
        f"{len(base_findings)} base finding(s), Merkle-sealed)[/]"
    )

    total = time.monotonic() - start
    console.print(f"\n[bold green]Done in {total:.0f}s.[/]")


def _write_multi_framework_memo(
    snapshot: dict,
    panel_result,
    mapping: dict,
    out_dir: Path,
    ts: str,
    elapsed: float,
) -> Path:
    """Generate an audit memo with a multi-framework evidence map section."""
    parsed = mapping["parsed_refs"]
    frameworks = ", ".join(r["display_fw"] for r in parsed)

    lines = [
        f"# Audit Memo — Multi-framework ({frameworks})",
        f"Generated: {ts}",
        f"Evidence source: {snapshot.get('snapshot_name', 'live')}",
        "",
        "## Multi-framework evidence map",
        "",
        f"**{len(parsed)} framework controls → "
        f"{len(mapping['internal_controls'])} internal controls → "
        f"{len(mapping['minimal_spl_set'])} SPL queries**",
        "",
        "| Framework | Framework Control | Internal Controls |",
        "|---|---|---|",
    ]

    for ref in parsed:
        ctrls = mapping["framework_coverage"].get(ref["input"], [])
        lines.append(
            f"| {ref['display_fw']} | {ref['control_id']} | {', '.join(ctrls)} |"
        )
    lines.append("")

    if mapping["shared_controls"]:
        lines.append(
            f"**Shared controls** (appear in all frameworks): "
            f"{', '.join(mapping['shared_controls'])}"
        )
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(
        f"**{panel_result.final_verdict}** (consensus: {panel_result.consensus_method})"
    )
    lines.append("")

    lines.append("## Panel Summary")
    lines.append("")
    for c in panel_result.critiques:
        lines.append(f"### {c.persona.capitalize()} ({c.model})")
        lines.append(f"- Verdict: {c.verdict} (confidence: {c.confidence:.0%})")
        lines.append(f"- Rationale: {c.rationale}")
        if c.concerns:
            lines.append("- Concerns:")
            for concern in c.concerns:
                lines.append(f"  - {concern}")
        lines.append("")

    lines.append("---")
    lines.append(f"*Generated by audit-evidence-compiler in {elapsed:.1f}s*")

    memo_path = out_dir / f"audit_memo_{ts}.md"
    memo_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return memo_path


def _has_splunk_env() -> bool:
    return bool(os.environ.get("SPLUNK_HOST") and os.environ.get("SPLUNK_TOKEN"))


def _resolve_mcp_mode(cli_value: str | None, live: bool = False) -> str:
    """Resolve CLI/env transport selection.

    The hackathon default is MCP via splunk-official. The legacy --live flag
    still forces the direct REST path unless --mcp is explicitly provided.
    """
    if cli_value is not None:
        return cli_value
    if live:
        return "rest"

    env_value = os.environ.get("AEC_SPLUNK_MCP_SERVER")
    if not env_value:
        return "official"

    mcp_map = {
        "official": "official",
        "splunk-official": "official",
        "livehybrid": "livehybrid",
        "rest": "rest",
    }
    try:
        return mcp_map[env_value]
    except KeyError as exc:
        valid = ", ".join(sorted(mcp_map))
        raise ValueError(
            f"Invalid AEC_SPLUNK_MCP_SERVER={env_value!r}; choose one of: {valid}"
        ) from exc


async def _run(args: argparse.Namespace) -> None:
    start = time.monotonic()
    splunk_client = None
    mcp_router = None
    mcp_mode = getattr(args, "mcp", None)

    # Step 1: Load snapshot
    if args.sample:
        snapshot = _load_sample(args.sample)
        console.print(
            f"[bold cyan][1/5][/] Loading snapshot: samples/{args.sample}.json "
            f"({snapshot['event_count']} events, "
            f"{snapshot['time_range']['earliest']} to {snapshot['time_range']['latest']})"
        )
    elif mcp_mode and mcp_mode != "rest":
        from aec.splunk.mcp import MCPRouter, MCPTransportError

        preferred = {"official": "splunk-official", "livehybrid": "livehybrid"}[mcp_mode]
        mcp_router = MCPRouter(preferred=preferred)
        try:
            await mcp_router.connect()
        except MCPTransportError as exc:
            console.print(f"[red]MCP connection failed: {exc}[/]")
            raise SystemExit(1)

        console.print(
            f"[bold cyan][1/5][/] MCP server: {mcp_router.active_label}"
        )
        if mcp_router.fallback_label:
            console.print(
                f"      Fallback configured: {mcp_router.fallback_label}"
            )

        try:
            snapshot = await _load_via_mcp(args.control, args.window, mcp_router)
        except MCPTransportError as exc:
            console.print(f"[red]MCP query failed: {exc}[/]")
            raise SystemExit(1)
        console.print(
            f"      [green]✓[/] Fetched {snapshot['event_count']} events "
            f"({snapshot['time_range']['earliest']} to {snapshot['time_range']['latest']})"
        )
    elif _has_splunk_env() or args.live:
        if not _has_splunk_env():
            console.print("[red]--live requires SPLUNK_HOST and SPLUNK_TOKEN env vars.[/]")
            console.print("Set them in .env or export them:")
            console.print("  export SPLUNK_HOST=https://localhost:8089")
            console.print("  export SPLUNK_TOKEN=your-token")
            raise SystemExit(1)
        console.print(
            f"[bold cyan][1/5][/] Connecting to Splunk "
            f"({os.environ.get('SPLUNK_HOST', '')})..."
        )
        snapshot, splunk_client = _load_live(args.control, args.window)
        console.print(
            f"      [green]✓[/] Fetched {snapshot['event_count']} events "
            f"({snapshot['time_range']['earliest']} to {snapshot['time_range']['latest']})"
        )
    else:
        console.print("[red]No snapshot source available.[/]")
        console.print("Either:")
        console.print("  1. Use --sample <name> for pre-canned data")
        console.print("  2. Set SPLUNK_HOST + SPLUNK_TOKEN for live queries")
        console.print("  3. Use --live with env vars configured")
        raise SystemExit(1)

    control_id = snapshot["control_id"]
    control_text = _control_text_for(control_id)

    if args.no_llm:
        console.print("[yellow][2/5] Skipping panel (--no-llm)[/]")
        console.print(Panel(json.dumps(snapshot, indent=2), title="Snapshot", border_style="cyan"))
        return

    # Step 2: Run panel (with recurrence loop)
    enable_recurrence = not getattr(args, "no_recurrence", False)
    max_counter = getattr(args, "max_counter_searches", 3)
    round_label = "with recurrence" if enable_recurrence else "single round"
    console.print(
        f"[bold cyan][2/5][/] Running panel debate (3 personas, parallel, {round_label})..."
    )

    from aec.agent.panel import run_panel_with_recurrence
    from aec.agent.panel_view import PanelView

    view = PanelView(console=console)
    view.start()

    try:
        recurrence_result = await run_panel_with_recurrence(
            snapshot=snapshot,
            control_text=control_text,
            spl_executed=snapshot.get("search", ""),
            splunk_snapshot=snapshot,
            splunk_client=splunk_client,
            time_window=args.window,
            view=view,
            enable_recurrence=enable_recurrence,
            max_counter_searches=max_counter,
        )
    finally:
        view.stop()

    panel_result = recurrence_result.round_2 or recurrence_result.round_1

    # Step 3: Consensus
    verdict_style = (
        "green" if recurrence_result.final_verdict == "PASS"
        else "yellow" if recurrence_result.final_verdict == "PARTIAL"
        else "red"
    )

    adversary = next((c for c in panel_result.critiques if c.persona == "adversary"), None)
    consensus_detail = ""
    if adversary and adversary.concerns:
        consensus_detail = f" — adversary surfaced: {adversary.concerns[0]}"

    console.print(
        f"[bold cyan][3/5][/] Consensus: "
        f"[bold {verdict_style}]{recurrence_result.final_verdict}[/]"
        f" (round {recurrence_result.final_consensus_round})"
        f"{consensus_detail}"
    )

    if recurrence_result.counter_searches:
        executed = sum(1 for s in recurrence_result.counter_searches if s.executed)
        console.print(
            f"      Counter-evidence: {executed}/{len(recurrence_result.counter_searches)} "
            f"adversary searches executed"
        )

    if panel_result.adversary_followups:
        console.print("      Included adversary follow-up search results in transcript")

    # Step 4: Write artifacts
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    transcript_path = out_dir / f"transcript_{ts}.md"
    transcript_path.write_text(recurrence_result.transcript, encoding="utf-8")

    elapsed = time.monotonic() - start
    memo_path = _write_audit_memo(snapshot, panel_result, out_dir, ts, elapsed)

    console.print(f"[bold cyan][4/5][/] Wrote {transcript_path}")
    console.print(f"      Wrote {memo_path}")

    # Step 5: Evidence chain + xlsx + manifest (Merkle seal)
    from aec.agent.snapshot_adapter import (
        extract_gap_findings,
        recurrence_result_to_snapshots,
    )
    from aec.formatter.audit_findings import write_findings
    from aec.integrity.chain import chain_snapshots, write_trail
    from aec.integrity.manifest import write_manifest_sheet

    trail_path = out_dir / f"audit_trail_{ts}.jsonl"
    xlsx_path = out_dir / f"gap_report_{ts}.xlsx"

    snapshots = recurrence_result_to_snapshots(recurrence_result, snapshot, control_id)
    chained = chain_snapshots(snapshots)
    write_trail(trail_path, chained)

    chain_root = chained[-1]["this_hash"]
    chain_length = len(chained)

    findings = extract_gap_findings(
        panel_result, snapshot, control_id, str(trail_path),
    )
    if not findings:
        from aec.formatter.audit_findings import GapFinding

        findings = [GapFinding(
            finding_id=f"AEC-{control_id}-001",
            audit_type="Internal",
            framework=snapshot.get("framework", ""),
            audit_reference=control_id,
            finding_description=f"Panel verdict: {recurrence_result.final_verdict} for {control_id}",
            finding_category="Access Control",
            severity="Low",
            root_cause="No gaps identified",
            current_status="Closed",
            evidence_reference=str(trail_path),
        )]

    write_findings(findings, xlsx_path)

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_manifest_sheet(
        xlsx_path,
        chain_root,
        chain_length,
        created_at=created_at,
        mcp_server=snapshot.get("mcp_server"),
    )

    console.print(f"[bold cyan][5/5][/] Wrote {trail_path}")
    console.print(
        f"\n[bold green]Wrote {xlsx_path} "
        f"({chain_length} evidence snapshots, Merkle-sealed)[/]"
    )
    console.print(
        f"\nVerify integrity:\n"
        f"  $ aec verify {xlsx_path} --trail {trail_path}"
    )

    mcp_tag = snapshot.get("mcp_server")
    if mcp_tag:
        console.print(f"\nprovenance: mcp_server={mcp_tag}")

    if mcp_router:
        await mcp_router.close()

    total = time.monotonic() - start
    console.print(f"\n[bold green]Done in {total:.0f}s.[/]")


async def _run_drift(args: argparse.Namespace) -> None:
    """Execute the two-window drift comparison pipeline."""
    from aec.splunk.drift import compute_drift, format_drift_transcript

    start = time.monotonic()
    mcp_router = None
    threshold = getattr(args, "drift_threshold", 5.0)

    compare_str = args.compare
    if args.drift_window:
        w1_spec, w2_spec = _parse_drift_window(args.drift_window)
        compare_str = f"{w1_spec},{w2_spec}"

    window_1_spec, window_2_spec = _parse_compare_flag(compare_str)

    is_sample_mode = ":" not in window_1_spec and ":" not in window_2_spec

    if is_sample_mode:
        snapshot_1 = _load_sample(window_1_spec)
        snapshot_2 = _load_sample(window_2_spec)
        console.print(
            f"[bold cyan][1/6][/] Loading window 1: samples/{window_1_spec}.json "
            f"({snapshot_1['event_count']} events)"
        )
        console.print(
            f"[bold cyan][2/6][/] Loading window 2: samples/{window_2_spec}.json "
            f"({snapshot_2['event_count']} events)"
        )
    else:
        if not args.control:
            console.print("[red]--control is required for live drift comparison[/]")
            raise SystemExit(1)

        e1, l1 = _parse_window_range(window_1_spec)
        e2, l2 = _parse_window_range(window_2_spec)

        if args.mcp and args.mcp != "rest":
            from aec.splunk.mcp import MCPRouter, MCPTransportError

            preferred = {"official": "splunk-official", "livehybrid": "livehybrid"}[args.mcp]
            mcp_router = MCPRouter(preferred=preferred)
            try:
                await mcp_router.connect()
            except MCPTransportError as exc:
                console.print(f"[red]MCP connection failed: {exc}[/]")
                raise SystemExit(1)

            console.print(f"[bold cyan]MCP server:[/] {mcp_router.active_label}")
            if mcp_router.fallback_label:
                console.print(f"      Fallback configured: {mcp_router.fallback_label}")

            console.print(
                f"[bold cyan][1/6][/] Fetching window 1 snapshot... ({e1} to {l1})"
            )
            try:
                snapshot_1 = await _load_via_mcp(args.control, e1, mcp_router, latest=l1)
            except MCPTransportError as exc:
                console.print(f"[red]MCP query failed: {exc}[/]")
                if mcp_router:
                    await mcp_router.close()
                raise SystemExit(1)

            console.print(
                f"[bold cyan][2/6][/] Fetching window 2 snapshot... ({e2} to {l2})"
            )
            try:
                snapshot_2 = await _load_via_mcp(args.control, e2, mcp_router, latest=l2)
            except MCPTransportError as exc:
                console.print(f"[red]MCP query failed: {exc}[/]")
                if mcp_router:
                    await mcp_router.close()
                raise SystemExit(1)
        elif _has_splunk_env() or args.live:
            from aec.splunk.client import SplunkClient
            from aec.splunk.snapshot import fetch_snapshot

            client = SplunkClient(verify_ssl=False)
            console.print(
                f"[bold cyan][1/6][/] Fetching window 1 snapshot... ({e1} to {l1})"
            )
            snapshot_1 = fetch_snapshot(
                args.control,
                time_window=e1,
                latest=l1,
                client=client,
                live=True,
            )

            console.print(
                f"[bold cyan][2/6][/] Fetching window 2 snapshot... ({e2} to {l2})"
            )
            snapshot_2 = fetch_snapshot(
                args.control,
                time_window=e2,
                latest=l2,
                client=client,
                live=True,
            )
        else:
            console.print("[red]No Splunk connection available for live drift.[/]")
            console.print("Use sample names with --compare (e.g., --compare soc2-cc61,soc2-cc61-q2)")
            raise SystemExit(1)

    control_id = snapshot_2["control_id"]
    control_text = _control_text_for(control_id)

    console.print(
        f"[bold cyan][3/6][/] Drift analysis (threshold: {threshold}%)..."
    )
    drift_analysis = compute_drift(snapshot_1, snapshot_2, threshold_pct=threshold)

    direction_style = {
        "improving": "green",
        "stable": "yellow",
        "worsening": "red",
    }[drift_analysis.overall_direction]
    console.print(
        f"      [{direction_style}]{drift_analysis.summary}[/]"
    )

    if args.no_llm:
        console.print("[yellow][4/6] Skipping panel (--no-llm)[/]")
        drift_text = format_drift_transcript(drift_analysis)
        console.print(Panel(drift_text, title="Drift Analysis", border_style="cyan"))
        if mcp_router:
            await mcp_router.close()
        return

    console.print(
        "[bold cyan][4/6][/] Panel debate (drift-aware, 3 personas)..."
    )

    from aec.agent.panel import run_panel_with_recurrence
    from aec.agent.panel_view import PanelView

    enable_recurrence = not getattr(args, "no_recurrence", False)
    max_counter = getattr(args, "max_counter_searches", 3)

    view = PanelView(console=console)
    view.start()

    try:
        recurrence_result = await run_panel_with_recurrence(
            snapshot=snapshot_2,
            control_text=control_text,
            spl_executed=snapshot_2.get("search", ""),
            splunk_snapshot=snapshot_2,
            time_window=args.window,
            view=view,
            enable_recurrence=enable_recurrence,
            max_counter_searches=max_counter,
            drift=drift_analysis,
        )
    finally:
        view.stop()
        if mcp_router:
            await mcp_router.close()
            mcp_router = None

    panel_result = recurrence_result.round_2 or recurrence_result.round_1

    verdict_style = (
        "green" if recurrence_result.final_verdict == "PASS"
        else "yellow" if recurrence_result.final_verdict == "PARTIAL"
        else "red"
    )
    console.print(
        f"[bold cyan][5/6][/] Consensus: "
        f"[bold {verdict_style}]{recurrence_result.final_verdict}[/]"
        f" (round {recurrence_result.final_consensus_round})"
    )

    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")

    drift_block = format_drift_transcript(drift_analysis)
    transcript_content = drift_block + "\n" + recurrence_result.transcript
    transcript_path = out_dir / f"transcript_{ts}.md"
    transcript_path.write_text(transcript_content, encoding="utf-8")

    elapsed = time.monotonic() - start
    memo_path = _write_drift_audit_memo(
        snapshot_1, snapshot_2, drift_analysis, panel_result, out_dir, ts, elapsed,
    )

    console.print(f"[bold cyan][6/6][/] Wrote {transcript_path}")
    console.print(f"      Wrote {memo_path}")

    total = time.monotonic() - start
    console.print(f"\n[bold green]Done in {total:.0f}s.[/]")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Evidence Compiler — Splunk hackathon demo"
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    subparsers.add_parser(
        "list-checkpoints", help="Show resumable checkpoint runs"
    )
    subparsers.add_parser(
        "clean", help="Remove all checkpoints"
    )

    parser.add_argument(
        "--control",
        help="Control ID (e.g., CC6.1, A.9.2.1, PR.AC-1)",
    )
    parser.add_argument(
        "--window",
        default="30d",
        help="Time window for Splunk search (default: 30d)",
    )
    parser.add_argument(
        "--sample",
        help="Use pre-canned snapshot (e.g., soc2-cc61). Bypasses live Splunk.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Force live Splunk mode (requires SPLUNK_HOST + SPLUNK_TOKEN)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip panel debate — just show snapshot (debugging)",
    )
    parser.add_argument(
        "--mcp",
        choices=["official", "livehybrid", "rest"],
        default=None,
        help=(
            "Splunk transport: official (splunk-official MCP), "
            "livehybrid (community MCP), rest (direct REST API). "
            "Default: read AEC_SPLUNK_MCP_SERVER env, else official."
        ),
    )
    parser.add_argument(
        "--compare",
        default=None,
        help=(
            "Two time windows for drift comparison, as "
            "\"start1:end1,start2:end2\" (e.g., \"2018-08-01:2018-08-15,2018-09-01:2018-09-15\"). "
            "Can also be two sample names: \"soc2-cc61,soc2-cc61-q2\"."
        ),
    )
    parser.add_argument(
        "--drift-window",
        default=None,
        help=(
            "Shorthand for --compare: compare last N days vs the N days before that. "
            "E.g., --drift-window 90d compares last 90d vs the 90d before that."
        ),
    )
    parser.add_argument(
        "--drift-threshold",
        type=float,
        default=5.0,
        help="Percentage threshold for material drift (default: 5.0)",
    )
    parser.add_argument(
        "--concept",
        default=None,
        help=(
            "Concept shorthand for multi-framework mapping "
            "(e.g., access-control, logging, data-protection). "
            "Use with --frameworks."
        ),
    )
    parser.add_argument(
        "--frameworks",
        default=None,
        help=(
            "Comma-separated list of frameworks for --concept mode "
            "(e.g., SOC2,ISO,NIST-CSF)."
        ),
    )
    parser.add_argument(
        "--no-recurrence",
        action="store_true",
        help="Disable counter-evidence recurrence loop (single round only)",
    )
    parser.add_argument(
        "--max-counter-searches",
        type=int,
        default=3,
        help="Max adversary counter-searches to execute per recurrence loop (default: 3)",
    )
    parser.add_argument(
        "--review",
        choices=["auto", "interactive", "spl-only", "verdict-only"],
        default="auto",
        help=(
            "HITL review mode: auto (no interrupts), interactive (both gates), "
            "spl-only, verdict-only. Default: auto."
        ),
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        default=None,
        help="Resume a previously interrupted run from its checkpoint.",
    )

    args = parser.parse_args()

    if args.subcommand == "list-checkpoints":
        from aec.agent.state import list_checkpoints

        checkpoints = list_checkpoints()
        if not checkpoints:
            console.print("[yellow]No checkpoints found.[/]")
        else:
            for cp in checkpoints:
                nodes = ", ".join(cp["completed_nodes"]) if cp["completed_nodes"] else "(none)"
                console.print(
                    f"  {cp['run_id']}  control={cp['control_id']}  "
                    f"started={cp['started_at']}  nodes=[{nodes}]  "
                    f"verdict={cp['final_verdict'] or '—'}"
                )
        return

    if args.subcommand == "clean":
        from aec.agent.state import clean_checkpoints

        count = clean_checkpoints()
        console.print(f"[green]Removed {count} checkpoint(s).[/]")
        return

    # Allow AEC_SAMPLE env var as shortcut
    if not args.sample and os.environ.get("AEC_SAMPLE"):
        args.sample = os.environ["AEC_SAMPLE"]

    try:
        args.mcp = _resolve_mcp_mode(args.mcp, live=getattr(args, "live", False))
    except ValueError as exc:
        parser.error(str(exc))

    if args.resume:
        asyncio.run(_run_graph(args))
        return

    if args.compare or args.drift_window:
        if args.drift_window and not args.compare:
            args.compare = "drift-window-placeholder"
        asyncio.run(_run_drift(args))
        return

    is_multi_framework = (
        (args.control and "+" in args.control)
        or (args.concept and args.frameworks)
    )
    if is_multi_framework:
        asyncio.run(_run_multi_framework(args))
        return

    if not args.sample and not args.control:
        parser.error("Provide --sample or --control")

    review_mode = getattr(args, "review", "auto")
    use_graph = review_mode != "auto" or args.resume

    if use_graph:
        asyncio.run(_run_graph(args))
    else:
        asyncio.run(_run(args))


if __name__ == "__main__":
    main()
