"""Tests for Foundation-Sec-8B transport and 4-persona panel debate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aec.agent.models import Critique, PersonaSpec
from aec.agent.panel import _compute_consensus, load_persona, run_panel
from aec.agent.transports import CompletionResult
from aec.agent.transports.foundation_sec_api import FoundationSecAPITransport
from aec.agent.transports.foundation_sec_local import FoundationSecLocalTransport


FIXTURE_SNAPSHOT: dict[str, Any] = {
    "snapshot_id": "test-fsec-001",
    "control_id": "CC6.1",
    "spl_executed": "index=main action=login | stats count by user",
    "row_count": 100,
    "timestamp": "2026-06-01T14:00:00Z",
}

FIXTURE_CONTROL_TEXT = "CC6.1: Logical and physical access controls."
FIXTURE_SPL = FIXTURE_SNAPSHOT["spl_executed"]


def _critique_json(
    verdict: str = "PASS",
    confidence: float = 0.85,
    rationale: str = "Evidence supports compliance.",
    concerns: list[str] | None = None,
) -> str:
    return json.dumps({
        "verdict": verdict,
        "confidence": confidence,
        "rationale": rationale,
        "concerns": concerns or [],
        "recommended_additional_searches": [],
    })


@pytest.fixture
def persona_dir_with_security_model(tmp_path: Path) -> Path:
    personas = {
        "auditor": (
            "---\npersona: auditor\ntransports:\n  - anthropic-cli\ntemperature: 0.4\n---\n"
            "You are the auditor persona. Respond with JSON.\n"
        ),
        "engineer": (
            "---\npersona: engineer\ntransports:\n  - openai-cli\ntemperature: 0.4\n---\n"
            "You are the engineer persona. Respond with JSON.\n"
        ),
        "adversary": (
            "---\npersona: adversary\ntransports:\n  - gemini-cli\ntemperature: 0.4\n---\n"
            "You are the adversary persona. Respond with JSON.\n"
        ),
        "security_model": (
            "---\npersona: security_model\ntransports:\n  - foundation-sec-api\n"
            "  - foundation-sec-local\ntemperature: 0.3\n---\n"
            "You are the security model persona. Respond with JSON.\n"
        ),
    }
    for name, content in personas.items():
        (tmp_path / f"{name}.md").write_text(content)
    return tmp_path


class TestFoundationSecAPITransport:
    def test_name(self):
        t = FoundationSecAPITransport()
        assert t.name == "foundation-sec-api"

    @pytest.mark.asyncio
    async def test_available_without_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        t = FoundationSecAPITransport()
        assert not await t.available()

    @pytest.mark.asyncio
    async def test_available_with_token(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HF_TOKEN", "hf_test_token")
        t = FoundationSecAPITransport()
        assert await t.available()


class TestFoundationSecLocalTransport:
    def test_name(self):
        t = FoundationSecLocalTransport()
        assert t.name == "foundation-sec-local"

    @pytest.mark.asyncio
    async def test_unavailable_when_no_server(self):
        t = FoundationSecLocalTransport()
        assert not await t.available()


class TestLoadSecurityModelPersona:
    def test_loads_security_model(self, persona_dir_with_security_model: Path):
        spec = load_persona("security_model", persona_dir_with_security_model)
        assert spec.persona == "security_model"
        assert len(spec.transports) == 2
        assert spec.transports[0].name == "foundation-sec-api"
        assert spec.transports[1].name == "foundation-sec-local"
        assert spec.temperature == 0.3
        assert "security model" in spec.system_prompt.lower()


class TestFourPersonaConsensus:
    def test_four_way_unanimous_pass(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="engineer", model="b", transport="t", verdict="PASS", confidence=0.8, rationale="ok"),
            Critique(persona="adversary", model="c", transport="t", verdict="PASS", confidence=0.7, rationale="ok"),
            Critique(persona="security_model", model="d", transport="t", verdict="PASS", confidence=0.8, rationale="ok"),
        ]
        assert _compute_consensus(critiques) == "PASS"

    def test_security_model_dissent_wins(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="engineer", model="b", transport="t", verdict="PASS", confidence=0.8, rationale="ok"),
            Critique(persona="adversary", model="c", transport="t", verdict="PASS", confidence=0.7, rationale="ok"),
            Critique(persona="security_model", model="d", transport="t", verdict="FAIL", confidence=0.8, rationale="no threat detection"),
        ]
        assert _compute_consensus(critiques) == "FAIL"

    def test_four_way_mixed(self):
        critiques = [
            Critique(persona="auditor", model="a", transport="t", verdict="PASS", confidence=0.9, rationale="ok"),
            Critique(persona="engineer", model="b", transport="t", verdict="PARTIAL", confidence=0.8, rationale="partial"),
            Critique(persona="adversary", model="c", transport="t", verdict="FAIL", confidence=0.7, rationale="fail"),
            Critique(persona="security_model", model="d", transport="t", verdict="PARTIAL", confidence=0.8, rationale="partial"),
        ]
        assert _compute_consensus(critiques) == "FAIL"


@pytest.mark.asyncio
class TestFourPersonaPanel:
    async def test_four_persona_panel(self, persona_dir_with_security_model: Path):
        responses = {
            "auditor": _critique_json("PASS", 0.9, "Auditor says PASS."),
            "engineer": _critique_json("PASS", 0.85, "Engineer says PASS."),
            "adversary": _critique_json("PARTIAL", 0.8, "Adversary says PARTIAL."),
            "security_model": _critique_json("FAIL", 0.75, "Security Model says FAIL."),
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

        with patch("aec.agent.panel.llm_router.complete", side_effect=fake_complete):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir_with_security_model,
            )

        assert len(result.critiques) == 4
        assert result.final_verdict == "FAIL"
        personas_present = {c.persona for c in result.critiques}
        assert personas_present == {"auditor", "engineer", "adversary", "security_model"}
        assert "SECURITY_MODEL" in result.transcript

    async def test_graceful_degradation_no_hf_token(
        self, persona_dir_with_security_model: Path
    ):
        """When security_model transport fails, panel degrades to 3 personas."""
        responses = {
            "auditor": _critique_json("PASS", 0.9, "Auditor says PASS."),
            "engineer": _critique_json("PASS", 0.85, "Engineer says PASS."),
            "adversary": _critique_json("PASS", 0.8, "Adversary says PASS."),
        }

        async def fail_on_security_model(persona: PersonaSpec, prompt: str):
            if persona.persona == "security_model":
                raise RuntimeError("All transports exhausted for persona 'security_model'")
            text = responses[persona.persona]
            return (
                CompletionResult(
                    text=text,
                    model=f"mock-{persona.persona}",
                    transport_name=f"mock-{persona.persona}-transport",
                ),
                False,
            )

        with patch("aec.agent.panel.llm_router.complete", side_effect=fail_on_security_model):
            result = await run_panel(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir_with_security_model,
            )

        assert len(result.critiques) == 3
        assert result.degraded
        assert result.final_verdict == "PASS"
        personas_present = {c.persona for c in result.critiques}
        assert "security_model" not in personas_present
