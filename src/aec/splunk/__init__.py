"""Splunk integration — REST API client, snapshot fetching, and SPL validation."""
from __future__ import annotations

from typing import Any

__all__ = ["SplunkClient", "fetch_snapshot", "run_spl"]


def __getattr__(name: str) -> Any:
    if name == "SplunkClient":
        from aec.splunk.client import SplunkClient

        return SplunkClient
    if name == "fetch_snapshot":
        from aec.splunk.snapshot import fetch_snapshot

        return fetch_snapshot
    if name == "run_spl":
        from aec.splunk.spl_validator import run_spl

        return run_spl
    raise AttributeError(name)
