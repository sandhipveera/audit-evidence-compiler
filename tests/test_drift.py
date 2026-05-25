"""Tests for two-window drift detection — computation, thresholds, direction logic."""
from __future__ import annotations

from aec.agent.models import DriftAnalysis, DriftMetric
from aec.splunk.drift import (
    _classify_direction,
    _compute_overall_direction,
    compute_drift,
    format_drift_persona_appendix,
    format_drift_transcript,
)


SNAPSHOT_Q1 = {
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
        "source_index": "botsv3",
        "source_sourcetype": "o365:management:activity",
    },
}

SNAPSHOT_Q2 = {
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
        "source_index": "botsv3",
        "source_sourcetype": "o365:management:activity",
    },
}


class TestClassifyDirection:
    def test_mfa_drop_is_worsening(self):
        assert _classify_direction("mfa_enforced_pct", -14.46, 5.0) == "worsening"

    def test_mfa_increase_is_improving(self):
        assert _classify_direction("mfa_enforced_pct", 10.0, 5.0) == "improving"

    def test_failed_logins_increase_is_worsening(self):
        assert _classify_direction("failed_logins", 161.0, 5.0) == "worsening"

    def test_failed_logins_decrease_is_improving(self):
        assert _classify_direction("failed_logins", -30.0, 5.0) == "improving"

    def test_unknown_metric_within_threshold_is_stable(self):
        assert _classify_direction("unique_users", 3.0, 5.0) == "stable"

    def test_unknown_metric_beyond_threshold_is_stable(self):
        assert _classify_direction("unique_users", 10.0, 5.0) == "stable"

    def test_within_threshold_always_stable(self):
        assert _classify_direction("mfa_enforced_pct", 4.9, 5.0) == "stable"
        assert _classify_direction("failed_logins", -4.9, 5.0) == "stable"


class TestComputeDrift:
    def test_basic_drift(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        assert isinstance(drift, DriftAnalysis)
        assert len(drift.metrics) > 0
        assert drift.window_1["earliest"] == "2018-08-01"
        assert drift.window_2["earliest"] == "2018-09-01"

    def test_mfa_metric_worsening(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        mfa = next(m for m in drift.metrics if m.name == "mfa_enforced_pct")
        assert mfa.value_1 == 0.83
        assert mfa.value_2 == 0.71
        assert mfa.delta_abs < 0
        assert mfa.delta_pct < 0
        assert mfa.direction == "worsening"
        assert mfa.material is True

    def test_failed_logins_worsening(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        fl = next(m for m in drift.metrics if m.name == "failed_logins")
        assert fl.value_2 > fl.value_1
        assert fl.direction == "worsening"
        assert fl.material is True

    def test_overall_direction_worsening(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        assert drift.overall_direction == "worsening"

    def test_summary_not_empty(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        assert len(drift.summary) > 0
        assert "Material" in drift.summary or "material" in drift.summary.lower()

    def test_identical_snapshots_stable(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q1)
        assert drift.overall_direction == "stable"
        assert all(m.delta_abs == 0 for m in drift.metrics)
        assert all(not m.material for m in drift.metrics)

    def test_non_numeric_fields_excluded(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        metric_names = {m.name for m in drift.metrics}
        assert "source_index" not in metric_names
        assert "source_sourcetype" not in metric_names

    def test_threshold_exactly_5_not_material(self):
        """5% exactly should NOT be flagged as material (strictly greater than)."""
        s1 = {
            "time_range": {"earliest": "a", "latest": "b"},
            "aggregations": {"metric_x": 100},
        }
        s2 = {
            "time_range": {"earliest": "c", "latest": "d"},
            "aggregations": {"metric_x": 105},
        }
        drift = compute_drift(s1, s2, threshold_pct=5.0)
        mx = drift.metrics[0]
        assert mx.delta_pct == 5.0
        assert mx.material is False

    def test_threshold_5_point_1_is_material(self):
        """5.1% should be material."""
        s1 = {
            "time_range": {"earliest": "a", "latest": "b"},
            "aggregations": {"metric_x": 1000},
        }
        s2 = {
            "time_range": {"earliest": "c", "latest": "d"},
            "aggregations": {"metric_x": 1051},
        }
        drift = compute_drift(s1, s2, threshold_pct=5.0)
        mx = drift.metrics[0]
        assert mx.delta_pct == 5.1
        assert mx.material is True

    def test_custom_threshold(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2, threshold_pct=200.0)
        assert all(not m.material for m in drift.metrics)
        assert drift.overall_direction == "stable"

    def test_zero_base_value(self):
        s1 = {
            "time_range": {"earliest": "a", "latest": "b"},
            "aggregations": {"count": 0},
        }
        s2 = {
            "time_range": {"earliest": "c", "latest": "d"},
            "aggregations": {"count": 10},
        }
        drift = compute_drift(s1, s2)
        m = drift.metrics[0]
        assert m.delta_pct == 100.0
        assert m.material is True

    def test_empty_aggregations(self):
        s1 = {"time_range": {"earliest": "a", "latest": "b"}, "aggregations": {}}
        s2 = {"time_range": {"earliest": "c", "latest": "d"}, "aggregations": {}}
        drift = compute_drift(s1, s2)
        assert drift.metrics == []
        assert drift.overall_direction == "stable"

    def test_missing_aggregations_key(self):
        s1 = {"time_range": {"earliest": "a", "latest": "b"}}
        s2 = {"time_range": {"earliest": "c", "latest": "d"}}
        drift = compute_drift(s1, s2)
        assert drift.metrics == []


class TestOverallDirection:
    def test_all_stable(self):
        metrics = [
            DriftMetric(name="a", value_1=1, value_2=1, delta_abs=0, delta_pct=0, direction="stable", material=False),
        ]
        assert _compute_overall_direction(metrics) == "stable"

    def test_worsening_majority(self):
        metrics = [
            DriftMetric(name="a", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="worsening", material=True),
            DriftMetric(name="b", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="worsening", material=True),
            DriftMetric(name="c", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="improving", material=True),
        ]
        assert _compute_overall_direction(metrics) == "worsening"

    def test_improving_majority(self):
        metrics = [
            DriftMetric(name="a", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="improving", material=True),
            DriftMetric(name="b", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="improving", material=True),
            DriftMetric(name="c", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="worsening", material=True),
        ]
        assert _compute_overall_direction(metrics) == "improving"

    def test_tie_is_stable(self):
        metrics = [
            DriftMetric(name="a", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="worsening", material=True),
            DriftMetric(name="b", value_1=1, value_2=2, delta_abs=1, delta_pct=100, direction="improving", material=True),
        ]
        assert _compute_overall_direction(metrics) == "stable"


class TestFormatDriftTranscript:
    def test_contains_table_headers(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        text = format_drift_transcript(drift)
        assert "## Drift analysis" in text
        assert "| Metric" in text
        assert "Window 1:" in text
        assert "Window 2:" in text

    def test_contains_metric_rows(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        text = format_drift_transcript(drift)
        assert "mfa_enforced_pct" in text
        assert "failed_logins" in text

    def test_contains_overall_direction(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        text = format_drift_transcript(drift)
        assert "WORSENING" in text


class TestFormatDriftPersonaAppendix:
    def test_contains_trend_instruction(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        appendix = format_drift_persona_appendix(drift)
        assert "compliance TREND" in appendix
        assert "worsening" in appendix
        assert "mfa_enforced_pct" in appendix

    def test_contains_window_info(self):
        drift = compute_drift(SNAPSHOT_Q1, SNAPSHOT_Q2)
        appendix = format_drift_persona_appendix(drift)
        assert "2018-08-01" in appendix
        assert "2018-09-01" in appendix
