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
    Critique,
    PanelResult,
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
) -> str:
    """Build the user prompt sent to all three personas."""
    snapshot_json = json.dumps(snapshot, indent=2, ensure_ascii=False)
    return (
        f"## Control requirement\n\n{control_text}\n\n"
        f"## SPL query executed\n\n```spl\n{spl_executed}\n```\n\n"
        f"## Evidence snapshot\n\n```json\n{snapshot_json}\n```\n\n"
        "Evaluate this evidence against the control requirement. "
        "Respond with a single JSON object as specified in your instructions."
    )


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


def _render_transcript(critiques: list[Critique], final_verdict: str) -> str:
    """Render the panel debate as a markdown transcript."""
    lines = ["# Panel Debate Transcript\n"]
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

    user_prompt = _build_user_prompt(snapshot, control_text, spl_executed)

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
    transcript = _render_transcript(critiques, final_verdict)

    panel_result = PanelResult(
        critiques=critiques,
        final_verdict=final_verdict,
        consensus_method="lowest_of_three",
        transcript=transcript,
        degraded=degraded,
        mode=mode,
    )

    if view:
        view.finish(final_verdict, "lowest_of_three")

    return panel_result


async def main() -> None:
    """CLI entry point for standalone panel testing."""
    import argparse

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


if __name__ == "__main__":
    asyncio.run(main())
