"""Tests for SPL validator — mocked at the client boundary."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from aec.splunk.spl_validator import _validate_spl_syntax, run_spl


class TestSyntaxValidation:
    def test_empty_query(self):
        assert _validate_spl_syntax("") is not None
        assert "Empty" in _validate_spl_syntax("")

    def test_valid_query(self):
        assert _validate_spl_syntax("index=auth | stats count by user") is None

    def test_forbidden_command_delete(self):
        err = _validate_spl_syntax("index=main | delete")
        assert err is not None
        assert "delete" in err.lower()

    def test_forbidden_command_outputlookup(self):
        err = _validate_spl_syntax("index=main | outputlookup test.csv")
        assert err is not None
        assert "outputlookup" in err.lower()

    def test_unbalanced_brackets(self):
        err = _validate_spl_syntax("index=main [search index=auth")
        assert err is not None
        assert "bracket" in err.lower()

    def test_unbalanced_parens(self):
        err = _validate_spl_syntax("index=main | where (count > 5")
        assert err is not None
        assert "parenthes" in err.lower()

    def test_too_long_query(self):
        err = _validate_spl_syntax("x" * 5000)
        assert err is not None
        assert "length" in err.lower()


class TestRunSplValid:
    @patch("aec.splunk.spl_validator.SplunkClient")
    def test_valid_spl_returns_ok(self, MockClient):
        mock_instance = MagicMock()
        mock_instance.search.return_value = {
            "results": [{"user": "alice", "count": "10"}],
            "event_count": 42,
            "search_id": "sid-1",
        }
        MockClient.return_value = mock_instance

        result = run_spl("index=auth | stats count by user")
        assert result["ok"] is True
        assert result["hit_count"] == 42
        assert len(result["sample"]) == 1
        assert result["error"] is None

    @patch("aec.splunk.spl_validator.SplunkClient")
    def test_empty_result_returns_zero(self, MockClient):
        mock_instance = MagicMock()
        mock_instance.search.return_value = {
            "results": [],
            "event_count": 0,
            "search_id": "sid-2",
        }
        MockClient.return_value = mock_instance

        result = run_spl("index=nonexistent | stats count")
        assert result["ok"] is True
        assert result["hit_count"] == 0
        assert result["sample"] == []
        assert result["error"] is None


class TestRunSplErrors:
    def test_syntax_error_returns_error(self):
        result = run_spl("index=main | delete")
        assert result["ok"] is False
        assert "delete" in result["error"].lower()
        assert result["hit_count"] == 0
        assert result["sample"] == []

    def test_empty_query_returns_error(self):
        result = run_spl("")
        assert result["ok"] is False
        assert "Empty" in result["error"]

    @patch("aec.splunk.spl_validator.SplunkClient")
    def test_client_unavailable_returns_error(self, MockClient):
        MockClient.side_effect = ValueError("SPLUNK_HOST not set")
        result = run_spl("index=main | stats count")
        assert result["ok"] is False
        assert "SPLUNK_HOST" in result["error"]

    @patch("aec.splunk.spl_validator.SplunkClient")
    def test_search_exception_returns_error(self, MockClient):
        mock_instance = MagicMock()
        mock_instance.search.side_effect = Exception("connection refused")
        MockClient.return_value = mock_instance

        result = run_spl("index=auth | stats count by user")
        assert result["ok"] is False
        assert "connection refused" in result["error"]


class TestRunSplWithProvidedClient:
    def test_uses_provided_client(self):
        mock_client = MagicMock()
        mock_client.search.return_value = {
            "results": [{"x": "y"}],
            "event_count": 5,
            "search_id": "sid-3",
        }
        result = run_spl("index=main | head 5", client=mock_client)
        assert result["ok"] is True
        assert result["hit_count"] == 5
        mock_client.search.assert_called_once()
