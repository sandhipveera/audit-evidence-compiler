"""Tests for the post-submission enhancements:
- dissent ledger + panel-agreement confidence (models)
- round-over-round verdict changes (counter-search self-correction)
- MITRE ATT&CK crosswalk threaded catalog → ControlMatch → web resolver
"""
from __future__ import annotations

from aec.agent.models import (
    Critique,
    PanelResult,
    build_dissent_ledger,
    build_verdict_changes,
    confidence_label,
)


def _critique(persona: str, verdict: str, confidence: float = 0.8) -> Critique:
    return Critique(
        persona=persona,
        model="m",
        transport="t",
        verdict=verdict,
        confidence=confidence,
        rationale="r",
    )


class TestDissentLedger:
    def test_unanimous_is_full_confidence(self):
        crits = [_critique(p, "PASS") for p in ("auditor", "engineer", "adversary")]
        conf, ledger = build_dissent_ledger(crits, "PASS")
        assert conf == 1.0
        assert all(e["agreed"] for e in ledger)

    def test_split_lowers_confidence_and_flags_dissenters(self):
        crits = [
            _critique("auditor", "FAIL"),
            _critique("engineer", "PARTIAL"),
            _critique("adversary", "FAIL"),
            _critique("security_model", "PARTIAL"),
        ]
        conf, ledger = build_dissent_ledger(crits, "FAIL")
        assert conf == 0.5  # 2 of 4 agree with the sealed FAIL
        dissenters = [e for e in ledger if not e["agreed"]]
        assert {e["persona"] for e in dissenters} == {"engineer", "security_model"}

    def test_empty(self):
        assert build_dissent_ledger([], "INSUFFICIENT") == (0.0, [])

    def test_panel_result_carries_defaults(self):
        # Backward-compatible: constructing without the new fields still works.
        pr = PanelResult(critiques=[], final_verdict="INSUFFICIENT")
        assert pr.consensus_confidence == 0.0
        assert pr.dissent_ledger == []


class TestConfidenceLabel:
    def test_thresholds(self):
        assert confidence_label(1.0) == "unanimous"
        assert confidence_label(0.75) == "strong"
        assert confidence_label(0.5) == "split"
        assert confidence_label(0.25) == "contested"


class TestVerdictChanges:
    def test_no_second_round_is_empty(self):
        r1 = PanelResult(critiques=[_critique("auditor", "PASS")], final_verdict="PASS")
        assert build_verdict_changes(r1, None) == []

    def test_detects_flip_from_counter_search(self):
        r1 = PanelResult(
            critiques=[_critique("auditor", "PASS"), _critique("adversary", "PARTIAL")],
            final_verdict="PARTIAL",
        )
        r2 = PanelResult(
            critiques=[_critique("auditor", "FAIL"), _critique("adversary", "FAIL")],
            final_verdict="FAIL",
        )
        changes = {c["persona"]: c for c in build_verdict_changes(r1, r2)}
        assert changes["auditor"]["from"] == "PASS"
        assert changes["auditor"]["to"] == "FAIL"
        assert changes["auditor"]["changed"] is True


class TestMitreCrosswalk:
    def test_catalog_resolves_techniques_for_cc61(self):
        import json
        from importlib.resources import files

        catalog = json.loads((files("aec.priors") / "catalog.json").read_text())
        entry = catalog["control_id_index"]["CC6.1"]
        ids = {t["id"] for t in entry["mitre_attack"]}
        assert "T1078" in ids  # Valid Accounts — the MFA/access lens
        assert all("name" in t and "id" in t for t in entry["mitre_attack"])

    def test_control_mapper_threads_mitre(self):
        from aec.agent.nodes import control_mapper

        out = control_mapper({"control_id": "CC6.1"})
        match = out["matched_controls"][0]
        assert match["mitre_attack"], "control_mapper should carry MITRE techniques"
        assert match["mitre_attack"][0]["id"].startswith("T")

    def test_web_resolver(self):
        from web.main import _resolve_mitre

        assert any(t["id"] == "T1078" for t in _resolve_mitre("CC6.1"))
        assert _resolve_mitre("does-not-exist") == []
