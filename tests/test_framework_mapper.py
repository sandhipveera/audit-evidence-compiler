"""Tests for cross-framework control mapper."""
from __future__ import annotations

import pytest

from aec.priors.framework_mapper import (
    map_ask,
    map_concept,
    map_controls,
    parse_control_ref,
)

CATALOG = {
    "version": "0.1.0",
    "frameworks": ["ISO 27001", "NIST 800-53", "SOC 2", "COBIT"],
    "spl_hints": {
        "Access Control": {
            "spl_skeleton": "index=* (action=login OR action=privilege_grant) | stats count by user action",
        },
        "Logging & Monitoring": {
            "spl_skeleton": "| metadata type=sourcetypes index=* | eval lag_min=(now()-recentTime)/60",
        },
        "Risk Management": {
            "spl_skeleton": "| inputlookup asset_inventory | stats count by host",
        },
    },
    "controls": [
        {
            "internal_id": "CTRL-002",
            "name": "Asset Inventory",
            "control_family": "Asset Inventory",
            "frameworks": {"ISO 27001": False, "NIST 800-53": True, "SOC 2": True, "COBIT": False},
            "splunk_hint_category": "Risk Management",
            "splunk_hint": {
                "spl_skeleton": "| inputlookup asset_inventory | stats count by host",
            },
        },
        {
            "internal_id": "CTRL-003",
            "name": "Access Control Policy",
            "control_family": "Access Control Policy",
            "frameworks": {"ISO 27001": True, "NIST 800-53": True, "SOC 2": True, "COBIT": False},
            "splunk_hint_category": "Access Control",
            "splunk_hint": {
                "spl_skeleton": "index=* (action=login OR action=privilege_grant) | stats count by user action",
            },
        },
        {
            "internal_id": "CTRL-007",
            "name": "Asset Inventory",
            "control_family": "Asset Inventory",
            "frameworks": {"ISO 27001": True, "NIST 800-53": True, "SOC 2": False, "COBIT": False},
            "splunk_hint_category": "Risk Management",
            "splunk_hint": {
                "spl_skeleton": "| inputlookup asset_inventory | stats count by host",
            },
        },
        {
            "internal_id": "CTRL-020",
            "name": "User Access Review",
            "control_family": "User Access Review",
            "frameworks": {"ISO 27001": True, "NIST 800-53": True, "SOC 2": False, "COBIT": True},
            "splunk_hint_category": "Access Control",
            "splunk_hint": {
                "spl_skeleton": "index=* (action=login OR action=privilege_grant) | stats count by user action",
            },
        },
        {
            "internal_id": "CTRL-021",
            "name": "User Access Review",
            "control_family": "User Access Review",
            "frameworks": {"ISO 27001": False, "NIST 800-53": False, "SOC 2": True, "COBIT": True},
            "splunk_hint_category": "Access Control",
            "splunk_hint": {
                "spl_skeleton": "index=* (action=login OR action=privilege_grant) | stats count by user action",
            },
        },
        {
            "internal_id": "CTRL-013",
            "name": "Logging & Monitoring",
            "control_family": "Logging & Monitoring",
            "frameworks": {"ISO 27001": True, "NIST 800-53": False, "SOC 2": False, "COBIT": True},
            "splunk_hint_category": "Logging & Monitoring",
            "splunk_hint": {
                "spl_skeleton": "| metadata type=sourcetypes index=* | eval lag_min=(now()-recentTime)/60",
            },
        },
    ],
}


class TestParseControlRef:
    def test_soc2_cc61(self):
        cat_fw, disp_fw, ctrl = parse_control_ref("SOC2:CC6.1")
        assert cat_fw == "SOC 2"
        assert disp_fw == "SOC 2"
        assert ctrl == "CC6.1"

    def test_iso_a923(self):
        cat_fw, disp_fw, ctrl = parse_control_ref("ISO:A.8.2")
        assert cat_fw == "ISO 27001"
        assert disp_fw == "ISO 27001"
        assert ctrl == "A.8.2"

    def test_nist_csf_prac1(self):
        cat_fw, disp_fw, ctrl = parse_control_ref("NIST-CSF:PR.AC-1")
        assert cat_fw == "NIST CSF"
        assert disp_fw == "NIST CSF"
        assert ctrl == "PR.AC-1"

    def test_invalid_format(self):
        with pytest.raises(ValueError, match="expected"):
            parse_control_ref("CC6.1")

    def test_unknown_framework(self):
        with pytest.raises(ValueError, match="Unknown framework"):
            parse_control_ref("HIPAA:164.312")


class TestMapControlsSingleControl:
    def test_single_soc2_resolves(self):
        result = map_controls(["SOC2:CC6.1"], catalog=CATALOG)
        assert len(result["internal_controls"]) >= 1
        assert "CTRL-003" in result["internal_controls"]
        assert "SOC2:CC6.1" in result["framework_coverage"]

    def test_single_iso_resolves(self):
        result = map_controls(["ISO:A.8.2"], catalog=CATALOG)
        assert "CTRL-003" in result["internal_controls"]
        assert "CTRL-007" in result["internal_controls"]


class TestMapControlsNoOverlap:
    def test_two_different_categories(self):
        result = map_controls(["SOC2:CC6.1", "ISO:A.8.15"], catalog=CATALOG)
        assert "CTRL-003" in result["internal_controls"]
        assert "CTRL-013" in result["internal_controls"]
        assert len(result["shared_controls"]) == 0


class TestMapControlsSharedInternal:
    def test_three_controls_with_shared(self):
        result = map_controls(
            ["SOC2:CC6.1", "ISO:A.8.2", "NIST-CSF:PR.AC-1"],
            catalog=CATALOG,
        )
        assert "CTRL-003" in result["shared_controls"]
        assert len(result["minimal_spl_set"]) == 2

    def test_minimal_spl_fewer_than_controls(self):
        result = map_controls(
            ["SOC2:CC6.1", "ISO:A.8.2", "NIST-CSF:PR.AC-1"],
            catalog=CATALOG,
        )
        total_covered = sum(len(s["covers"]) for s in result["minimal_spl_set"])
        assert total_covered >= len(result["internal_controls"])
        assert len(result["minimal_spl_set"]) < 3


class TestMapControlsParsedRefs:
    def test_parsed_refs_populated(self):
        result = map_controls(["SOC2:CC6.1", "NIST-CSF:PR.AC-1"], catalog=CATALOG)
        assert len(result["parsed_refs"]) == 2
        assert result["parsed_refs"][0]["display_fw"] == "SOC 2"
        assert result["parsed_refs"][1]["display_fw"] == "NIST CSF"


class TestMapConcept:
    def test_access_control_across_frameworks(self):
        result = map_concept(
            "access-control", ["SOC2", "ISO", "NIST-CSF"], catalog=CATALOG,
        )
        assert len(result["internal_controls"]) >= 1
        assert len(result["minimal_spl_set"]) >= 1
        assert [ref["control_id"] for ref in result["parsed_refs"]] == [
            "CC6.1",
            "A.8.2",
            "PR.AC-1",
        ]

    def test_unknown_concept_raises(self):
        with pytest.raises(ValueError, match="Unknown concept"):
            map_concept("quantum-compliance", ["SOC2"])


class TestMapAsk:
    def test_access_control_natural_language(self):
        result = map_ask(
            "Show me evidence that satisfies access control across SOC 2, "
            "ISO 27001, and NIST CSF",
            catalog=CATALOG,
        )
        assert [ref["input"] for ref in result["parsed_refs"]] == [
            "SOC2:CC6.1",
            "ISO:A.8.2",
            "NIST-CSF:PR.AC-1",
        ]
        assert "CTRL-003" in result["shared_controls"]
