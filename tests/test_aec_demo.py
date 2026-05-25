"""Tests for the hackathon demo CLI wrapper."""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from cli import aec_demo


def test_aec_sample_env_satisfies_required_args(monkeypatch):
    seen = {}

    async def fake_run(args):
        seen["sample"] = args.sample
        seen["control"] = args.control

    monkeypatch.setenv("AEC_SAMPLE", "soc2-cc61")
    monkeypatch.setattr(aec_demo, "_run", fake_run)

    with patch("sys.argv", ["aec_demo"]):
        aec_demo.main()

    assert seen == {"sample": "soc2-cc61", "control": None}


def test_mcp_defaults_to_official(monkeypatch):
    seen = {}

    async def fake_run(args):
        seen["mcp"] = args.mcp

    monkeypatch.delenv("AEC_SPLUNK_MCP_SERVER", raising=False)
    monkeypatch.setattr(aec_demo, "_run", fake_run)

    with patch("sys.argv", ["aec_demo", "--control", "CC6.1"]):
        aec_demo.main()

    assert seen["mcp"] == "official"


def test_mcp_env_selects_livehybrid(monkeypatch):
    seen = {}

    async def fake_run(args):
        seen["mcp"] = args.mcp

    monkeypatch.setenv("AEC_SPLUNK_MCP_SERVER", "livehybrid")
    monkeypatch.setattr(aec_demo, "_run", fake_run)

    with patch("sys.argv", ["aec_demo", "--control", "CC6.1"]):
        aec_demo.main()

    assert seen["mcp"] == "livehybrid"


def test_live_flag_defaults_to_rest(monkeypatch):
    seen = {}

    async def fake_run(args):
        seen["mcp"] = args.mcp

    monkeypatch.delenv("AEC_SPLUNK_MCP_SERVER", raising=False)
    monkeypatch.setattr(aec_demo, "_run", fake_run)

    with patch("sys.argv", ["aec_demo", "--control", "CC6.1", "--live"]):
        aec_demo.main()

    assert seen["mcp"] == "rest"


def test_invalid_mcp_env_fails(monkeypatch):
    monkeypatch.setenv("AEC_SPLUNK_MCP_SERVER", "bogus")

    with patch("sys.argv", ["aec_demo", "--control", "CC6.1"]):
        try:
            aec_demo.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("Expected invalid AEC_SPLUNK_MCP_SERVER to fail")


@pytest.mark.asyncio
async def test_drift_rest_compare_passes_window_bounds(monkeypatch):
    calls = []

    def fake_fetch_snapshot(control_id, time_window, latest, client, live):
        calls.append((control_id, time_window, latest, client, live))
        return {
            "control_id": control_id,
            "framework": "SOC2",
            "snapshot_name": f"{time_window}-{latest}",
            "time_range": {"earliest": time_window, "latest": latest},
            "search": "index=botsv3 | stats count",
            "event_count": 10,
            "sample_events": [],
            "aggregations": {"mfa_enforced_pct": 0.9 if len(calls) == 1 else 0.8},
        }

    monkeypatch.setenv("SPLUNK_HOST", "https://splunk.example.com:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "token")
    monkeypatch.setattr("aec.splunk.client.SplunkClient", lambda verify_ssl=False: "client")
    monkeypatch.setattr("aec.splunk.snapshot.fetch_snapshot", fake_fetch_snapshot)

    args = argparse.Namespace(
        control="CC6.1",
        compare="2018-08-01:2018-08-15,2018-09-01:2018-09-15",
        drift_window=None,
        drift_threshold=5.0,
        no_llm=True,
        live=True,
        mcp="rest",
        window="30d",
    )

    await aec_demo._run_drift(args)

    assert calls == [
        ("CC6.1", "2018-08-01", "2018-08-15", "client", True),
        ("CC6.1", "2018-09-01", "2018-09-15", "client", True),
    ]


@pytest.mark.asyncio
async def test_load_via_mcp_passes_latest_and_derives_aggregations():
    class FakeRouter:
        mcp_server_tag = "splunk-official-test"

        def __init__(self):
            self.calls = []

        async def execute_spl(self, query, time_window="-30d", latest="now"):
            self.calls.append((query, time_window, latest))
            return {
                "event_count": 42,
                "results": [{"count": "5"}, {"count": "7"}],
                "search_id": "sid",
            }

    router = FakeRouter()
    snapshot = await aec_demo._load_via_mcp(
        "CC6.1",
        "2018-08-01",
        router,
        latest="2018-08-15",
    )

    assert router.calls[0][1:] == ("2018-08-01", "2018-08-15")
    assert snapshot["time_range"] == {
        "earliest": "2018-08-01",
        "latest": "2018-08-15",
    }
    assert snapshot["aggregations"]["event_count"] == 42
    assert snapshot["aggregations"]["result_count_sum"] == 12
    assert snapshot["mcp_server"] == "splunk-official-test"
