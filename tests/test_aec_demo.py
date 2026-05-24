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
