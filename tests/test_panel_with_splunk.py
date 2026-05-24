"""End-to-end tests — panel debate with Splunk snapshot integration."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from aec.agent.models import PersonaSpec
from aec.agent.panel import run_panel
from aec.agent.transports import CompletionResult


SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"


def _load_sample(name: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


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


def _mock_router_for_scenario(
    auditor_verdict: str = "FAIL",
    engineer_verdict: str = "PARTIAL",
    adversary_verdict: str = "FAIL",
    adversary_concerns: list[str] | None = None,
    adversary_searches: list[str] | None = None,
):
    responses = {
        "auditor": _critique_json(auditor_verdict, 0.9, f"Auditor says {auditor_verdict}."),
        "engineer": _critique_json(engineer_verdict, 0.85, f"Engineer says {engineer_verdict}."),
        "adversary": _critique_json(
            adversary_verdict,
            0.8,
            f"Adversary says {adversary_verdict}.",
            concerns=adversary_concerns or ["17% of logins bypass MFA"],
            searches=adversary_searches or ["index=auth mfa_status=bypassed | stats count by user"],
        ),
    }

    async def fake_complete(persona: PersonaSpec, prompt: str):
        return (
            CompletionResult(
                text=responses[persona.persona],
                model=f"mock-{persona.persona}",
                transport_name="mock-transport",
            ),
            False,
        )

    return fake_complete


@pytest.fixture
def persona_dir(tmp_path: Path) -> Path:
    for name in ("auditor", "engineer", "adversary"):
        (tmp_path / f"{name}.md").write_text(
            f"---\npersona: {name}\ntransports:\n  - anthropic-cli\ntemperature: 0.4\n---\n"
            f"You are the {name}. Respond with JSON.\n"
        )
    return tmp_path


@pytest.mark.asyncio
class TestPanelWithSplunkSnapshot:
    async def test_snapshot_content_in_prompt(self, persona_dir: Path):
        snapshot = _load_sample("soc2-cc61")
        prompts_seen: list[str] = []

        async def capture_prompt(persona: PersonaSpec, prompt: str):
            prompts_seen.append(prompt)
            text = _critique_json("FAIL", 0.8, "MFA gap found", ["17% bypass MFA"])
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=capture_prompt):
            await run_panel(
                snapshot=snapshot,
                control_text="CC6.1: Logical access controls",
                spl_executed=snapshot["search"],
                persona_dir=persona_dir,
                splunk_snapshot=snapshot,
            )

        assert len(prompts_seen) == 3
        for prompt in prompts_seen:
            assert "Splunk snapshot" in prompt
            assert str(snapshot["event_count"]) in prompt
            assert "mfa_enforced_pct" in prompt

    async def test_transcript_contains_snapshot_data(self, persona_dir: Path):
        snapshot = _load_sample("soc2-cc61")
        mock_fn = _mock_router_for_scenario()

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=snapshot,
                control_text="CC6.1: Logical access controls",
                spl_executed=snapshot["search"],
                persona_dir=persona_dir,
                splunk_snapshot=snapshot,
            )

        assert result.final_verdict == "FAIL"
        assert str(snapshot["event_count"]) in result.transcript
        assert "src_ip" in result.transcript
        assert any("MFA" in c.rationale or "MFA" in " ".join(c.concerns) for c in result.critiques)

    async def test_without_splunk_snapshot_still_works(self, persona_dir: Path):
        snapshot = _load_sample("soc2-cc61")

        async def simple_mock(persona: PersonaSpec, prompt: str):
            assert "Splunk snapshot" not in prompt
            text = _critique_json("PASS", 0.9, "All good")
            return CompletionResult(text=text, model="mock", transport_name="mock"), False

        with patch("aec.agent.panel.llm_router.complete", side_effect=simple_mock):
            result = await run_panel(
                snapshot=snapshot,
                control_text="CC6.1: Logical access controls",
                spl_executed=snapshot["search"],
                persona_dir=persona_dir,
            )

        assert result.final_verdict == "PASS"


@pytest.mark.asyncio
class TestAdversaryFollowUp:
    async def test_followup_section_when_enabled(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        snapshot = _load_sample("soc2-cc61")
        mock_fn = _mock_router_for_scenario(
            adversary_searches=["index=auth mfa_status=bypassed | stats count by user"],
        )

        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [{"user": "svc_deploy", "count": "12"}],
            "event_count": 12,
            "search_id": "sid-followup",
        }

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=snapshot,
                control_text="CC6.1",
                spl_executed=snapshot["search"],
                persona_dir=persona_dir,
                splunk_snapshot=snapshot,
                splunk_client=mock_client,
            )

        adversary = next(c for c in result.critiques if c.persona == "adversary")
        assert len(adversary.recommended_additional_searches) > 0
        assert result.adversary_followups[0]["hit_count"] == 12
        assert "Adversary follow-up searches" in result.transcript
        assert "svc_deploy" in result.transcript

    async def test_no_followup_when_disabled(self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "false")
        snapshot = _load_sample("soc2-cc61")
        mock_fn = _mock_router_for_scenario(
            adversary_searches=["index=auth mfa_status=bypassed | stats count by user"],
        )

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=snapshot,
                control_text="CC6.1",
                spl_executed=snapshot["search"],
                persona_dir=persona_dir,
                splunk_snapshot=snapshot,
            )

        assert result.final_verdict == "FAIL"
        assert result.adversary_followups == []
        assert "Adversary follow-up searches" not in result.transcript

    async def test_no_followup_by_default(self, persona_dir: Path):
        snapshot = _load_sample("soc2-cc61")
        mock_fn = _mock_router_for_scenario(
            adversary_searches=["index=auth mfa_status=bypassed"],
        )

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel(
                snapshot=snapshot,
                control_text="CC6.1",
                spl_executed=snapshot["search"],
                persona_dir=persona_dir,
                splunk_snapshot=snapshot,
            )

        assert result.final_verdict == "FAIL"
        assert result.adversary_followups == []
