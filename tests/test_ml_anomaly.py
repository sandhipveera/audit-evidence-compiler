"""Tests for the Splunk-native ML anomaly node (Splunk AI at runtime)."""
from __future__ import annotations


from aec.splunk.client import SplunkSearchError
from aec.splunk import ml_anomaly
from aec.agent.nodes import splunk_ml_anomaly


class FakeClient:
    """Stand-in for SplunkClient.search — scripted per-call behaviour."""

    def __init__(self, behaviours):
        # behaviours: list of (result_dict | Exception) consumed in order
        self._behaviours = list(behaviours)
        self.searches: list[str] = []

    def search(self, query, **kwargs):
        self.searches.append(query)
        b = self._behaviours.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


_ANOMALY_ROW = {
    "registered_domain": "evil.example",
    "query_count": "900",
    "unique_queries": "162",
    "avg_qlen": "53.0",
    "log_event_prob": "-15.14",
    "probable_cause": "unique_queries",
}


class TestRunMlAnomaly:
    def test_builtin_engine_shapes_anomalies(self, monkeypatch):
        monkeypatch.setenv("AEC_SPLUNK_ML_ENGINE", "builtin")
        client = FakeClient([{"results": [_ANOMALY_ROW], "event_count": 26539}])
        out = ml_anomaly.run_ml_anomaly("CC7.2", "SOC2", client=client)

        assert out["available"] is True
        assert out["command"] == "anomalydetection"
        assert out["anomaly_count"] == 1
        assert out["anomalies"][0]["registered_domain"] == "evil.example"
        # empty-string fields are dropped during shaping
        assert "ProbableCause" not in out["anomalies"][0]
        assert "evil.example" in out["summary"]

    def test_auto_falls_back_when_mltk_missing(self, monkeypatch):
        monkeypatch.setenv("AEC_SPLUNK_ML_ENGINE", "auto")
        # 1st call (MLTK fit) errors with unknown-command; 2nd (builtin) succeeds.
        client = FakeClient([
            SplunkSearchError("Unknown search command 'fit'."),
            {"results": [_ANOMALY_ROW], "event_count": 100},
        ])
        out = ml_anomaly.run_ml_anomaly("CC7.2", client=client)

        assert out["available"] is True
        assert out["command"] == "anomalydetection"
        assert len(client.searches) == 2  # tried MLTK first, then built-in
        assert "fit DensityFunction" in client.searches[0]

    def test_unavailable_when_all_engines_fail(self, monkeypatch):
        monkeypatch.setenv("AEC_SPLUNK_ML_ENGINE", "builtin")
        client = FakeClient([SplunkSearchError("Splunk down")])
        out = ml_anomaly.run_ml_anomaly("CC6.1", client=client)
        assert out["available"] is False
        assert "Splunk down" in out["reason"]


class TestNode:
    def test_node_merges_ml_into_snapshot(self, monkeypatch):
        captured = {"engine": "Splunk built-in anomalydetection", "available": True,
                    "anomaly_count": 1, "anomalies": [_ANOMALY_ROW]}
        monkeypatch.setattr(ml_anomaly, "run_ml_anomaly", lambda *a, **k: captured)
        state = {"control_id": "CC7.2", "framework": "SOC2",
                 "splunk_snapshot": {"event_count": 5}, "node_durations_ms": {},
                 "completed_nodes": []}
        patch = splunk_ml_anomaly(state)
        assert patch["splunk_ml"]["available"] is True
        assert patch["splunk_snapshot"]["ml_anomaly"] is captured

    def test_sample_mode_uses_recorded_snapshot(self):
        recorded = {"available": True, "source": "recorded", "anomaly_count": 1}
        state = {"control_id": "CC7.2", "sample_name": "soc2-cc72",
                 "splunk_snapshot": {"ml_anomaly": recorded}, "node_durations_ms": {},
                 "completed_nodes": []}
        patch = splunk_ml_anomaly(state)
        assert patch["splunk_ml"] == recorded
