"""Tests for snapshot_adapter — PanelResult → EvidenceSnapshot + GapFinding."""
from __future__ import annotations

from aec.agent.models import Critique, PanelResult
from aec.agent.snapshot_adapter import extract_gap_findings, panel_result_to_snapshots


def _make_critique(persona: str, verdict: str, **kw) -> Critique:
    return Critique(
        persona=persona,
        model="test-model",
        transport="test-transport",
        verdict=verdict,
        confidence=0.85,
        rationale=kw.get("rationale", f"{persona} rationale"),
        concerns=kw.get("concerns", []),
        recommended_additional_searches=kw.get("recommended_additional_searches", []),
    )


def _make_panel_result(verdict: str, **kw) -> PanelResult:
    critiques = kw.get("critiques", [
        _make_critique("auditor", verdict),
        _make_critique("engineer", verdict),
        _make_critique("adversary", verdict, **{
            k: v for k, v in kw.items()
            if k in ("concerns", "recommended_additional_searches", "rationale")
        }),
    ])
    return PanelResult(
        critiques=critiques,
        final_verdict=verdict,
        consensus_method="lowest_of_three",
        transcript="full transcript text",
    )


SAMPLE_SNAPSHOT = {
    "control_id": "CC6.1",
    "framework": "SOC2",
    "snapshot_name": "soc2-cc61",
    "search": "index=auth EventCode=4625",
    "event_count": 1247,
    "time_range": {"earliest": "-30d", "latest": "now"},
}


class TestPanelResultToSnapshots:
    def test_produces_one_per_critique_plus_consensus(self):
        result = _make_panel_result("PASS")
        snaps = panel_result_to_snapshots(result, SAMPLE_SNAPSHOT, "CC6.1")
        assert len(snaps) == 4  # 3 critiques + 1 consensus

    def test_consensus_snapshot_is_last(self):
        result = _make_panel_result("FAIL")
        snaps = panel_result_to_snapshots(result, SAMPLE_SNAPSHOT, "CC6.1")
        assert snaps[-1]["persona"] == "consensus"
        assert snaps[-1]["panel_verdict"] == "FAIL"

    def test_critique_snapshots_carry_persona_fields(self):
        result = _make_panel_result("PARTIAL")
        snaps = panel_result_to_snapshots(result, SAMPLE_SNAPSHOT, "CC6.1")
        auditor = snaps[0]
        assert auditor["persona"] == "auditor"
        assert auditor["model"] == "test-model"
        assert auditor["transport"] == "test-transport"
        assert auditor["confidence"] == 0.85
        assert auditor["control_id"] == "CC6.1"
        assert auditor["spl_executed"] == "index=auth EventCode=4625"
        assert auditor["row_count"] == 1247

    def test_snapshot_ids_are_unique(self):
        result = _make_panel_result("PASS")
        snaps = panel_result_to_snapshots(result, SAMPLE_SNAPSHOT, "CC6.1")
        ids = [s["snapshot_id"] for s in snaps]
        assert len(ids) == len(set(ids))

    def test_custom_timestamp(self):
        result = _make_panel_result("PASS")
        snaps = panel_result_to_snapshots(
            result, SAMPLE_SNAPSHOT, "CC6.1", timestamp="2026-01-01T00:00:00Z",
        )
        assert all(s["timestamp"] == "2026-01-01T00:00:00Z" for s in snaps)

    def test_transcript_hash_is_deterministic(self):
        result = _make_panel_result("PASS")
        s1 = panel_result_to_snapshots(result, SAMPLE_SNAPSHOT, "CC6.1", timestamp="T")
        s2 = panel_result_to_snapshots(result, SAMPLE_SNAPSHOT, "CC6.1", timestamp="T")
        assert s1[-1]["panel_transcript_hash"] == s2[-1]["panel_transcript_hash"]


class TestExtractGapFindings:
    def test_pass_returns_empty(self):
        result = _make_panel_result("PASS")
        findings = extract_gap_findings(result, SAMPLE_SNAPSHOT, "CC6.1", "trail.jsonl")
        assert findings == []

    def test_fail_returns_high_severity(self):
        result = _make_panel_result("FAIL")
        findings = extract_gap_findings(result, SAMPLE_SNAPSHOT, "CC6.1", "trail.jsonl")
        assert len(findings) == 1
        assert findings[0].severity == "High"
        assert findings[0].finding_id == "AEC-CC6.1-001"
        assert findings[0].framework == "SOC2"

    def test_partial_returns_medium_severity(self):
        result = _make_panel_result("PARTIAL")
        findings = extract_gap_findings(result, SAMPLE_SNAPSHOT, "CC6.1", "trail.jsonl")
        assert len(findings) == 1
        assert findings[0].severity == "Medium"

    def test_insufficient_returns_high_severity(self):
        result = _make_panel_result("INSUFFICIENT")
        findings = extract_gap_findings(result, SAMPLE_SNAPSHOT, "CC6.1", "trail.jsonl")
        assert findings[0].severity == "High"

    def test_adversary_rationale_used_as_root_cause(self):
        result = _make_panel_result("FAIL", rationale="MFA bypass for service accounts")
        findings = extract_gap_findings(result, SAMPLE_SNAPSHOT, "CC6.1", "trail.jsonl")
        assert "MFA bypass" in findings[0].root_cause

    def test_adversary_search_used_as_remediation(self):
        result = _make_panel_result(
            "FAIL",
            recommended_additional_searches=["index=auth mfa_status=bypassed | stats count"],
        )
        findings = extract_gap_findings(result, SAMPLE_SNAPSHOT, "CC6.1", "trail.jsonl")
        assert "index=auth" in findings[0].remediation_action

    def test_evidence_reference_points_to_trail(self):
        result = _make_panel_result("FAIL")
        findings = extract_gap_findings(
            result, SAMPLE_SNAPSHOT, "CC6.1", "out/audit_trail.jsonl",
        )
        assert findings[0].evidence_reference == "out/audit_trail.jsonl"
