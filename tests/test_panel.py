"""Tests for three-agent panel debate — mocked transports, real consensus logic."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aec.agent.models import Critique, PersonaSpec, VERDICT_SEVERITY
from aec.agent.panel import (
    _compute_consensus,
    _parse_critique_json,
    _render_transcript,
    load_persona,
    run_panel,
)
from aec.agent.transports import CompletionResult


FIXTURE_SNAPSHOT: dict[str, Any] = {
    "snapshot_id": "test-001",
    "control_id": "CC6.1",
    "spl_executed": 'index=main action=login earliest=-90d | stats count by user | where count > 100',
    "row_count": 247,
    "timestamp": "2026-06-01T14:23:00Z",
}

FIXTURE_CONTROL_TEXT = (
    "CC6.1: Logical and physical access controls — the entity implements logical access "
    "security software, infrastructure, and architectures over protected information assets."
)

FIXTURE_SPL = FIXTURE_SNAPSHOT["spl_executed"]


def _critique_json(
    verdict: str = "PASS",
    confidence: float = 0.85,
    rationale: str = "Evidence supports compliance.",
    concerns: list[str] | None = None,
    searches: list[str] | None = None,
) -> str:
    return json.dumps({
        "verdict": verdict,
        "confidence": confidence,
        "rationale": rationale,
        "concerns": concerns or [],
        "recommended_additional_searches": searches or [],
    })


def _mock_router_for_verdicts(
    auditor_verdict: str = "PASS",
    engineer_verdict: str = "PASS",
    adversary_verdict: str = "PASS",
    adversary_searches: list[str] | None = None,
):
    """Return a mock for llm_router.complete that returns predefined verdicts."""
    responses = {
        "auditor": _critique_json(
            auditor_verdict, 0.9, f"Auditor says {auditor_verdict}."
        ),
        "engineer": _critique_json(
            engineer_verdict, 0.85, f"Engineer says {engineer_verdict}."
        ),
        "adversary": _critique_json(
            adversary_verdict,
            0.8,
            f"Adversary says {adversary_verdict}.",
            concerns=["potential blind spot"] if adversary_verdict != "PASS" else [],
            searches=adversary_searches,
        ),
    }

    async def fake_complete(persona: PersonaSpec, prompt: str):
        text = responses[persona.persona]
        return (
            CompletionResult(
                text=text,
                model=f"mock-{persona.persona}",
                transport_name=f"mock-{persona.persona}-transport",
            ),
            False,
        )

    return fake_complete


@pytest.fixture
def persona_dir(tmp_path: Path) -> Path:
    """Write minimal persona .md files to a temp dir."""
    files = {
        "auditor": (
            "---\n"
            "persona: auditor\n"
            "transports:\n"
            "  - anthropic-cli\n"
            "  - anthropic-api:\n"
            "      model: claude-sonnet-4-6\n"
            "temperature: 0.4\n"
            "---\n"
            "You are the auditor persona. Respond with JSON.\n"
        ),
        "engineer": (
            "---\n"
            "persona: engineer\n"
            "transports:\n"
            "  - openai-cli\n"
            "  - openai-api:\n"
            "      model: gpt-5\n"
            "temperature: 0.4\n"
            "---\n"
            "You are the engineer persona. Respond with JSON.\n"
        ),
        "adversary": (
            "---\n"
            "persona: adversary\n"
            "transports:\n"
            "  - gemini-cli\n"
            "  - gemini-api:\n"
            "      model: gemini-2.5-pro\n"
            "  - openrouter-api:\n"
            "      model: google/gemini-2.5-pro\n"
            "temperature: 0.4\n"
            "---\n"
            "You are the adversary persona. Respond with JSON.\n"
        ),
    }
    for name, content in files.items():
        (tmp_path / f"{name}.md").write_text(content)
    return tmp_path


class TestLoadPersona:
    def test_loads_auditor(self, persona_dir: Path):
        spec = load_persona("auditor", persona_dir)
        assert spec.persona == "auditor"
        assert len(spec.transports) == 2
        assert spec.transports[0].name == "anthropic-cli"
        assert spec.transports[1].name == "anthropic-api"
        assert spec.transports[1].config["model"] == "claude-sonnet-4-6"
        assert spec.temperature == 0.4
        assert "auditor" in spec.system_prompt.lower()

    def test_loads_adversary_with_three_transports(self, persona_dir: Path):
        spec = load_persona("adversary", persona_dir)
        assert spec.persona == "adversary"
        assert len(spec.transports) == 3
        assert spec.transports[0].name == "gemini-cli"
        assert spec.transports[2].name == "openrouter-api"

    def test_missing_persona_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_persona("nonexistent", tmp_path)


class TestParseCritiqueJson:
    def test_plain_json(self):
        raw = '{"verdict": "PASS", "confidence": 0.9, "rationale": "ok", "concerns": []}'
        parsed = _parse_critique_json(raw)
        assert parsed["verdict"] == "PASS"

    def test_fenced_json(self):
        raw = '```json\n{"verdict": "FAIL", "confidence": 0.7, "rationale": "bad", "concerns": ["x"]}\n```'
        parsed = _parse_critique_json(raw)
        assert parsed["verdict"] == "FAIL"
        assert parsed["concerns"] == ["x"]

    def test_fenced_no_language_tag(self):
        raw = '```\n{"verdict": "PARTIAL"}\n```'
        parsed = _parse_critique_json(raw)
        assert parsed["verdict"] == "PARTIAL"

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _parse_critique_json("not json at all")


class TestConsensus:
    def test_all_pass(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="engineer", model="b", transport="t", verdict="PASS", confidence=0.8, rationale="ok"),
            Critique(persona="adversary", model="c", transport="t", verdict="PASS", confidence=0.7, rationale="ok"),
        ]
        assert _compute_consensus(critiques) == "PASS"

    def test_mixed_verdicts_lowest_wins(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="engineer", model="b", transport="t", verdict="PARTIAL", confidence=0.8, rationale="partial"),
            Critique(persona="adversary", model="c", transport="t", verdict="FAIL", confidence=0.7, rationale="fail"),
        ]
        assert _compute_consensus(critiques) == "FAIL"

    def test_all_fail(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="FAIL", confidence=0.9, rationale="fail"),
            Critique(persona="engineer", model="b", transport="t", verdict="FAIL", confidence=0.8, rationale="fail"),
            Critique(persona="adversary", model="c", transport="t", verdict="FAIL", confidence=0.7, rationale="fail"),
        ]
        assert _compute_consensus(critiques) == "FAIL"

    def test_insufficient_is_worst(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="engineer", model="b", transport="t", verdict="INSUFFICIENT", confidence=0.3, rationale="no data"),
        ]
        assert _compute_consensus(critiques) == "INSUFFICIENT"

    def test_empty_critiques(self):
        assert _compute_consensus([]) == "INSUFFICIENT"

    def test_two_persona_degraded(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="adversary", model="c", transport="t", verdict="PARTIAL", confidence=0.7, rationale="gaps"),
        ]
        assert _compute_consensus(critiques) == "PARTIAL"


class TestRenderTranscript:
    def test_contains_all_personas(self):
        critiques = [
            Critique(persona="auditor", model="claude", transport="anthropic-cli", verdict="PASS", confidence=0.9, rationale="ok", latency_ms=1200),
            Critique(persona="engineer", model="gpt-5", transport="openai-api", verdict="PARTIAL", confidence=0.7, rationale="partial", latency_ms=800, concerns=["time bounds"]),
            Critique(persona="adversary", model="gemini", transport="gemini-cli", verdict="FAIL", confidence=0.6, rationale="gaps", latency_ms=1500, recommended_additional_searches=["index=main ..."]),
        ]
        md = _render_transcript(critiques, "FAIL")
        assert "AUDITOR" in md
        assert "ENGINEER" in md
        assert "ADVERSARY" in md
        assert "Consensus: **FAIL**" in md
        assert "time bounds" in md
        assert "index=main" in md


class TestVerdictSeverityOrder:
    def test_ordering(self):
        assert VERDICT_SEVERITY["PASS"] < VERDICT_SEVERITY["PARTIAL"]
        assert VERDICT_SEVERITY["PARTIAL"] < VERDICT_SEVERITY["FAIL"]
        assert VERDICT_SEVERITY["FAIL"] < VERDICT_SEVERITY["INSUFFICIENT"]


@pytest.mark.asyncio
class TestRunPanel:
    async def test_unanimous_pass(self, persona_dir: Path):
        mock_fn = _mock_router_for_verdicts("PASS", "PASS", "PASS")
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        assert result.final_verdict == "PASS"
        assert len(result.critiques) == 3
        assert result.consensus_method == "lowest_of_three"
        assert not result.degraded

    async def test_mixed_verdicts(self, persona_dir: Path):
        mock_fn = _mock_router_for_verdicts("PASS", "PARTIAL", "FAIL")
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        assert result.final_verdict == "FAIL"
        assert len(result.critiques) == 3

    async def test_all_fail(self, persona_dir: Path):
        mock_fn = _mock_router_for_verdicts("FAIL", "FAIL", "FAIL")
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        assert result.final_verdict == "FAIL"

    async def test_adversary_counter_search(self, persona_dir: Path):
        mock_fn = _mock_router_for_verdicts(
            "PASS",
            "PASS",
            "PARTIAL",
            adversary_searches=["index=main action=su earliest=-90d | stats count by user"],
        )
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        assert result.final_verdict == "PARTIAL"
        adversary = next(c for c in result.critiques if c.persona == "adversary")
        assert len(adversary.recommended_additional_searches) == 1
        assert "index=main" in adversary.recommended_additional_searches[0]

    async def test_degradation_one_persona_fails(self, persona_dir: Path):
        call_count = 0

        async def partial_fail(persona: PersonaSpec, prompt: str):
            nonlocal call_count
            call_count += 1
            if persona.persona == "engineer":
                raise RuntimeError("Transport chain exhausted")
            text = _critique_json("PASS", 0.9, f"{persona.persona} ok")
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=partial_fail):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        assert result.degraded
        assert len(result.critiques) == 2
        assert result.final_verdict == "PASS"
        personas_present = {c.persona for c in result.critiques}
        assert "engineer" not in personas_present

    async def test_single_vendor_fallback_when_two_personas_fail(
        self,
        persona_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        monkeypatch.setenv("AEC_PANEL_SINGLE_VENDOR_FALLBACK", "true")
        call_counts = {"auditor": 0, "engineer": 0, "adversary": 0}

        async def two_fail_then_claude(persona: PersonaSpec, prompt: str):
            call_counts[persona.persona] += 1

            if call_counts[persona.persona] == 1 and persona.persona != "auditor":
                raise RuntimeError("vendor unavailable")

            assert persona.transports[0].name == "anthropic-cli"
            text = _critique_json("PASS", 0.9, f"{persona.persona} via Claude")
            return (
                CompletionResult(
                    text=text,
                    model="claude-sonnet-4-6",
                    transport_name="anthropic-cli",
                ),
                False,
            )

        with patch("aec.agent.panel.llm_router.complete", side_effect=two_fail_then_claude):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )

        assert result.degraded
        assert result.mode == "single-vendor"
        assert result.final_verdict == "PASS"
        assert len(result.critiques) == 3
        assert {c.persona for c in result.critiques} == {"auditor", "engineer", "adversary"}
        assert {c.transport for c in result.critiques} == {"anthropic-cli"}
        assert all(c.fallback_used for c in result.critiques)
        assert call_counts == {"auditor": 2, "engineer": 2, "adversary": 2}

    async def test_transport_fallback_recorded(self, persona_dir: Path):
        async def fallback_scenario(persona: PersonaSpec, prompt: str):
            text = _critique_json("PASS", 0.9, f"{persona.persona} ok")
            fallback = persona.persona == "adversary"
            return (
                CompletionResult(
                    text=text,
                    model="gemini-2.5-pro" if fallback else "mock",
                    transport_name="gemini-api" if fallback else "mock",
                ),
                fallback,
            )

        with patch("aec.agent.panel.llm_router.complete", side_effect=fallback_scenario):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        adversary = next(c for c in result.critiques if c.persona == "adversary")
        assert adversary.fallback_used
        assert adversary.transport == "gemini-api"

    async def test_all_personas_fail_raises(self, persona_dir: Path):
        async def all_fail(persona: PersonaSpec, prompt: str):
            raise RuntimeError("everything is broken")

        with patch("aec.agent.panel.llm_router.complete", side_effect=all_fail):
            with pytest.raises(RuntimeError, match="no critiques|All personas"):
                await run_panel(
                    snapshot=FIXTURE_SNAPSHOT,
                    control_text=FIXTURE_CONTROL_TEXT,
                    spl_executed=FIXTURE_SPL,
                    persona_dir=persona_dir,
                )

    async def test_transcript_in_result(self, persona_dir: Path):
        mock_fn = _mock_router_for_verdicts("PASS", "PARTIAL", "FAIL")
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )
        assert "Panel Debate Transcript" in result.transcript
        assert "AUDITOR" in result.transcript
        assert "Consensus: **FAIL**" in result.transcript
