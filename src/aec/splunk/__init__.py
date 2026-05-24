"""Splunk integration — REST API client, snapshot fetching, and SPL validation."""

from aec.splunk.client import SplunkClient
from aec.splunk.snapshot import fetch_snapshot
from aec.splunk.spl_validator import run_spl

__all__ = ["SplunkClient", "fetch_snapshot", "run_spl"]
