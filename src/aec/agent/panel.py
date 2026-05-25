"""Panel debate orchestrator — three personas, parallel execution, conservative consensus."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from aec.agent import llm_router
from aec.agent.models import (
    AdversarySearch,
    Critique,
    PanelResult,
    PanelResultWithRecurrence,
    PersonaSpec,
    TransportSpec,
    VERDICT_SEVERITY,
)

log = logging.getLogger(__name__)

PERSONA_DIR = files("aec.agent") / "personas"
PERSONA_NAMES = ("auditor", "engineer", "adversary")
SINGLE_VENDOR_FALLBACK_CHAIN = (
    TransportSpec(name="anthropic-cli"),
    TransportSpec(name="anthropic-api", config={"model": "claude-sonnet-4-6"}),
)


def load_persona(name: str, persona_dir: Path | None = None) -> PersonaSpec:
    """Load a persona spec from its markdown file with YAML frontmatter."""
    if persona_dir is None:
        md_path = PERSONA_DIR / f"{name}.md"
        raw = md_path.read_text(encoding="utf-8")
    else:
        md_path = Path(persona_dir) / f"{name}.md"
        raw = md_path.read_text(encoding="utf-8")

    if not raw.startswith("---"):
        raise ValueError(f"Persona {name}.md missing YAML frontmatter")

    _, fm_raw, body = raw.split("---", 2)
    meta = yaml.safe_load(fm_raw)

    transports: list[TransportSpec] = []
    for entry in meta.get("transports", []):
        if isinstance(entry, str):
            transports.append(TransportSpec(name=entry))
        elif isinstance(entry, dict):
            for tname, tconfig in entry.items():
                transports.append(TransportSpec(name=tname, config=tconfig or {}))

    return PersonaSpec(
        persona=meta["persona"],
        transports=transports,
        temperature=meta.get("temperature", 0.5),
        system_prompt=body.strip(),
    )


def _build_user_prompt(
    snapshot: dict[str, Any],
    control_text: str,
    spl_executed: str,
    splunk_snapshot: dict[str, Any] | None = None,
) -> str:
    """Build the user prompt sent to all three personas."""
    snapshot_json = json.dumps(snapshot, indent=2, ensure_ascii=False)
    parts = [
        f"## Control requirement\n\n{control_text}\n\n",
        f"## SPL query executed\n\n```spl\n{spl_executed}\n```\n\n",
        f"## Evidence snapshot\n\n```json\n{snapshot_json}\n```\n\n",
    ]
    if splunk_snapshot is not None:
        splunk_json = json.dumps(splunk_snapshot, indent=2, ensure_ascii=False)
        parts.append(f"## Splunk snapshot\n\n```json\n{splunk_json}\n```\n\n")
    parts.append(
        "Evaluate this evidence against the control requirement. "
        "Respond with a single JSON object as specified in your instructions."
    )
    return "".join(parts)


def _parse_critique_json(raw_text: str) -> dict[str, Any]:
    """Extract JSON from LLM response, handling markdown fences."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)


async def _run_persona(
    persona: PersonaSpec,
    user_prompt: str,
    force_fallback_used: bool = False,
) -> Critique:
    """Run a single persona and return a Critique."""
    start = time.monotonic()
    result, fallback_used = await llm_router.complete(persona, user_prompt)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    parsed = _parse_critique_json(result.text)

    return Critique(
        persona=persona.persona,
        model=result.model,
        transport=result.transport_name,
        verdict=parsed["verdict"],
        confidence=parsed.get("confidence", 0.5),
        rationale=parsed.get("rationale", ""),
        concerns=parsed.get("concerns", []),
        recommended_additional_searches=parsed.get("recommended_additional_searches", []),
        latency_ms=elapsed_ms,
        fallback_used=force_fallback_used or fallback_used,
    )


def _compute_consensus(critiques: list[Critique]) -> str:
    """Lowest verdict wins. Severity: PASS < PARTIAL < FAIL < INSUFFICIENT."""
    if not critiques:
        return "INSUFFICIENT"
    return max(critiques, key=lambda c: VERDICT_SEVERITY[c.verdict]).verdict


def _format_followup_section(followups: list[dict[str, Any]]) -> str:
    """Render adversary follow-up searches for transcript output."""
    if not followups:
        return ""

    lines = ["## Adversary follow-up searches", ""]
    for result in followups:
        status = "OK" if result.get("ok") else "ERROR"
        lines.append(f"### `{result.get('query', '')}`")
        lines.append(f"- Status: {status}")
        lines.append(f"- Hit count: {result.get('hit_count', 0)}")
        if result.get("error"):
            lines.append(f"- Error: {result['error']}")
        sample = result.get("sample") or []
        if sample:
            lines.append("- Sample:")
            lines.append("```json")
            lines.append(json.dumps(sample[:3], indent=2, ensure_ascii=False))
            lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_transcript(
    critiques: list[Critique],
    final_verdict: str,
    splunk_snapshot: dict[str, Any] | None = None,
    followups: list[dict[str, Any]] | None = None,
) -> str:
    """Render the panel debate as a markdown transcript."""
    lines = ["# Panel Debate Transcript\n"]
    if splunk_snapshot is not None:
        lines.append("## Splunk snapshot")
        lines.append("```json")
        lines.append(json.dumps(splunk_snapshot, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")
    for c in critiques:
        lines.append(f"## {c.persona.upper()} ({c.model} via {c.transport})")
        lines.append(f"**Verdict:** {c.verdict} (confidence: {c.confidence:.0%})")
        lines.append(f"**Rationale:** {c.rationale}")
        if c.concerns:
            lines.append("**Concerns:**")
            for concern in c.concerns:
                lines.append(f"- {concern}")
        if c.recommended_additional_searches:
            lines.append("**Recommended additional searches:**")
            for spl in c.recommended_additional_searches:
                lines.append(f"- `{spl}`")
        lines.append(f"*Latency: {c.latency_ms}ms | Fallback: {c.fallback_used}*")
        lines.append("")
    lines.append(f"## Consensus: **{final_verdict}** (lowest-of-three)")
    followup_section = _format_followup_section(followups or [])
    if followup_section:
        lines.append("")
        lines.append(followup_section.rstrip())
    return "\n".join(lines)


def _single_vendor_fallback_enabled() -> bool:
    return os.environ.get("AEC_PANEL_SINGLE_VENDOR_FALLBACK", "true").lower() not in {
        "0",
        "false",
        "no",
    }


def _as_single_vendor_persona(persona: PersonaSpec) -> PersonaSpec:
    """Keep the persona prompt, but force the transport chain to Claude."""
    return PersonaSpec(
        persona=persona.persona,
        transports=list(SINGLE_VENDOR_FALLBACK_CHAIN),
        temperature=persona.temperature,
        system_prompt=persona.system_prompt,
    )


def _mode_for(critiques: list[Critique], single_vendor_fallback: bool = False) -> str:
    if single_vendor_fallback:
        return "single-vendor"
    if len(critiques) >= 2 and len({c.transport.split("-")[0] for c in critiques}) >= 2:
        return "multi-vendor"
    if critiques:
        return "single-vendor"
    return "failed"


async def run_panel(
    snapshot: dict[str, Any],
    control_text: str,
    spl_executed: str,
    persona_dir: Path | None = None,
    view: Any | None = None,
    splunk_snapshot: dict[str, Any] | None = None,
    splunk_client: Any | None = None,
    time_window: str = "30d",
) -> PanelResult:
    """Run the three-persona panel debate and return the result.

    Falls back gracefully:
      - 3 personas → multi-vendor (ideal)
      - 2 personas → degraded multi-vendor
      - 1 persona  → rerun all three prompts through Claude single-vendor mode
      - 0 personas → raises RuntimeError
    """
    personas: list[PersonaSpec] = []
    for name in PERSONA_NAMES:
        try:
            personas.append(load_persona(name, persona_dir))
        except Exception as exc:
            log.warning("Failed to load persona %s: %s", name, exc)

    if not personas:
        raise RuntimeError("No personas could be loaded")

    user_prompt = _build_user_prompt(snapshot, control_text, spl_executed, splunk_snapshot)

    async def _invoke(persona: PersonaSpec, force_fallback_used: bool = False) -> Critique | None:
        if view:
            view.update(persona.persona, "running...")
        try:
            critique = await _run_persona(persona, user_prompt, force_fallback_used)
            if view:
                view.update(persona.persona, critique.rationale, critique.verdict)
            return critique
        except Exception as exc:
            log.warning("Persona %s failed entirely: %s", persona.persona, exc)
            if view:
                view.update(persona.persona, f"FAILED: {exc}")
            return None

    results = await asyncio.gather(*[_invoke(p) for p in personas])
    critiques = [c for c in results if c is not None]

    if not critiques:
        raise RuntimeError("All personas failed — no critiques produced")

    used_single_vendor_fallback = False
    if len(critiques) == 1 and _single_vendor_fallback_enabled():
        log.warning(
            "Only one persona succeeded; rerunning all personas in single-vendor fallback mode"
        )
        if view:
            for persona in personas:
                view.update(persona.persona, "single-vendor fallback...")

        fallback_results = await asyncio.gather(
            *[
                _invoke(_as_single_vendor_persona(persona), force_fallback_used=True)
                for persona in personas
            ]
        )
        fallback_critiques = [c for c in fallback_results if c is not None]
        if fallback_critiques:
            critiques = fallback_critiques
            used_single_vendor_fallback = True

    final_verdict = _compute_consensus(critiques)
    degraded = len(critiques) < 3 or used_single_vendor_fallback
    mode = _mode_for(critiques, used_single_vendor_fallback)

    followups: list[dict[str, Any]] = []
    run_followups = os.environ.get("AEC_RUN_ADVERSARY_SEARCHES", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    adversary = next((c for c in critiques if c.persona == "adversary"), None)
    if (
        run_followups
        and splunk_client is not None
        and adversary is not None
        and adversary.recommended_additional_searches
    ):
        from aec.splunk.spl_validator import run_spl

        for query in adversary.recommended_additional_searches:
            result = run_spl(query, time_window=time_window, client=splunk_client)
            followups.append({"query": query, **result})

    transcript = _render_transcript(critiques, final_verdict, splunk_snapshot, followups)

    panel_result = PanelResult(
        critiques=critiques,
        final_verdict=final_verdict,
        consensus_method="lowest_of_three",
        transcript=transcript,
        degraded=degraded,
        mode=mode,
        splunk_snapshot=splunk_snapshot,
        adversary_followups=followups,
    )

    if view:
        view.finish(final_verdict, "lowest_of_three")

    return panel_result


def _render_recurrence_transcript(
    round_1: PanelResult,
    round_2: PanelResult | None,
    counter_searches: list[AdversarySearch],
) -> str:
    """Render a combined transcript showing both rounds and counter-search results."""
    lines = ["# Panel Debate Transcript\n"]

    lines.append("## Round 1\n")
    lines.append(round_1.transcript)
    lines.append("")

    if counter_searches:
        executed_count = sum(1 for s in counter_searches if s.executed)
        total_count = len(counter_searches)
        lines.append("## Counter-evidence loop\n")
        lines.append(
            f"The Adversary recommended {total_count} follow-up searches. "
            f"Executed {executed_count} via MCP.\n"
        )
        for i, search in enumerate(counter_searches, 1):
            lines.append(f"### Search {i}: `{search.spl}`")
            lines.append(f"- Validation: {search.validation_status}")
            if search.validation_status == "rejected":
                lines.append(f"  Reason: {search.rejection_reason}")
                lines.append("- Result: not executed")
            elif search.executed:
                lines.append(
                    f"- Result: {search.row_count} events, {search.execution_time_ms}ms"
                )
                if search.sample_events:
                    lines.append(f"- Sample event: {json.dumps(search.sample_events[0])}")
                if search.error:
                    lines.append(f"- Error: {search.error}")
            lines.append("")

    if round_2 is not None:
        lines.append("## Round 2 panel debate\n")
        lines.append(round_2.transcript)
        lines.append("")

        lines.append("### What changed")
        r1_map = {c.persona: c.verdict for c in round_1.critiques}
        r2_map = {c.persona: c.verdict for c in round_2.critiques}
        for persona in ("auditor", "engineer", "adversary"):
            v1 = r1_map.get(persona, "N/A")
            v2 = r2_map.get(persona, "N/A")
            change = "no change" if v1 == v2 else f"{v1} → {v2}"
            lines.append(f"- {persona.capitalize()}: {change}")
        consensus_change = (
            "no change"
            if round_1.final_verdict == round_2.final_verdict
            else f"{round_1.final_verdict} → {round_2.final_verdict}"
        )
        lines.append(f"- Consensus: {consensus_change} (round 2 supersedes)")

    return "\n".join(lines)


async def run_panel_with_recurrence(
    snapshot: dict[str, Any],
    control_text: str,
    spl_executed: str,
    persona_dir: Path | None = None,
    view: Any | None = None,
    splunk_snapshot: dict[str, Any] | None = None,
    splunk_client: Any | None = None,
    time_window: str = "30d",
    enable_recurrence: bool = True,
    max_counter_searches: int = 3,
) -> PanelResultWithRecurrence:
    """Run panel debate with optional counter-evidence recurrence loop.

    After round 1, if the Adversary recommends additional searches and recurrence
    is enabled, those searches are validated, executed, and fed into a second round.
    Round 2's verdict supersedes round 1.
    """
    recurrence_enabled = enable_recurrence and os.environ.get(
        "AEC_RUN_ADVERSARY_SEARCHES", "true"
    ).lower() not in {"0", "false", "no"}

    if not recurrence_enabled:
        log.info("Counter-evidence loop disabled")

    round_1 = await run_panel(
        snapshot=snapshot,
        control_text=control_text,
        spl_executed=spl_executed,
        persona_dir=persona_dir,
        view=view,
        splunk_snapshot=splunk_snapshot,
        splunk_client=None,
        time_window=time_window,
    )

    adversary = next((c for c in round_1.critiques if c.persona == "adversary"), None)
    recommended = adversary.recommended_additional_searches if adversary else []

    if not recurrence_enabled or not recommended:
        return PanelResultWithRecurrence(
            round_1=round_1,
            round_2=None,
            counter_searches=[],
            final_verdict=round_1.final_verdict,
            final_consensus_round=1,
            transcript=round_1.transcript,
            iteration_count=1,
        )

    from aec.splunk.spl_validator import _validate_spl_syntax

    capped = recommended[:max_counter_searches]
    counter_searches: list[AdversarySearch] = []

    for spl_query in capped:
        validation_error = _validate_spl_syntax(spl_query)
        if validation_error:
            counter_searches.append(AdversarySearch(
                spl=spl_query,
                validation_status="rejected",
                rejection_reason=validation_error,
                executed=False,
            ))
            continue

        if splunk_client is None:
            counter_searches.append(AdversarySearch(
                spl=spl_query,
                validation_status="accepted",
                executed=False,
                error="No Splunk client available",
            ))
            continue

        start_ms = time.monotonic()
        from aec.splunk.spl_validator import run_spl

        result = run_spl(spl_query, time_window=time_window, client=splunk_client)
        elapsed_ms = int((time.monotonic() - start_ms) * 1000)

        counter_searches.append(AdversarySearch(
            spl=spl_query,
            validation_status="accepted",
            executed=True,
            row_count=result.get("hit_count", 0),
            sample_events=result.get("sample", []),
            execution_time_ms=elapsed_ms,
            error=result.get("error"),
        ))

    executed_searches = [s for s in counter_searches if s.executed]
    if not executed_searches:
        transcript = _render_recurrence_transcript(round_1, None, counter_searches)
        return PanelResultWithRecurrence(
            round_1=round_1,
            round_2=None,
            counter_searches=counter_searches,
            final_verdict=round_1.final_verdict,
            final_consensus_round=1,
            transcript=transcript,
            iteration_count=1,
        )

    augmented_snapshot = {
        **snapshot,
        "iteration": 2,
        "counter_searches": [s.model_dump() for s in executed_searches],
    }

    round_2_timeout = 60.0
    try:
        round_2 = await asyncio.wait_for(
            run_panel(
                snapshot=augmented_snapshot,
                control_text=control_text,
                spl_executed=spl_executed,
                persona_dir=persona_dir,
                view=view,
                splunk_snapshot=splunk_snapshot,
                splunk_client=None,
                time_window=time_window,
            ),
            timeout=round_2_timeout,
        )
    except asyncio.TimeoutError:
        log.warning("Round 2 timed out after %.0fs — using round 1 verdict", round_2_timeout)
        transcript = _render_recurrence_transcript(round_1, None, counter_searches)
        return PanelResultWithRecurrence(
            round_1=round_1,
            round_2=None,
            counter_searches=counter_searches,
            final_verdict=round_1.final_verdict,
            final_consensus_round=1,
            transcript=transcript,
            iteration_count=1,
        )

    transcript = _render_recurrence_transcript(round_1, round_2, counter_searches)
    return PanelResultWithRecurrence(
        round_1=round_1,
        round_2=round_2,
        counter_searches=counter_searches,
        final_verdict=round_2.final_verdict,
        final_consensus_round=2,
        transcript=transcript,
        iteration_count=2,
    )


def _format_transcript_file(result: PanelResult, control_id: str, snapshot_name: str) -> str:
    """Format a PanelResult into the structured transcript markdown for disk."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    persona_status = ", ".join(
        f"{c.persona}={'ok' if not c.fallback_used else 'fallback'}"
        for c in result.critiques
    )
    lines = [
        f"# Panel Debate — {control_id} on {snapshot_name}",
        f"Generated: {ts}",
        f"Consensus: {result.final_verdict}",
        f"Personas: {persona_status}",
        "",
    ]
    if result.splunk_snapshot is not None:
        lines.append("## Splunk snapshot")
        lines.append("```json")
        lines.append(json.dumps(result.splunk_snapshot, indent=2, ensure_ascii=False))
        lines.append("```")
        lines.append("")

    for c in result.critiques:
        lines.append(f"## {c.persona.capitalize()}")
        lines.append(f"- Model: {c.model}")
        lines.append(f"- Transport: {c.transport}")
        lines.append(f"- Verdict: {c.verdict}")
        lines.append(f"- Reasoning: {c.rationale}")
        lines.append("- Gaps identified:")
        if c.concerns:
            for concern in c.concerns:
                lines.append(f"  - {concern}")
        else:
            lines.append("  - (none)")
        lines.append("- Recommended additional searches:")
        if c.recommended_additional_searches:
            for spl in c.recommended_additional_searches:
                lines.append(f"  - {spl}")
        else:
            lines.append("  - (none)")
        lines.append("")

    lines.append("## Consensus")
    lines.append(
        f"Most conservative verdict from the three personas: {result.final_verdict}"
    )
    lines.append(
        f"Rationale: {result.consensus_method} — the highest-severity verdict dominates."
    )
    if result.adversary_followups:
        lines.append("")
        lines.append(_format_followup_section(result.adversary_followups).rstrip())
    return "\n".join(lines) + "\n"


async def main() -> None:
    """CLI entry point for standalone panel testing."""
    import argparse
    from datetime import datetime, timezone

    parser = argparse.ArgumentParser(description="Run three-agent panel debate")
    parser.add_argument("--snapshot", required=True, help="Path to snapshot JSON file")
    parser.add_argument("--control", required=True, help="Control ID (e.g., CC6.1)")
    parser.add_argument("--control-text", default="", help="Full control requirement text")
    parser.add_argument("--no-tui", action="store_true", help="Disable rich TUI")
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    spl = snapshot.get("spl_executed", "")
    control_text = args.control_text or f"Control {args.control}"

    view = None
    if not args.no_tui:
        from aec.agent.panel_view import PanelView
        view = PanelView()
        view.start()

    try:
        result = await run_panel(
            snapshot=snapshot,
            control_text=control_text,
            spl_executed=spl,
            view=view,
        )
    finally:
        if view:
            view.stop()

    print(result.model_dump_json(indent=2))

    # Persist transcript to disk
    out_dir = Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    snapshot_name = snapshot.get("snapshot_id", "unknown")
    transcript_path = out_dir / f"transcript_{ts}.md"
    transcript_content = _format_transcript_file(result, args.control, snapshot_name)
    transcript_path.write_text(transcript_content, encoding="utf-8")
    print(f"Wrote transcript to {transcript_path}")


if __name__ == "__main__":
    asyncio.run(main())
