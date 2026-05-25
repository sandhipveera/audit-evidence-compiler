"""Tests for the hackathon demo CLI wrapper."""
from __future__ import annotations

from unittest.mock import patch

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
