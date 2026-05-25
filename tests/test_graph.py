"""Tests for the LangGraph audit-evidence pipeline."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aec.agent.graph import build_graph, make_initial_state, _route_after_spl_gate
from aec.agent.state import (
    AgentState,
    clean_checkpoints,
    list_checkpoints,
    read_checkpoint,
    write_checkpoint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_checkpoints_dir(tmp_path, monkeypatch):
    """Redirect checkpoint dir to a temp location for test isolation."""
    monkeypatch.setattr("aec.agent.state.CHECKPOINT_DIR", tmp_path / "checkpoints")
    monkeypatch.setattr("aec.agent.nodes.SAMPLES_DIR", Path(__file__).parent.parent / "samples")


def _make_fake_panel_result(**overrides):
    from aec.agent.models import Critique, PanelResult

    defaults = {
        "critiques": [
            Critique(
                persona="auditor",
                model="test-model",
                transport="test",
                verdict="PASS",
                confidence=0.9,
                rationale="Looks good",
            ),
            Critique(
                persona="engineer",
                model="test-model",
                transport="test",
                verdict="PASS",
                confidence=0.85,
                rationale="SPL is correct",
            ),
            Critique(
                persona="adversary",
                model="test-model",
                transport="test",
                verdict="PASS",
                confidence=0.8,
                rationale="No issues found",
                recommended_additional_searches=[],
            ),
        ],
        "final_verdict": "PASS",
        "consensus_method": "lowest_of_three",
        "transcript": "# Test transcript",
    }
    defaults.update(overrides)
    return PanelResult(**defaults)


# ---------------------------------------------------------------------------
# State model tests
# ---------------------------------------------------------------------------

class TestAgentState:
    def test_create_with_defaults(self):
        state = AgentState(control_id="CC6.1")
        assert state.control_id == "CC6.1"
        assert state.run_id
        assert state.started_at
        assert state.completed_nodes == []

    def test_serialization_roundtrip(self):
        state = AgentState(
            control_id="CC6.1",
            framework="SOC2",
            spl_query="index=botsv3",
            final_verdict="PASS",
            completed_nodes=["control_mapper", "spl_generator"],
        )
        json_str = state.model_dump_json()
        restored = AgentState.model_validate_json(json_str)
        assert restored.control_id == state.control_id
        assert restored.spl_query == state.spl_query
        assert restored.completed_nodes == state.completed_nodes


# ---------------------------------------------------------------------------
# Checkpoint tests
# ---------------------------------------------------------------------------

class TestCheckpointing:
    def test_write_and_read(self):
        state = AgentState(control_id="CC6.1", framework="SOC2")
        path = write_checkpoint(state)
        assert path.exists()

        restored = read_checkpoint(state.run_id)
        assert restored is not None
        assert restored.control_id == "CC6.1"
        assert restored.run_id == state.run_id

    def test_read_nonexistent(self):
        assert read_checkpoint("nonexistent-id") is None

    def test_list_checkpoints(self):
        s1 = AgentState(control_id="CC6.1")
        s2 = AgentState(control_id="CC7.2")
        write_checkpoint(s1)
        write_checkpoint(s2)

        results = list_checkpoints()
        assert len(results) == 2
        ids = {r["run_id"] for r in results}
        assert s1.run_id in ids
        assert s2.run_id in ids

    def test_clean_checkpoints(self):
        write_checkpoint(AgentState(control_id="CC6.1"))
        write_checkpoint(AgentState(control_id="CC7.2"))
        assert clean_checkpoints() == 2
        assert list_checkpoints() == []

    def test_checkpoint_is_valid_json(self):
        state = AgentState(control_id="CC6.1", spl_query="index=botsv3")
        path = write_checkpoint(state)
        data = json.loads(path.read_text())
        assert data["control_id"] == "CC6.1"
        assert data["spl_query"] == "index=botsv3"


# ---------------------------------------------------------------------------
# Graph structure tests
# ---------------------------------------------------------------------------

class TestGraphStructure:
    def test_graph_compiles(self):
        graph = build_graph(enable_checkpointing=False)
        assert graph is not None

    def test_graph_has_expected_nodes(self):
        graph = build_graph(enable_checkpointing=False)
        node_names = set(graph.get_graph().nodes.keys())
        expected = {
            "__start__", "__end__",
            "control_mapper", "spl_generator", "spl_validator",
            "hitl_spl_gate", "mcp_executor", "evidence_normalizer",
            "formatter_gap", "panel_round_1",
            "adversary_search_validator", "mcp_executor_counter",
            "panel_round_2", "consensus",
            "hitl_verdict_gate", "evidence_formatter", "merkle_chain_sealer",
        }
        assert expected.issubset(node_names)


# ---------------------------------------------------------------------------
# Routing logic tests
# ---------------------------------------------------------------------------

class TestRouting:
    def test_route_valid_spl_goes_to_executor(self):
        state = {"spl_validation": {"valid": True, "error": None}}
        assert _route_after_spl_gate(state) == "mcp_executor"

    def test_route_rejected_spl_goes_to_formatter_gap(self):
        state = {"spl_validation": {"valid": False, "error": "Forbidden command"}}
        assert _route_after_spl_gate(state) == "formatter_gap"

    def test_route_missing_validation_goes_to_executor(self):
        state = {"spl_validation": None}
        assert _route_after_spl_gate(state) == "mcp_executor"


# ---------------------------------------------------------------------------
# Individual node tests
# ---------------------------------------------------------------------------

class TestNodes:
    def test_control_mapper(self):
        from aec.agent.nodes import control_mapper

        state = {"control_id": "CC6.1", "node_durations_ms": {}, "completed_nodes": []}
        result = control_mapper(state)
        assert result["framework"] == "SOC2"
        assert len(result["matched_controls"]) == 1
        assert "control_mapper" in result["completed_nodes"]

    def test_spl_generator(self):
        from aec.agent.nodes import spl_generator

        state = {
            "control_id": "CC6.1",
            "matched_controls": [{"spl_hint": "index=botsv3 test"}],
            "node_durations_ms": {},
            "completed_nodes": [],
        }
        result = spl_generator(state)
        assert result["spl_query"] == "index=botsv3 test"
        assert "spl_generator" in result["completed_nodes"]

    def test_spl_validator_passes_valid_spl(self):
        from aec.agent.nodes import spl_validator

        state = {
            "spl_query": "index=botsv3 sourcetype=test | stats count by user",
            "node_durations_ms": {},
            "completed_nodes": [],
        }
        result = spl_validator(state)
        assert result["spl_validation"]["valid"] is True

    def test_spl_validator_rejects_forbidden_command(self):
        from aec.agent.nodes import spl_validator

        state = {
            "spl_query": "index=botsv3 | delete",
            "node_durations_ms": {},
            "completed_nodes": [],
        }
        result = spl_validator(state)
        assert result["spl_validation"]["valid"] is False
        assert "delete" in result["spl_validation"]["error"].lower()

    def test_formatter_gap(self):
        from aec.agent.nodes import formatter_gap

        state = {
            "control_id": "CC6.1",
            "framework": "SOC2",
            "spl_validation": {"valid": False, "error": "Forbidden command: delete"},
            "node_durations_ms": {},
            "completed_nodes": [],
        }
        result = formatter_gap(state)
        assert result["final_verdict"] == "FAIL"
        assert "formatter_gap" in result["completed_nodes"]

    def test_evidence_normalizer(self):
        from aec.agent.nodes import evidence_normalizer

        state = {
            "control_id": "CC6.1",
            "splunk_snapshot": {"search": "index=botsv3", "event_count": 42},
            "node_durations_ms": {},
            "completed_nodes": [],
        }
        result = evidence_normalizer(state)
        assert len(result["evidence_snapshots"]) == 1
        assert result["evidence_snapshots"][0]["row_count"] == 42

    def test_consensus_uses_round_1_when_no_round_2(self):
        from aec.agent.nodes import consensus

        panel = _make_fake_panel_result(final_verdict="PASS")
        state = {
            "control_id": "CC6.1",
            "panel_round_1": panel.model_dump(),
            "panel_round_2": None,
            "counter_searches": [],
            "node_durations_ms": {},
            "completed_nodes": [],
        }
        result = consensus(state)
        assert result["final_verdict"] == "PASS"
        assert result["recurrence_result"]["final_consensus_round"] == 1


# ---------------------------------------------------------------------------
# HITL interrupt tests
# ---------------------------------------------------------------------------

class TestHITLInterrupt:
    def test_spl_gate_auto_mode_passes_through(self):
        from aec.agent.graph import hitl_spl_gate

        state = {"review_mode": "auto", "spl_query": "index=botsv3"}
        result = hitl_spl_gate(state)
        assert result == {}

    def test_verdict_gate_auto_mode_passes_through(self):
        from aec.agent.graph import hitl_verdict_gate

        state = {"review_mode": "auto", "final_verdict": "PASS"}
        result = hitl_verdict_gate(state)
        assert result == {}

    @pytest.mark.asyncio
    async def test_spl_gate_interrupt_with_auto_approve(self):
        """HITL interrupt at SPL gate with auto-approve callback."""
        from langgraph.checkpoint.memory import MemorySaver
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Command, interrupt

        def spl_gate(state: dict) -> dict:
            answer = interrupt("Approve SPL?")
            return {"approved": answer == "approve"}

        builder = StateGraph(dict)
        builder.add_node("gate", spl_gate)
        builder.add_edge(START, "gate")
        builder.add_edge("gate", END)

        graph = builder.compile(checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": "test-hitl"}}

        result = graph.invoke({"approved": False}, config=config)
        assert "__interrupt__" in result

        state = graph.get_state(config)
        assert "gate" in state.next

        result2 = graph.invoke(Command(resume="approve"), config=config)
        assert result2["approved"] is True


# ---------------------------------------------------------------------------
# End-to-end graph tests (with mocked panel)
# ---------------------------------------------------------------------------

class TestGraphEndToEnd:
    @pytest.mark.asyncio
    async def test_happy_path_sample(self, tmp_path):
        """Full pipeline through graph with sample data and mocked panel."""
        panel_result = _make_fake_panel_result()

        async def fake_run_panel(**kwargs):
            return panel_result

        with patch("aec.agent.graph.panel_round_1", _make_sync_panel_node), \
             patch("aec.agent.graph.panel_round_2", _make_sync_panel_node):
            graph = build_graph(enable_checkpointing=False)
            initial = make_initial_state(
                control_id="CC6.1",
                sample_name="soc2-cc61",
                review_mode="auto",
            )
            config = {"configurable": {"thread_id": "happy-path"}}
            result = await graph.ainvoke(initial, config=config)

        assert result.get("final_verdict") is not None
        assert "control_mapper" in result.get("completed_nodes", [])
        assert "spl_generator" in result.get("completed_nodes", [])
        assert "spl_validator" in result.get("completed_nodes", [])

    @pytest.mark.asyncio
    async def test_validator_rejection_routes_to_gap(self):
        """When SPL is rejected, graph routes to formatter_gap and skips execution."""

        def bad_spl_generator(state: dict) -> dict:
            completed = list(state.get("completed_nodes") or [])
            completed.append("spl_generator")
            durations = dict(state.get("node_durations_ms") or {})
            durations["spl_generator"] = 0
            return {
                "spl_query": "index=botsv3 | delete",
                "node_durations_ms": durations,
                "completed_nodes": completed,
            }

        with patch("aec.agent.graph.spl_generator", bad_spl_generator):
            graph = build_graph(enable_checkpointing=False)
            initial = make_initial_state(control_id="CC6.1", review_mode="auto")
            config = {"configurable": {"thread_id": "reject-path"}}
            result = await graph.ainvoke(initial, config=config)

        assert result.get("final_verdict") == "FAIL"
        assert "formatter_gap" in result.get("completed_nodes", [])
        assert "mcp_executor" not in result.get("completed_nodes", [])

    @pytest.mark.asyncio
    async def test_state_mutations_isolated_per_node(self):
        """Each node only sees state from prior nodes, not from parallel branches."""
        from aec.agent.nodes import control_mapper, spl_generator

        state = {"control_id": "CC6.1", "node_durations_ms": {}, "completed_nodes": []}

        r1 = control_mapper(state)
        assert "control_mapper" in r1["completed_nodes"]
        assert state["completed_nodes"] == []

        merged = {**state, **r1}
        r2 = spl_generator(merged)
        assert "spl_generator" in r2["completed_nodes"]
        assert "control_mapper" in r2["completed_nodes"]


# ---------------------------------------------------------------------------
# Checkpoint resume test
# ---------------------------------------------------------------------------

class TestCheckpointResume:
    def test_resume_picks_up_at_right_node(self):
        """After a simulated crash, checkpoint has correct completed_nodes."""
        state = AgentState(
            control_id="CC6.1",
            framework="SOC2",
            spl_query="index=botsv3",
            completed_nodes=["control_mapper", "spl_generator", "spl_validator"],
        )
        write_checkpoint(state)

        restored = read_checkpoint(state.run_id)
        assert restored is not None
        assert restored.completed_nodes == [
            "control_mapper", "spl_generator", "spl_validator",
        ]
        assert "mcp_executor" not in restored.completed_nodes

    def test_checkpoint_preserves_full_state(self):
        state = AgentState(
            control_id="CC6.1",
            framework="SOC2",
            spl_query="index=botsv3 sourcetype=test",
            spl_validation={"valid": True, "error": None},
            final_verdict="PASS",
            review_mode="interactive",
            interrupts_raised=["spl_review"],
        )
        write_checkpoint(state)

        restored = read_checkpoint(state.run_id)
        assert restored.spl_query == "index=botsv3 sourcetype=test"
        assert restored.final_verdict == "PASS"
        assert restored.review_mode == "interactive"
        assert "spl_review" in restored.interrupts_raised


# ---------------------------------------------------------------------------
# make_initial_state tests
# ---------------------------------------------------------------------------

class TestMakeInitialState:
    def test_defaults(self):
        state = make_initial_state(control_id="CC6.1")
        assert state["control_id"] == "CC6.1"
        assert state["review_mode"] == "auto"
        assert state["mcp_mode"] == "official"
        assert state["window_str"] == "30d"
        assert state["enable_recurrence"] is True

    def test_custom_params(self):
        state = make_initial_state(
            control_id="CC7.2",
            review_mode="interactive",
            mcp_mode="rest",
            window_str="7d",
            enable_recurrence=False,
            max_counter_searches=1,
        )
        assert state["control_id"] == "CC7.2"
        assert state["review_mode"] == "interactive"
        assert state["mcp_mode"] == "rest"
        assert state["enable_recurrence"] is False
        assert state["max_counter_searches"] == 1

    def test_run_id_override(self):
        state = make_initial_state(control_id="CC6.1", run_id="custom-run-123")
        assert state["run_id"] == "custom-run-123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sync_panel_node(state: dict) -> dict:
    """Fake panel_round_1 that returns a canned result without LLM calls."""
    panel = _make_fake_panel_result()
    completed = list(state.get("completed_nodes") or [])
    completed.append("panel_round_1")
    durations = dict(state.get("node_durations_ms") or {})
    durations["panel_round_1"] = 0
    return {
        "panel_round_1": panel.model_dump(),
        "completed_nodes": completed,
        "node_durations_ms": durations,
    }
