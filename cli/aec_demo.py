"""aec_demo — hackathon demo entry point.

Usage:
    aec_demo --sample soc2-cc61
    aec_demo --control CC6.1 --window 30d
    aec_demo --sample soc2-cc61 --no-llm
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
    control_id: str, window: str, mcp_router,
) -> dict:
    """Fetch live snapshot from Splunk via MCP transport."""
    from aec.splunk.snapshot import SPL_BY_CONTROL, _infer_framework

    spl = SPL_BY_CONTROL.get(control_id, f"index=main control_id={control_id}")
    earliest = f"-{window}" if not window.startswith("-") else window

    result = await mcp_router.execute_spl(spl, time_window=earliest)

    from datetime import datetime, timezone

    return {
        "control_id": control_id,
        "framework": _infer_framework(control_id),
        "snapshot_name": (
            f"{_infer_framework(control_id).lower()}-{control_id.lower().replace('.', '')}"
        ),
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_range": {"earliest": earliest, "latest": "now"},
        "search": spl,
        "event_count": result.get("event_count", 0),
        "sample_events": result.get("results", [])[:10],
        "aggregations": {},
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit Evidence Compiler — Splunk hackathon demo"
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

    args = parser.parse_args()

    # Allow AEC_SAMPLE env var as shortcut
    if not args.sample and os.environ.get("AEC_SAMPLE"):
        args.sample = os.environ["AEC_SAMPLE"]

    try:
        args.mcp = _resolve_mcp_mode(args.mcp, live=args.live)
    except ValueError as exc:
        parser.error(str(exc))

    if not args.sample and not args.control:
        parser.error("Provide --sample or --control")

    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
