"""Tests for the Splunk custom search command auditcompiler.py."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure bin/ and bin/lib/ are importable
_bin_dir = str(Path(__file__).resolve().parent.parent / "splunk-app" / "auditcompiler" / "bin")
_lib_dir = os.path.join(_bin_dir, "lib")
for p in (_lib_dir, _bin_dir):
    if p not in sys.path:
        sys.path.insert(0, p)


FIXTURE_EVENTS = [
    {"_time": "2026-01-15T10:00:00", "user": "alice", "mfa_used": "true", "count": "5"},
    {"_time": "2026-01-15T10:01:00", "user": "bob", "mfa_used": "false", "count": "12"},
    {"_time": "2026-01-15T10:02:00", "user": "charlie", "mfa_used": "false", "count": "3"},
]


@pytest.fixture
def _mock_splunklib():
    """Provide a mock splunklib.searchcommands so we can import auditcompiler.py."""
    mock_searchcommands = MagicMock()

    mock_searchcommands.Configuration = lambda *a, **kw: (lambda cls: cls)
    mock_searchcommands.Option = MagicMock(return_value=None)
    mock_searchcommands.validators = MagicMock()
    mock_searchcommands.validators.Set = MagicMock(return_value=None)

    class FakeEventingCommand:
        pass

    mock_searchcommands.EventingCommand = FakeEventingCommand
    mock_searchcommands.dispatch = MagicMock()

    mock_module = MagicMock()
    mock_module.searchcommands = mock_searchcommands

    with patch.dict(sys.modules, {
        "splunklib": mock_module,
        "splunklib.searchcommands": mock_searchcommands,
    }):
        if "auditcompiler" in sys.modules:
            del sys.modules["auditcompiler"]
        import auditcompiler as mod
        yield mod
        if "auditcompiler" in sys.modules:
            del sys.modules["auditcompiler"]


class TestBuildSnapshot:
    def test_basic_snapshot_shape(self, _mock_splunklib):
        mod = _mock_splunklib
        snapshot = mod._build_snapshot_from_events(FIXTURE_EVENTS, "CC6.1", "SOC2")

        assert snapshot["control_id"] == "CC6.1"
        assert snapshot["framework"] == "SOC2"
        assert snapshot["event_count"] == 3
        assert len(snapshot["sample_events"]) == 3
        assert "aggregations" in snapshot

    def test_numeric_aggregation(self, _mock_splunklib):
        mod = _mock_splunklib
        snapshot = mod._build_snapshot_from_events(FIXTURE_EVENTS, "CC6.1", "SOC2")

        agg = snapshot["aggregations"]
        assert agg["event_count"] == 3
        assert agg["result_count_sum"] == 20

    def test_empty_events(self, _mock_splunklib):
        mod = _mock_splunklib
        snapshot = mod._build_snapshot_from_events([], "CC6.1", "SOC2")

        assert snapshot["event_count"] == 0
        assert snapshot["sample_events"] == []

    def test_snapshot_name_format(self, _mock_splunklib):
        mod = _mock_splunklib
        snapshot = mod._build_snapshot_from_events(FIXTURE_EVENTS, "A.5.16", "ISO27001")

        assert snapshot["snapshot_name"] == "iso27001-a516"

    def test_internal_fields_excluded_from_aggregation(self, _mock_splunklib):
        mod = _mock_splunklib
        events = [{"_time": "2026-01-15", "_raw": "test", "count": "10"}]
        snapshot = mod._build_snapshot_from_events(events, "CC6.1", "SOC2")

        agg = snapshot["aggregations"]
        assert "_time" not in str(agg.keys())
        assert "_raw" not in str(agg.keys())


class TestSeverityMap:
    def test_all_verdicts_mapped(self, _mock_splunklib):
        mod = _mock_splunklib
        for verdict in ("PASS", "PARTIAL", "FAIL", "INSUFFICIENT"):
            assert verdict in mod.SEVERITY_MAP


class TestFrameworkAliases:
    def test_soc2_aliases(self, _mock_splunklib):
        mod = _mock_splunklib
        assert mod.FRAMEWORK_ALIASES["SOC2"] == "SOC2"

    def test_iso_aliases(self, _mock_splunklib):
        mod = _mock_splunklib
        assert mod.FRAMEWORK_ALIASES["ISO"] == "ISO27001"
        assert mod.FRAMEWORK_ALIASES["ISO27001"] == "ISO27001"

    def test_nist_aliases(self, _mock_splunklib):
        mod = _mock_splunklib
        assert mod.FRAMEWORK_ALIASES["NIST"] == "NIST_CSF"
        assert mod.FRAMEWORK_ALIASES["NIST-CSF"] == "NIST_CSF"


class TestRunPanelSync:
    def test_import_error_returns_insufficient(self, _mock_splunklib):
        mod = _mock_splunklib
        snapshot = mod._build_snapshot_from_events(FIXTURE_EVENTS, "CC6.1", "SOC2")

        with patch.dict(sys.modules, {"aec.agent.panel": None}):
            with patch(f"{mod.__name__}._run_panel_sync") as mock_run:
                mock_run.return_value = {
                    "verdict": "INSUFFICIENT",
                    "severity": "critical",
                    "root_cause": "Import error: test",
                    "consensus_method": "error",
                    "transcript": "",
                    "panel_mode": "error",
                }
                result = mock_run(snapshot, "CC6.1", "SOC2")

        assert result["verdict"] == "INSUFFICIENT"
        assert "error" in result["panel_mode"]


class TestAuditCompilerCommand:
    def test_command_class_exists(self, _mock_splunklib):
        mod = _mock_splunklib
        assert hasattr(mod, "AuditCompilerCommand")

    def test_transform_enrich_mode(self, _mock_splunklib):
        mod = _mock_splunklib
        cmd = mod.AuditCompilerCommand.__new__(mod.AuditCompilerCommand)
        cmd.control = "CC6.1"
        cmd.framework = "SOC2"
        cmd.mode = "enrich"

        mock_panel_result = {
            "verdict": "PARTIAL",
            "severity": "medium",
            "root_cause": "[auditor] MFA gaps | [adversary] Suspicious logins",
            "consensus_method": "lowest_of_three",
            "transcript": "...",
            "panel_mode": "multi-vendor",
            "auditor_verdict": "PASS",
            "engineer_verdict": "PARTIAL",
            "adversary_verdict": "PARTIAL",
            "security_model_verdict": "PARTIAL",
            "critiques": "[]",
        }

        events = [dict(e) for e in FIXTURE_EVENTS]

        with patch(f"{mod.__name__}._run_panel_sync", return_value=mock_panel_result):
            results = list(cmd.transform(iter(events)))

        assert len(results) == 3
        for row in results:
            assert row["verdict"] == "PARTIAL"
            assert row["severity"] == "medium"
            assert "root_cause" in row
            assert row["control_id"] == "CC6.1"

    def test_transform_summary_mode(self, _mock_splunklib):
        mod = _mock_splunklib
        cmd = mod.AuditCompilerCommand.__new__(mod.AuditCompilerCommand)
        cmd.control = "CC6.1"
        cmd.framework = "SOC2"
        cmd.mode = "summary"

        mock_panel_result = {
            "verdict": "FAIL",
            "severity": "high",
            "root_cause": "[adversary] Critical gaps found",
            "consensus_method": "lowest_of_three",
            "transcript": "...",
            "panel_mode": "single-vendor",
            "auditor_verdict": "PASS",
            "engineer_verdict": "PARTIAL",
            "adversary_verdict": "FAIL",
            "security_model_verdict": "FAIL",
            "critiques": "[]",
        }

        events = [dict(e) for e in FIXTURE_EVENTS]

        with patch(f"{mod.__name__}._run_panel_sync", return_value=mock_panel_result):
            results = list(cmd.transform(iter(events)))

        assert len(results) == 1
        row = results[0]
        assert row["consensus"] == "FAIL"
        assert row["auditor_verdict"] == "PASS"
        assert row["adversary_verdict"] == "FAIL"
        assert row["security_model_verdict"] == "FAIL"
        assert row["event_count"] == "3"

    def test_transform_empty_events(self, _mock_splunklib):
        mod = _mock_splunklib
        cmd = mod.AuditCompilerCommand.__new__(mod.AuditCompilerCommand)
        cmd.control = "CC6.1"
        cmd.framework = "SOC2"
        cmd.mode = "enrich"

        results = list(cmd.transform(iter([])))

        assert len(results) == 1
        assert results[0]["verdict"] == "INSUFFICIENT"

    def test_framework_alias_resolution(self, _mock_splunklib):
        mod = _mock_splunklib
        cmd = mod.AuditCompilerCommand.__new__(mod.AuditCompilerCommand)
        cmd.control = "A.5.16"
        cmd.framework = "ISO"
        cmd.mode = "summary"

        mock_panel_result = {
            "verdict": "PASS",
            "severity": "info",
            "root_cause": "Compliant",
            "consensus_method": "lowest_of_three",
            "transcript": "",
            "panel_mode": "multi-vendor",
            "auditor_verdict": "PASS",
            "engineer_verdict": "PASS",
            "adversary_verdict": "PASS",
            "security_model_verdict": "PASS",
            "critiques": "[]",
        }

        events = [dict(e) for e in FIXTURE_EVENTS]

        with patch(f"{mod.__name__}._run_panel_sync", return_value=mock_panel_result):
            results = list(cmd.transform(iter(events)))

        assert results[0]["framework"] == "ISO27001"


class TestPackageScript:
    def test_package_script_exists(self):
        script = Path(__file__).resolve().parent.parent / "package.sh"
        assert script.exists(), "package.sh must exist at repo root"

    def test_package_script_is_valid_bash(self):
        script = Path(__file__).resolve().parent.parent / "package.sh"
        content = script.read_text()
        assert content.startswith("#!/usr/bin/env bash")
        assert "tar -czf" in content


class TestAppConf:
    def test_app_conf_exists(self):
        conf = (
            Path(__file__).resolve().parent.parent
            / "splunk-app" / "auditcompiler" / "default" / "app.conf"
        )
        assert conf.exists()

    def test_commands_conf_exists(self):
        conf = (
            Path(__file__).resolve().parent.parent
            / "splunk-app" / "auditcompiler" / "default" / "commands.conf"
        )
        assert conf.exists()
        content = conf.read_text()
        assert "auditcompiler" in content
        assert "python.version = python3" in content
