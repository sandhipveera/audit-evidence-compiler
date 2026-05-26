"""Tests for SOC incident response mode — alert-to-control mapping."""
from __future__ import annotations

from aec.agent.incident_mapper import (
    alert_fields_from_payload,
    build_incident_report,
    map_alert_to_controls,
    sample_for_control,
)


class TestMapAlertToControls:
    def test_brute_force_maps_to_access_and_incident_response(self):
        controls = map_alert_to_controls("Brute Force Detected", "")
        assert controls == ["CC6.1", "CC7.2"]

    def test_mfa_maps_to_multiple_controls(self):
        controls = map_alert_to_controls("MFA Bypass Detected — 23 accounts", "")
        assert "CC6.1" in controls
        assert "A.9.2.3" in controls
        assert "PR.AC-1" in controls

    def test_login_maps_to_cc61_and_cc72(self):
        controls = map_alert_to_controls("Failed Login Spike", "")
        assert "CC6.1" in controls
        assert "CC7.2" in controls

    def test_privilege_escalation(self):
        controls = map_alert_to_controls("Privilege Escalation Detected", "")
        assert "CC6.1" in controls
        assert "A.9.2.3" in controls

    def test_ransomware_maps_to_incident_response(self):
        controls = map_alert_to_controls("Ransomware Activity Detected", "")
        assert "CC7.2" in controls
        assert "RC.RP-1" in controls

    def test_body_text_is_also_searched(self):
        controls = map_alert_to_controls("Security Alert", "mfa bypass attempt")
        assert "CC6.1" in controls
        assert "PR.AC-1" in controls

    def test_case_insensitive(self):
        controls = map_alert_to_controls("BRUTE FORCE ATTACK", "")
        assert "CC6.1" in controls

    def test_multiple_keywords_union(self):
        controls = map_alert_to_controls(
            "MFA Brute Force with Lateral Movement", ""
        )
        assert "CC6.1" in controls
        assert "CC7.2" in controls
        assert "A.9.2.3" in controls
        assert "PR.AC-1" in controls

    def test_unknown_alert_defaults_to_cc61(self):
        controls = map_alert_to_controls("Unknown Event Type", "something else")
        assert controls == ["CC6.1"]

    def test_returns_deterministic_mapping_order(self):
        controls = map_alert_to_controls("MFA bypass with brute force", "")
        assert controls == ["CC6.1", "A.9.2.3", "PR.AC-1", "CC7.2"]

    def test_anomaly_maps_to_cc72(self):
        controls = map_alert_to_controls("Anomalous Activity Detected", "")
        assert "CC7.2" in controls

    def test_lateral_movement(self):
        controls = map_alert_to_controls("Lateral Movement Detected", "")
        assert "CC6.1" in controls
        assert "CC7.2" in controls

    def test_extracts_non_message_result_fields(self):
        alert_name, alert_body = alert_fields_from_payload({
            "alert_name": "Security Alert",
            "result": {"signature": "failed login spike", "user": "svc_account"},
        })
        assert alert_name == "Security Alert"
        assert "failed login spike" in alert_body
        assert map_alert_to_controls(alert_name, alert_body) == ["CC6.1", "CC7.2"]

    def test_mapped_controls_have_sample_fallbacks(self):
        assert sample_for_control("PR.AC-1") == "soc2-cc61"
        assert sample_for_control("RC.RP-1") == "soc2-cc72"


class TestBuildIncidentReport:
    def test_basic_report_structure(self):
        alert = {"alert_name": "Test Alert", "severity": "high"}
        controls = ["CC6.1"]
        panel_results = [
            {
                "control_id": "CC6.1",
                "verdict": "FAIL",
                "confidence": 0.91,
                "rationale": "MFA not enforced.",
                "recommendations": ["Enforce MFA for all accounts"],
            }
        ]
        report = build_incident_report(alert, controls, panel_results, 5.0)

        assert "# Incident Compliance Report" in report
        assert "Test Alert" in report
        assert "high" in report
        assert "CC6.1" in report
        assert "FAIL" in report
        assert "MFA not enforced." in report
        assert "Enforce MFA for all accounts" in report
        assert "5.0s" in report

    def test_multiple_controls(self):
        alert = {"alert_name": "Complex Alert"}
        controls = ["CC6.1", "CC7.2"]
        panel_results = [
            {
                "control_id": "CC6.1",
                "verdict": "FAIL",
                "confidence": 0.85,
                "rationale": "Access control gap.",
                "recommendations": ["Fix access controls"],
            },
            {
                "control_id": "CC7.2",
                "verdict": "PARTIAL",
                "confidence": 0.74,
                "rationale": "Monitoring partially effective.",
                "recommendations": ["Improve monitoring"],
            },
        ]
        report = build_incident_report(alert, controls, panel_results, 10.0)

        assert "CC6.1" in report
        assert "CC7.2" in report
        assert "FAIL" in report
        assert "PARTIAL" in report

    def test_pass_verdict_no_actions(self):
        alert = {"alert_name": "Benign Alert"}
        controls = ["CC6.1"]
        panel_results = [
            {
                "control_id": "CC6.1",
                "verdict": "PASS",
                "confidence": 0.95,
                "rationale": "All controls effective.",
                "recommendations": [],
            }
        ]
        report = build_incident_report(alert, controls, panel_results, 2.0)

        assert "No immediate actions required" in report

    def test_alert_result_fields_extracted(self):
        alert = {
            "alert_name": "Brute Force",
            "result": {"count": 847, "user": "svc_account", "message": "spike"},
        }
        controls = ["CC6.1"]
        panel_results = [
            {
                "control_id": "CC6.1",
                "verdict": "FAIL",
                "confidence": 0.9,
                "rationale": "Brute force detected.",
                "recommendations": [],
            }
        ]
        report = build_incident_report(alert, controls, panel_results, 1.0)
        assert "svc_account" in report

    def test_report_tolerates_string_result(self):
        alert = {"search_name": "String Result Alert", "result": "failed login spike"}
        controls = ["CC6.1"]
        panel_results = [
            {
                "control_id": "CC6.1",
                "verdict": "INSUFFICIENT",
                "confidence": 0.0,
                "rationale": "Panel skipped.",
                "recommendations": [],
            }
        ]
        report = build_incident_report(alert, controls, panel_results, 1.0)
        assert "String Result Alert" in report
        assert "N/A events" in report
