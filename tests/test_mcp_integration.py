"""Integration tests for MCP servers — requires live servers running locally.

Run with:
    AEC_MCP_LIVE_TEST=1 pytest tests/test_mcp_integration.py -v
"""
from __future__ import annotations

import os

import pytest

from aec.splunk.mcp.router import MCPRouter


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def require_live():
    if not os.environ.get("AEC_MCP_LIVE_TEST"):
        pytest.skip("AEC_MCP_LIVE_TEST not set — skipping live MCP tests")


class TestOfficialIntegration:
    async def test_probe_official(self):
        router = MCPRouter(preferred="splunk-official")
        try:
            await router.connect()
            info = await router.probe()
            assert info["ok"] is True
        finally:
            await router.close()

    async def test_execute_spl_official(self):
        router = MCPRouter(preferred="splunk-official")
        try:
            await router.connect()
            result = await router.execute_spl("index=botsv3 | head 3")
            assert result["event_count"] >= 0
        finally:
            await router.close()

    async def test_list_indexes_official(self):
        router = MCPRouter(preferred="splunk-official")
        try:
            await router.connect()
            indexes = await router.list_indexes()
            assert isinstance(indexes, list)
        finally:
            await router.close()


class TestLivehybridIntegration:
    async def test_probe_livehybrid(self):
        router = MCPRouter(preferred="livehybrid")
        try:
            await router.connect()
            info = await router.probe()
            assert info["ok"] is True
        finally:
            await router.close()

    async def test_execute_spl_livehybrid(self):
        router = MCPRouter(preferred="livehybrid")
        try:
            await router.connect()
            result = await router.execute_spl("index=botsv3 | head 3")
            assert result["event_count"] >= 0
        finally:
            await router.close()
