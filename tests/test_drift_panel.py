"""Tests for panel debate with drift context — drift-aware verdicts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aec.agent.models import DriftAnalysis, PersonaSpec
from aec.agent.panel import _build_user_prompt, run_panel
from aec.agent.transports import CompletionResult
from aec.splunk.drift import compute_drift


SNAPSHOT_Q1: dict[str, Any] = {
    "control_id": "CC6.1",
    "framework": "SOC2",
    "time_range": {"earliest": "2018-08-01", "latest": "2018-08-31"},
    "event_count": 1247,
    "aggregations": {
        "successful_logins": 1198,
        "failed_logins": 49,
        "mfa_enforced_pct": 0.83,
        "unique_users": 142,
        "service_accounts_bypassing_mfa": 12,
    },
}

SNAPSHOT_Q2: dict[str, Any] = {
    "control_id": "CC6.1",
    "framework": "SOC2",
    "time_range": {"earliest": "2018-09-01", "latest": "2018-09-30"},
    "event_count": 1389,
    "aggregations": {
        "successful_logins": 1261,
        "failed_logins": 128,
        "mfa_enforced_pct": 0.71,
        "unique_users": 157,
        "service_accounts_bypassing_mfa": 19,
    },
}

CONTROL_TEXT = "CC6.1: Logical and physical access controls"
SPL = 'index=botsv3 sourcetype=o365:management:activity action=Login | stats count by user'


def _critique_json(
    verdict: str = "FAIL",
    confidence: float = 0.85,
    rationale: str = "Drift detected.",
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
def persona_dir(tmp_path: Path) -> Path:
    for name in ("auditor", "engineer", "adversary"):
        transport = {
            "auditor": "anthropic-cli",
            "engineer": "openai-cli",
            "adversary": "gemini-cli",
        }[name]
        content = (
            f"---\npersona: {name}\ntransports:\n  - {transport}\ntemperature: 0.4\n---\n"
            f"You are the {name} persona. Respond with JSON.\n"
        )
        (tmp_path / f"{name}.md").write_text(content)
    return tmp_path


@pytest.fixture
def drift_analysis() -> DriftAnalysis:
    return compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)


class TestBuildUserPromptWithDrift:
    def test_drift_section_included(self, drift_analysis: DriftAnalysis):
        prompt = _build_user_prompt(
            snapshot=SNAPSHOT_Q2,
            control_text=CONTROL_TEXT,
            spl_executed=SPL,
            drift=drift_analysis,
        )
        assert "## Drift analysis" in prompt
        assert "mfa_enforced_pct" in prompt
        assert "Window 1:" in prompt
        assert "Window 2:" in prompt

    def test_no_drift_no_section(self):
        prompt = _build_user_prompt(
            snapshot=SNAPSHOT_Q2,
            control_text=CONTROL_TEXT,
            spl_executed=SPL,
            drift=None,
        )
        assert "Drift analysis" not in prompt


@pytest.mark.asyncio
class TestRunPanelWithDrift:
    async def test_drift_injected_into_persona_prompts(
        self, persona_dir: Path, drift_analysis: DriftAnalysis,
    ):
        captured_prompts: list[str] = []

        async def capture_complete(persona: PersonaSpec, prompt: str):
            captured_prompts.append(persona.system_prompt)
            text = _critique_json("FAIL", 0.9, "MFA coverage declining.")
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=capture_complete):
            await run_panel(
                snapshot=SNAPSHOT_Q2,
                control_text=CONTROL_TEXT,
                spl_executed=SPL,
                persona_dir=persona_dir,
                drift=drift_analysis,
            )

        assert len(captured_prompts) == 3
        for sys_prompt in captured_prompts:
            assert "compliance TREND" in sys_prompt
            assert "worsening" in sys_prompt
            assert "mfa_enforced_pct" in sys_prompt

    async def test_drift_aware_verdict(
        self, persona_dir: Path, drift_analysis: DriftAnalysis,
    ):
        async def strict_verdicts(persona: PersonaSpec, prompt: str):
            text = _critique_json(
                "FAIL", 0.9,
                "MFA enforcement dropped 12% — control degradation.",
                concerns=["MFA coverage declining from 83% to 71%"],
            )
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=strict_verdicts):
            result = await run_panel(
                snapshot=SNAPSHOT_Q2,
                control_text=CONTROL_TEXT,
                spl_executed=SPL,
                persona_dir=persona_dir,
                drift=drift_analysis,
            )

        assert result.final_verdict == "FAIL"
        for c in result.critiques:
            assert c.verdict == "FAIL"

    async def test_no_drift_no_appendix(self, persona_dir: Path):
        captured_prompts: list[str] = []

        async def capture_complete(persona: PersonaSpec, prompt: str):
            captured_prompts.append(persona.system_prompt)
            text = _critique_json("PASS", 0.9, "Looks good.")
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=capture_complete):
            await run_panel(
                snapshot=SNAPSHOT_Q2,
                control_text=CONTROL_TEXT,
                spl_executed=SPL,
                persona_dir=persona_dir,
                drift=None,
            )

        for sys_prompt in captured_prompts:
            assert "compliance TREND" not in sys_prompt

    async def test_drift_in_user_prompt(
        self, persona_dir: Path, drift_analysis: DriftAnalysis,
    ):
        captured_user_prompts: list[str] = []

        async def capture_complete(persona: PersonaSpec, prompt: str):
            captured_user_prompts.append(prompt)
            text = _critique_json("FAIL", 0.9, "Worsening trend.")
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=capture_complete):
            await run_panel(
                snapshot=SNAPSHOT_Q2,
                control_text=CONTROL_TEXT,
                spl_executed=SPL,
                persona_dir=persona_dir,
                drift=drift_analysis,
            )

        for user_prompt in captured_user_prompts:
            assert "Drift analysis" in user_prompt
            assert "WORSENING" in user_prompt

    async def test_transcript_contains_drift_block(
        self, persona_dir: Path, drift_analysis: DriftAnalysis,
    ):
        async def mock_complete(persona: PersonaSpec, prompt: str):
            text = _critique_json("FAIL", 0.9, "Declining.")
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_complete):
            result = await run_panel(
                snapshot=SNAPSHOT_Q2,
                control_text=CONTROL_TEXT,
                spl_executed=SPL,
                persona_dir=persona_dir,
                drift=drift_analysis,
            )

        assert "Panel Debate Transcript" in result.transcript
        assert result.final_verdict == "FAIL"
