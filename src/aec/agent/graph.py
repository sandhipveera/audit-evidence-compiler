"""LangGraph definition for the audit-evidence-compiler pipeline.

Wraps the existing aec_demo pipeline as a proper LangGraph with:
- Explicit typed state (AgentState)
- Durable JSON checkpointing
- HITL interrupt gates at SPL review and verdict review
- Conditional edges (validator rejection → formatter_gap)
"""
from __future__ import annotations

import logging
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from aec.agent.nodes import (
    adversary_search_validator,
    consensus,
    control_mapper,
    evidence_formatter,
    evidence_normalizer,
    formatter_gap,
    merkle_chain_sealer,
    mcp_executor,
    mcp_executor_counter,
    panel_round_1,
    panel_round_2,
    spl_generator,
    spl_validator,
    splunk_ml_anomaly,
)
from aec.agent.state import AgentState, write_checkpoint

log = logging.getLogger(__name__)


class GraphState(TypedDict, total=False):
    control_id: str
    framework: str
    time_window: dict[str, str]
    transport: str

    matched_controls: list[dict[str, Any]]
    spl_query: str | None
    spl_validation: dict[str, Any] | None
    splunk_snapshot: dict[str, Any] | None
    splunk_ml: dict[str, Any] | None
    panel_round_1: dict[str, Any] | None
    counter_searches: list[dict[str, Any]]
    panel_round_2: dict[str, Any] | None
    recurrence_result: dict[str, Any] | None
    final_verdict: str | None
    evidence_snapshots: list[dict[str, Any]]
    output_paths: dict[str, str]

    run_id: str
    started_at: str
    node_durations_ms: dict[str, int]
    interrupts_raised: list[str]
    completed_nodes: list[str]

    sample_name: str | None
    review_mode: str
    mcp_mode: str
    no_llm: bool
    enable_recurrence: bool
    max_counter_searches: int
    window_str: str


# ---------------------------------------------------------------------------
# HITL gate nodes — use LangGraph's built-in interrupt() primitive
# ---------------------------------------------------------------------------

def hitl_spl_gate(state: dict) -> dict:
    """Interrupt for human review of the generated SPL before execution."""
    review_mode = state.get("review_mode", "auto")
    if review_mode not in ("interactive", "spl-only"):
        return {}

    spl = state.get("spl_query", "")
    prompt = (
        f"\n⏸ HITL gate: review SPL before execution\n\n"
        f"SPL: {spl}\n\n"
        f"[a]pprove / [e]dit / [r]eject"
    )

    answer = interrupt({"type": "spl_review", "spl": spl, "prompt": prompt})

    if isinstance(answer, dict):
        action = answer.get("action", "approve")
        edited_spl = answer.get("spl", spl)
    else:
        action = str(answer).strip().lower()
        edited_spl = spl

    interrupts = list(state.get("interrupts_raised") or [])
    interrupts.append("spl_review")

    if action in ("r", "reject"):
        return {
            "spl_validation": {"valid": False, "error": "Rejected by human reviewer"},
            "interrupts_raised": interrupts,
        }
    if action in ("e", "edit"):
        return {
            "spl_query": edited_spl,
            "interrupts_raised": interrupts,
        }
    return {"interrupts_raised": interrupts}


def hitl_verdict_gate(state: dict) -> dict:
    """Interrupt for human review of the final verdict before formatting."""
    review_mode = state.get("review_mode", "auto")
    if review_mode not in ("interactive", "verdict-only"):
        return {}

    verdict = state.get("final_verdict", "UNKNOWN")
    prompt = (
        f"\n⏸ HITL gate: review verdict\n\n"
        f"Verdict: {verdict}\n\n"
        f"[a]pprove / [r]eject"
    )

    answer = interrupt({"type": "verdict_review", "verdict": verdict, "prompt": prompt})

    if isinstance(answer, dict):
        action = answer.get("action", "approve")
    else:
        action = str(answer).strip().lower()

    interrupts = list(state.get("interrupts_raised") or [])
    interrupts.append("verdict_review")

    if action in ("r", "reject"):
        return {
            "final_verdict": "REJECTED",
            "interrupts_raised": interrupts,
        }
    return {"interrupts_raised": interrupts}


# ---------------------------------------------------------------------------
# Checkpoint-after-node wrapper
# ---------------------------------------------------------------------------

def _checkpoint_wrapper(node_fn):
    """Wrap a node so state is checkpointed after it completes."""
    if _is_async(node_fn):
        async def _wrapped(state: dict) -> dict:
            result = await node_fn(state)
            merged = {**state, **result}
            try:
                write_checkpoint(AgentState.model_validate(merged))
            except Exception:
                log.debug("Checkpoint write skipped (validation error)")
            return result
    else:
        def _wrapped(state: dict) -> dict:
            result = node_fn(state)
            merged = {**state, **result}
            try:
                write_checkpoint(AgentState.model_validate(merged))
            except Exception:
                log.debug("Checkpoint write skipped (validation error)")
            return result
    _wrapped.__name__ = node_fn.__name__
    return _wrapped


def _is_async(fn) -> bool:
    import asyncio
    return asyncio.iscoroutinefunction(fn)


# ---------------------------------------------------------------------------
# Routing functions for conditional edges
# ---------------------------------------------------------------------------

def _route_after_spl_gate(state: dict) -> str:
    v = state.get("spl_validation")
    if isinstance(v, dict) and not v.get("valid", True):
        return "formatter_gap"
    return "mcp_executor"


def _route_after_panel_1(state: dict) -> str:
    if not state.get("enable_recurrence", True):
        return "consensus"
    r1 = state.get("panel_round_1") or {}
    critiques = r1.get("critiques", [])
    adversary = next((c for c in critiques if c.get("persona") == "adversary"), None)
    if adversary and adversary.get("recommended_additional_searches"):
        return "adversary_search_validator"
    return "consensus"


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(*, checkpointer=None, enable_checkpointing: bool = True):
    """Build and compile the audit-evidence LangGraph.

    Args:
        checkpointer: LangGraph checkpointer instance. If None and
            enable_checkpointing is True, uses MemorySaver.
        enable_checkpointing: Whether to use a checkpointer at all.

    Returns:
        Compiled LangGraph ready for .invoke() or .ainvoke().
    """
    builder = StateGraph(GraphState)

    wrap = _checkpoint_wrapper if enable_checkpointing else (lambda fn: fn)

    builder.add_node("control_mapper", wrap(control_mapper))
    builder.add_node("spl_generator", wrap(spl_generator))
    builder.add_node("spl_validator", wrap(spl_validator))
    builder.add_node("hitl_spl_gate", hitl_spl_gate)
    builder.add_node("mcp_executor", wrap(mcp_executor))
    builder.add_node("evidence_normalizer", wrap(evidence_normalizer))
    builder.add_node("splunk_ml_anomaly", wrap(splunk_ml_anomaly))
    builder.add_node("formatter_gap", wrap(formatter_gap))
    builder.add_node("panel_round_1", wrap(panel_round_1))
    builder.add_node("adversary_search_validator", wrap(adversary_search_validator))
    builder.add_node("mcp_executor_counter", wrap(mcp_executor_counter))
    builder.add_node("panel_round_2", wrap(panel_round_2))
    builder.add_node("consensus", wrap(consensus))
    builder.add_node("hitl_verdict_gate", hitl_verdict_gate)
    builder.add_node("evidence_formatter", wrap(evidence_formatter))
    builder.add_node("merkle_chain_sealer", wrap(merkle_chain_sealer))

    builder.add_edge(START, "control_mapper")
    builder.add_edge("control_mapper", "spl_generator")
    builder.add_edge("spl_generator", "spl_validator")
    builder.add_edge("spl_validator", "hitl_spl_gate")

    builder.add_conditional_edges(
        "hitl_spl_gate",
        _route_after_spl_gate,
        {"mcp_executor": "mcp_executor", "formatter_gap": "formatter_gap"},
    )

    builder.add_edge("formatter_gap", END)
    builder.add_edge("mcp_executor", "evidence_normalizer")
    builder.add_edge("evidence_normalizer", "splunk_ml_anomaly")
    builder.add_edge("splunk_ml_anomaly", "panel_round_1")

    builder.add_conditional_edges(
        "panel_round_1",
        _route_after_panel_1,
        {
            "adversary_search_validator": "adversary_search_validator",
            "consensus": "consensus",
        },
    )

    builder.add_edge("adversary_search_validator", "mcp_executor_counter")
    builder.add_edge("mcp_executor_counter", "panel_round_2")
    builder.add_edge("panel_round_2", "consensus")

    builder.add_edge("consensus", "hitl_verdict_gate")
    builder.add_edge("hitl_verdict_gate", "evidence_formatter")
    builder.add_edge("evidence_formatter", "merkle_chain_sealer")
    builder.add_edge("merkle_chain_sealer", END)

    if checkpointer is None and enable_checkpointing:
        checkpointer = MemorySaver()

    return builder.compile(checkpointer=checkpointer)


def make_initial_state(
    *,
    control_id: str,
    sample_name: str | None = None,
    review_mode: str = "auto",
    mcp_mode: str = "official",
    window_str: str = "30d",
    enable_recurrence: bool = True,
    max_counter_searches: int = 3,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build the initial state dict for graph.invoke()."""
    state = AgentState(
        control_id=control_id,
        sample_name=sample_name,
        review_mode=review_mode,
        mcp_mode=mcp_mode,
        window_str=window_str,
        enable_recurrence=enable_recurrence,
        max_counter_searches=max_counter_searches,
    )
    if run_id:
        state.run_id = run_id
    return state.model_dump()
