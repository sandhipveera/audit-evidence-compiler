"""Tests for counter-evidence recurrence loop (task 014)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from aec.agent.models import PersonaSpec
from aec.agent.panel import run_panel_with_recurrence
from aec.agent.snapshot_adapter import recurrence_result_to_snapshots
from aec.agent.transports import CompletionResult


FIXTURE_SNAPSHOT: dict[str, Any] = {
    "snapshot_id": "test-recurrence",
    "control_id": "CC6.1",
    "spl_executed": "index=main action=login earliest=-90d | stats count by user",
    "row_count": 247,
    "timestamp": "2026-06-01T14:23:00Z",
}

FIXTURE_CONTROL_TEXT = "CC6.1: Logical and physical access controls."
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


def _make_mock_complete(
    auditor_verdict: str = "PASS",
    engineer_verdict: str = "PASS",
    adversary_verdict: str = "PASS",
    adversary_searches: list[str] | None = None,
    round_2_auditor: str | None = None,
    round_2_engineer: str | None = None,
    round_2_adversary: str | None = None,
):
    """Mock llm_router.complete that changes verdicts between rounds."""
    call_count = {"total": 0}

    async def fake_complete(persona: PersonaSpec, prompt: str):
        call_count["total"] += 1
        is_round_2 = "counter_searches" in prompt

        if persona.persona == "auditor":
            v = (round_2_auditor if is_round_2 and round_2_auditor else auditor_verdict)
            text = _critique_json(v, 0.9, f"Auditor says {v}.")
        elif persona.persona == "engineer":
            v = (round_2_engineer if is_round_2 and round_2_engineer else engineer_verdict)
            text = _critique_json(v, 0.85, f"Engineer says {v}.")
        else:
            v = (round_2_adversary if is_round_2 and round_2_adversary else adversary_verdict)
            text = _critique_json(
                v,
                0.8,
                f"Adversary says {v}.",
                concerns=["potential blind spot"] if v != "PASS" else [],
                searches=adversary_searches if not is_round_2 else [],
            )

        return (
            CompletionResult(
                text=text,
                model=f"mock-{persona.persona}",
                transport_name=f"mock-{persona.persona}-transport",
            ),
            False,
        )

    return fake_complete, call_count


@pytest.fixture
def persona_dir(tmp_path: Path) -> Path:
    files = {
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
    }
    for name, content in files.items():
        (tmp_path / f"{name}.md").write_text(content)
    return tmp_path


class FakeSplunkClient:
    """Minimal fake Splunk client for counter-search execution."""

    def __init__(self, results: list[dict[str, Any]] | None = None):
        self._results = results or [{"user": "svc_admin", "count": 47}]

    def search(self, query: str, earliest: str, latest: str, max_results: int = 10):
        return {"event_count": len(self._results), "results": self._results}


@pytest.mark.asyncio
class TestRecurrenceTwoRounds:
    async def test_two_round_path_with_counter_searches(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When Adversary recommends searches and recurrence is enabled, runs 2 rounds."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, call_count = _make_mock_complete(
            auditor_verdict="PASS",
            engineer_verdict="PARTIAL",
            adversary_verdict="FAIL",
            adversary_searches=["index=main action=su | stats count by user"],
            round_2_auditor="INSUFFICIENT",
            round_2_engineer="PARTIAL",
            round_2_adversary="FAIL",
        )

        client = FakeSplunkClient()
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=client,
                enable_recurrence=True,
            )

        assert result.iteration_count == 2
        assert result.final_consensus_round == 2
        assert result.round_1 is not None
        assert result.round_2 is not None
        assert result.round_1.final_verdict == "FAIL"
        assert result.final_verdict == "INSUFFICIENT"
        assert len(result.counter_searches) == 1
        assert result.counter_searches[0].validation_status == "accepted"
        assert result.counter_searches[0].executed
        assert call_count["total"] == 6  # 3 per round

    async def test_round_2_verdict_supersedes_round_1(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Round 2 verdict is the final verdict even if it differs from round 1."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, _ = _make_mock_complete(
            auditor_verdict="PASS",
            engineer_verdict="PASS",
            adversary_verdict="PARTIAL",
            adversary_searches=["index=main sourcetype=auth | stats count"],
            round_2_auditor="FAIL",
            round_2_engineer="FAIL",
            round_2_adversary="FAIL",
        )

        client = FakeSplunkClient()
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=client,
            )

        assert result.final_verdict == "FAIL"
        assert result.final_consensus_round == 2


@pytest.mark.asyncio
class TestRecurrenceSingleRound:
    async def test_no_searches_skips_round_2(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """When Adversary recommends nothing, no round 2 occurs."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, call_count = _make_mock_complete(
            auditor_verdict="PASS",
            engineer_verdict="PASS",
            adversary_verdict="PASS",
            adversary_searches=[],
        )

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=FakeSplunkClient(),
            )

        assert result.iteration_count == 1
        assert result.round_2 is None
        assert result.final_consensus_round == 1
        assert result.final_verdict == "PASS"
        assert call_count["total"] == 3

    async def test_no_recurrence_flag(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """--no-recurrence short-circuits to single round."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, call_count = _make_mock_complete(
            adversary_verdict="FAIL",
            adversary_searches=["index=main | delete"],
        )

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=FakeSplunkClient(),
                enable_recurrence=False,
            )

        assert result.iteration_count == 1
        assert result.round_2 is None
        assert result.counter_searches == []
        assert call_count["total"] == 3

    async def test_env_var_disables_recurrence(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """AEC_RUN_ADVERSARY_SEARCHES=false disables the loop."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "false")
        mock_fn, call_count = _make_mock_complete(
            adversary_verdict="FAIL",
            adversary_searches=["index=main action=login"],
        )

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=FakeSplunkClient(),
                enable_recurrence=True,
            )

        assert result.iteration_count == 1
        assert result.round_2 is None


@pytest.mark.asyncio
class TestValidatorRejection:
    async def test_rejected_search_records_reason(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Validator rejection mid-loop records the reason and continues."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, _ = _make_mock_complete(
            adversary_verdict="FAIL",
            adversary_searches=[
                "index=main action=su | stats count",
                "index=main | delete",
                "index=main sourcetype=auth earliest=-30d",
            ],
        )

        client = FakeSplunkClient()
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=client,
            )

        assert result.iteration_count == 2
        assert len(result.counter_searches) == 3

        accepted = [s for s in result.counter_searches if s.validation_status == "accepted"]
        rejected = [s for s in result.counter_searches if s.validation_status == "rejected"]
        assert len(accepted) == 2
        assert len(rejected) == 1
        assert rejected[0].spl == "index=main | delete"
        assert "Forbidden command" in rejected[0].rejection_reason
        assert not rejected[0].executed


@pytest.mark.asyncio
class TestCounterSearchCap:
    async def test_cap_respected(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Only max_counter_searches queries are processed."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, _ = _make_mock_complete(
            adversary_verdict="FAIL",
            adversary_searches=[
                "index=main q1",
                "index=main q2",
                "index=main q3",
                "index=main q4",
                "index=main q5",
            ],
        )

        client = FakeSplunkClient()
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=client,
                max_counter_searches=2,
            )

        assert len(result.counter_searches) == 2


@pytest.mark.asyncio
class TestSnapshotChain:
    async def test_9_snapshot_chain_for_2_rounds(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A 2-round result produces 9 chained EvidenceSnapshots."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, _ = _make_mock_complete(
            auditor_verdict="PASS",
            engineer_verdict="PARTIAL",
            adversary_verdict="FAIL",
            adversary_searches=["index=main action=su | stats count"],
            round_2_auditor="INSUFFICIENT",
            round_2_engineer="PARTIAL",
            round_2_adversary="FAIL",
        )

        client = FakeSplunkClient()
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=client,
            )

        splunk_snap = {
            "control_id": "CC6.1",
            "search": FIXTURE_SPL,
            "event_count": 247,
            "mcp_server": "splunk-official-v1",
        }
        snapshots = recurrence_result_to_snapshots(result, splunk_snap, "CC6.1")

        # 3 personas r1 + 1 consensus r1 + 3 personas r2 + 1 consensus r2 + 1 final = 9
        assert len(snapshots) == 9

        r1_snaps = [s for s in snapshots if s.get("iteration") == 1]
        r2_snaps = [s for s in snapshots if s.get("iteration") == 2]
        assert len(r1_snaps) == 4  # 3 personas + consensus
        assert len(r2_snaps) == 5  # 3 personas + consensus + final (iteration=2)

        final = snapshots[-1]
        assert final["persona"] == "final"
        assert final["panel_verdict"] == "INSUFFICIENT"
        assert final["final_consensus_round"] == 2

    async def test_5_snapshots_for_single_round(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A single-round result produces 5 snapshots (3 personas + consensus + final)."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, _ = _make_mock_complete(
            auditor_verdict="PASS",
            engineer_verdict="PASS",
            adversary_verdict="PASS",
            adversary_searches=[],
        )

        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
            )

        splunk_snap = {
            "control_id": "CC6.1",
            "search": FIXTURE_SPL,
            "event_count": 247,
            "mcp_server": None,
        }
        snapshots = recurrence_result_to_snapshots(result, splunk_snap, "CC6.1")
        assert len(snapshots) == 5  # 3 + consensus + final


@pytest.mark.asyncio
class TestTranscriptFormat:
    async def test_transcript_shows_both_rounds(
        self, persona_dir: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Transcript includes round 1, counter-evidence section, round 2, and delta."""
        monkeypatch.setenv("AEC_RUN_ADVERSARY_SEARCHES", "true")
        mock_fn, _ = _make_mock_complete(
            auditor_verdict="PASS",
            engineer_verdict="PARTIAL",
            adversary_verdict="FAIL",
            adversary_searches=["index=main action=su | stats count by user"],
            round_2_auditor="INSUFFICIENT",
        )

        client = FakeSplunkClient()
        with patch("aec.agent.panel.llm_router.complete", side_effect=mock_fn):
            result = await run_panel_with_recurrence(
                snapshot=FIXTURE_SNAPSHOT,
                control_text=FIXTURE_CONTROL_TEXT,
                spl_executed=FIXTURE_SPL,
                persona_dir=persona_dir,
                splunk_client=client,
            )

        assert "## Round 1" in result.transcript
        assert "## Counter-evidence loop" in result.transcript
        assert "## Round 2 panel debate" in result.transcript
        assert "### What changed" in result.transcript
        assert "Auditor:" in result.transcript
        assert "round 2 supersedes" in result.transcript
