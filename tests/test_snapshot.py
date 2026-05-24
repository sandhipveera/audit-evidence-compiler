"""Tests for snapshot fetcher — mocked at the HTTP client boundary."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aec.splunk.snapshot import (
    _cache_path,
    _write_cache,
    fetch_snapshot,
)


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point CACHE_DIR to a temp directory for each test."""
    cache = tmp_path / ".aec_cache"
    monkeypatch.setattr("aec.splunk.snapshot.CACHE_DIR", cache)
    return cache


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.search.return_value = {
        "results": [
            {"user": "alice", "EventCode": "4624"},
            {"user": "bob", "EventCode": "4625"},
        ],
        "event_count": 100,
        "search_id": "sid-test",
    }
    return client


class TestFetchSnapshotShape:
    def test_returns_expected_dict_shape(self, mock_client):
        result = fetch_snapshot("CC6.1", time_window="30d", client=mock_client, use_cache=False)
        assert result["control_id"] == "CC6.1"
        assert result["framework"] == "SOC2"
        assert "snapshot_name" in result
        assert "fetched_at" in result
        assert "time_range" in result
        assert result["time_range"]["earliest"] == "-30d"
        assert result["time_range"]["latest"] == "now"
        assert "search" in result
        assert result["event_count"] == 100
        assert "sample_events" in result
        assert isinstance(result["sample_events"], list)
        assert "aggregations" in result

    def test_iso_control_inferred(self, mock_client):
        result = fetch_snapshot("A.9.2.1", client=mock_client, use_cache=False)
        assert result["framework"] == "ISO27001"

    def test_nist_csf_control_inferred(self, mock_client):
        result = fetch_snapshot("PR.AC-1", client=mock_client, use_cache=False)
        assert result["framework"] == "NIST_CSF"


class TestCacheHit:
    def test_cache_hit_returns_without_http(self, tmp_path, mock_client):
        cached_data = {
            "control_id": "CC6.1",
            "framework": "SOC2",
            "snapshot_name": "soc2-cc61",
            "fetched_at": "2026-05-24T12:00:00Z",
            "time_range": {"earliest": "-30d", "latest": "now"},
            "search": "index=auth",
            "event_count": 50,
            "sample_events": [],
            "aggregations": {},
        }
        _write_cache("CC6.1", "30d", cached_data)

        result = fetch_snapshot("CC6.1", time_window="30d", client=mock_client)
        assert result == cached_data
        mock_client.search.assert_not_called()


class TestCacheMiss:
    def test_cache_miss_writes_file(self, tmp_path, mock_client):
        fetch_snapshot("CC6.1", time_window="30d", client=mock_client)

        cache_file = _cache_path("CC6.1", "30d")
        assert cache_file.exists()
        stored = json.loads(cache_file.read_text())
        assert stored["control_id"] == "CC6.1"
        assert stored["event_count"] == 100


class TestCorruptedCache:
    def test_corrupted_cache_falls_back_to_live(self, tmp_path, mock_client, monkeypatch):
        from aec.splunk.snapshot import CACHE_DIR as real_cache

        real_cache.mkdir(parents=True, exist_ok=True)
        cache_file = _cache_path("CC6.1", "30d")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("not valid json {{{", encoding="utf-8")

        result = fetch_snapshot("CC6.1", time_window="30d", client=mock_client)
        assert result["event_count"] == 100
        mock_client.search.assert_called_once()

        refreshed = json.loads(cache_file.read_text())
        assert refreshed["event_count"] == 100
