"""Tests for the web dashboard server."""
from __future__ import annotations

import time

import pytest

from web.main import _check_rate_limit, _incident_results, _ip_timestamps, app


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    """Reset rate limiter state between tests."""
    _ip_timestamps.clear()
    _incident_results.clear()
    yield
    _ip_timestamps.clear()
    _incident_results.clear()


class TestRateLimiter:
    def test_allows_within_limit(self):
        assert _check_rate_limit("10.0.0.1") is True
        assert _check_rate_limit("10.0.0.1") is True
        assert _check_rate_limit("10.0.0.1") is True

    def test_blocks_over_limit(self):
        for _ in range(3):
            _check_rate_limit("10.0.0.2")
        assert _check_rate_limit("10.0.0.2") is False

    def test_separate_ips(self):
        for _ in range(3):
            _check_rate_limit("10.0.0.3")
        assert _check_rate_limit("10.0.0.4") is True

    def test_window_expiry(self):
        for _ in range(3):
            _check_rate_limit("10.0.0.5")
        _ip_timestamps["10.0.0.5"] = [time.monotonic() - 120]
        assert _check_rate_limit("10.0.0.5") is True


class TestControlsEndpoint:
    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        return TestClient(app)

    def test_list_controls(self, client):
        resp = client.get("/api/controls")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0
        for c in data:
            assert "sample" in c
            assert "control_id" in c
            assert "label" in c

    def test_root_returns_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_artifact_not_found(self, client):
        resp = client.get("/api/artifact/nonexistent-file.md")
        assert resp.status_code == 404


class TestWebSocketMessageShape:
    """Verify WebSocket message shapes by testing the pipeline with mocked panel."""

    @pytest.fixture(autouse=True)
    def fast_panel(self, monkeypatch):
        """Stub run_panel so the debate is instant and deterministic.

        Without this, the real panel probes (unavailable) vendors on a
        background task that outlives the test's WebSocket close, racing with
        TestClient teardown and intermittently surfacing a CancelledError.
        """
        from aec.agent.models import Critique, PanelResult

        result = PanelResult(
            critiques=[
                Critique(persona="auditor", model="m", transport="anthropic-cli",
                         verdict="PASS", confidence=0.9, rationale="r"),
            ],
            final_verdict="PASS",
            consensus_method="lowest_of_one",
        )

        async def _fake_run_panel(*args, **kwargs):
            return result

        monkeypatch.setattr("aec.agent.panel.run_panel", _fake_run_panel)

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        return TestClient(app)

    def test_websocket_run_start(self, client):
        with client.websocket_connect("/ws/run") as ws:
            ws.send_json({"sample": "soc2-cc61"})
            msg = ws.receive_json()
            assert msg["type"] == "run_start"
            assert "run_id" in msg
            assert msg["sample"] == "soc2-cc61"

    def test_websocket_snapshot_phase(self, client):
        with client.websocket_connect("/ws/run") as ws:
            ws.send_json({"sample": "soc2-cc61"})
            ws.receive_json()  # run_start
            msg = ws.receive_json()  # snapshot start
            assert msg["type"] == "phase"
            assert msg["name"] == "snapshot_fetch"
            assert msg["status"] == "start"

            msg = ws.receive_json()  # snapshot done
            assert msg["type"] == "phase"
            assert msg["name"] == "snapshot_fetch"
            assert msg["status"] == "done"
            assert msg["control_id"] == "CC6.1"
            assert msg["event_count"] == 1247

    def test_websocket_bad_sample(self, client):
        with client.websocket_connect("/ws/run") as ws:
            ws.send_json({"sample": "nonexistent"})
            ws.receive_json()  # run_start
            ws.receive_json()  # snapshot start
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "not found" in msg["message"]

    def test_websocket_rate_limit(self, client):
        for _ in range(3):
            _check_rate_limit("testclient")
        with client.websocket_connect("/ws/run") as ws:
            ws.send_json({"sample": "soc2-cc61"})
            msg = ws.receive_json()
            assert msg["type"] == "error"
            assert "Rate limit" in msg["message"]


class TestIncidentEndpoint:
    @pytest.fixture(autouse=True)
    def stub_incident_runner(self, monkeypatch):
        def fake_runner(run_id, controls, payload):
            _incident_results[run_id] = {
                "status": "complete",
                "controls": controls,
                "panel_results": [],
                "report_path": "incident_test.md",
            }

        monkeypatch.setattr("web.main._run_incident_panel_thread", fake_runner)

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        return TestClient(app)

    def test_post_incident_returns_controls(self, client):
        resp = client.post("/api/incident", json={
            "alert_name": "Brute Force Detected",
            "severity": "high",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "CC6.1" in data["controls"]
        assert "run_id" in data

    def test_post_incident_mfa_multi_control(self, client):
        resp = client.post("/api/incident", json={
            "alert_name": "MFA Bypass Detected",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "CC6.1" in data["controls"]
        assert "A.8.2" in data["controls"]
        assert "PR.AC-1" in data["controls"]

    def test_post_incident_searches_structured_result_fields(self, client):
        resp = client.post("/api/incident", json={
            "alert_name": "Security Alert",
            "result": {"signature": "failed login spike", "user": "svc_account"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["controls"] == ["CC6.1", "CC7.2"]

    def test_post_incident_rate_limited(self, client):
        for _ in range(3):
            resp = client.post("/api/incident", json={"alert_name": "MFA"})
            assert resp.status_code == 200

        resp = client.post("/api/incident", json={"alert_name": "MFA"})
        assert resp.status_code == 429

    def test_get_incident_not_found(self, client):
        resp = client.get("/api/incident/nonexistent-id")
        assert resp.status_code == 404


class TestArtifactPathTraversal:
    """Verify that artifact endpoint prevents path traversal."""

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        return TestClient(app)

    def test_path_traversal_blocked(self, client):
        resp = client.get("/api/artifact/../pyproject.toml")
        assert resp.status_code in (404, 400, 422)

    def test_path_traversal_dotdot(self, client):
        resp = client.get("/api/artifact/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (404, 400, 422)


class TestHealthEndpoint:
    """Liveness plus the gated, cached active vendor probe."""

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        return TestClient(app)

    @pytest.fixture(autouse=True)
    def _clear_probe_cache(self):
        from web import main as wm
        wm._vendor_probe_cache["at"] = 0.0
        wm._vendor_probe_cache["result"] = None
        yield
        wm._vendor_probe_cache["at"] = 0.0
        wm._vendor_probe_cache["result"] = None

    def test_liveness_is_cheap_and_unauthenticated(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"service": "ok"}

    def test_probe_requires_run_token_when_configured(self, client, monkeypatch):
        from web import main as wm
        monkeypatch.setattr(wm, "RUN_TOKEN", "secret")

        resp = client.get("/health?probe=1")
        assert resp.status_code == 401

    def test_probe_runs_and_caches_when_authorized(self, client, monkeypatch):
        from web import main as wm

        calls = {"n": 0}

        async def _fake_probe():
            calls["n"] += 1
            return {"all_up": True, "vendor_count": 4, "vendors": []}

        monkeypatch.setattr("aec.agent.panel.probe_vendors", _fake_probe)
        monkeypatch.setattr(wm, "RUN_TOKEN", "")  # dev: no token required

        first = client.get("/health?probe=1")
        assert first.status_code == 200
        body = first.json()
        assert body["service"] == "ok"
        assert body["all_up"] is True
        assert body["vendor_count"] == 4
        assert body["cached"] is False

        second = client.get("/health?probe=1")
        assert second.json()["cached"] is True
        assert calls["n"] == 1  # second request served from cache, no re-probe
