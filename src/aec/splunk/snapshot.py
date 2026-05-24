"""Snapshot fetcher — pulls evidence from Splunk and caches locally."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aec.splunk.client import SplunkClient

log = logging.getLogger(__name__)

CACHE_DIR = Path(".aec_cache")

SPL_BY_CONTROL: dict[str, str] = {
    "CC6.1": (
        "index=auth EventCode=4625 OR EventCode=4624 "
        "| stats count by user, EventCode "
        "| join user [search index=auth mfa_status=* | stats values(mfa_status) as mfa by user]"
    ),
    "CC7.2": (
        "index=security sourcetype=incident_tracking "
        "| stats count by severity, status, time_to_respond "
        '| eval response_sla=if(time_to_respond<=240,"met","breached")'
    ),
    "A.9.2.1": (
        "index=iam sourcetype=user_provisioning "
        "| stats count by action, approver, department "
        '| eval approved=if(isnotnull(approver),"yes","no")'
    ),
}


def _cache_key(control_id: str, time_window: str) -> str:
    return f"{control_id}_{time_window}".replace(".", "_").replace(" ", "_")


def _cache_path(control_id: str, time_window: str) -> Path:
    return CACHE_DIR / f"{_cache_key(control_id, time_window)}.json"


def _read_cache(control_id: str, time_window: str) -> dict[str, Any] | None:
    path = _cache_path(control_id, time_window)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "control_id" in data:
            return data
    except (json.JSONDecodeError, OSError):
        log.warning("Corrupted cache at %s — will refetch", path)
    return None


def _write_cache(control_id: str, time_window: str, data: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(control_id, time_window)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def fetch_snapshot(
    control_id: str,
    time_window: str = "30d",
    client: SplunkClient | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Fetch an evidence snapshot for a control from Splunk.

    Returns a dict matching the sample file schema:
        {control_id, framework, snapshot_name, fetched_at, time_range,
         search, event_count, sample_events, aggregations}

    Uses local JSON cache under .aec_cache/ for deterministic repeated runs.
    """
    if use_cache:
        cached = _read_cache(control_id, time_window)
        if cached is not None:
            log.info("Cache hit for %s/%s", control_id, time_window)
            return cached

    if client is None:
        client = SplunkClient()

    spl = SPL_BY_CONTROL.get(control_id, f"index=main control_id={control_id}")
    earliest = f"-{time_window}" if not time_window.startswith("-") else time_window

    result = client.search(query=spl, earliest=earliest, latest="now", max_results=50)

    snapshot: dict[str, Any] = {
        "control_id": control_id,
        "framework": _infer_framework(control_id),
        "snapshot_name": f"{_infer_framework(control_id).lower()}-{control_id.lower().replace('.', '')}",
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
        "time_range": {"earliest": earliest, "latest": "now"},
        "search": spl,
        "event_count": result["event_count"],
        "sample_events": result["results"][:10],
        "aggregations": {},
    }

    if use_cache:
        _write_cache(control_id, time_window, snapshot)

    return snapshot


def _infer_framework(control_id: str) -> str:
    if control_id.startswith("CC"):
        return "SOC2"
    if control_id.startswith("A."):
        return "ISO27001"
    if control_id.startswith("PR.") or control_id.startswith("DE.") or control_id.startswith("RS."):
        return "NIST_CSF"
    return "UNKNOWN"
