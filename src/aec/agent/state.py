"""AgentState — typed state for the LangGraph pipeline + JSON checkpoint persistence."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

CHECKPOINT_DIR = Path(".aec_cache/checkpoints")


class ValidationResult(BaseModel):
    valid: bool
    error: str | None = None


class ControlMatch(BaseModel):
    control_id: str
    framework: str
    description: str = ""
    spl_hint: str = ""


class EvidenceSnapshot(BaseModel):
    snapshot_id: str
    control_id: str
    data: dict[str, Any] = Field(default_factory=dict)


class AgentState(BaseModel):
    control_id: str
    framework: str = ""
    time_window: dict[str, str] = Field(default_factory=dict)
    transport: Literal["official", "livehybrid", "rest", "sample"] = "official"

    matched_controls: list[ControlMatch] = []
    spl_query: str | None = None
    spl_validation: ValidationResult | None = None
    splunk_snapshot: dict[str, Any] | None = None
    panel_round_1: dict[str, Any] | None = None
    counter_searches: list[dict[str, Any]] = []
    panel_round_2: dict[str, Any] | None = None
    recurrence_result: dict[str, Any] | None = None
    final_verdict: str | None = None
    evidence_snapshots: list[dict[str, Any]] = []
    output_paths: dict[str, str] = Field(default_factory=dict)

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    started_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    node_durations_ms: dict[str, int] = Field(default_factory=dict)
    interrupts_raised: list[str] = []
    completed_nodes: list[str] = []

    sample_name: str | None = None
    review_mode: str = "auto"
    mcp_mode: str = "official"
    no_llm: bool = False
    enable_recurrence: bool = True
    max_counter_searches: int = 3
    window_str: str = "30d"


def write_checkpoint(state: AgentState) -> Path:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    path = CHECKPOINT_DIR / f"{state.run_id}.json"
    path.write_text(
        state.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return path


def read_checkpoint(run_id: str) -> AgentState | None:
    path = CHECKPOINT_DIR / f"{run_id}.json"
    if not path.exists():
        return None
    try:
        return AgentState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("Corrupted checkpoint %s", path)
        return None


def list_checkpoints() -> list[dict[str, Any]]:
    if not CHECKPOINT_DIR.exists():
        return []
    results = []
    for p in sorted(CHECKPOINT_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            state = AgentState.model_validate_json(p.read_text(encoding="utf-8"))
            results.append({
                "run_id": state.run_id,
                "control_id": state.control_id,
                "started_at": state.started_at,
                "completed_nodes": state.completed_nodes,
                "final_verdict": state.final_verdict,
            })
        except Exception:
            continue
    return results


def clean_checkpoints() -> int:
    if not CHECKPOINT_DIR.exists():
        return 0
    count = 0
    for p in CHECKPOINT_DIR.glob("*.json"):
        p.unlink()
        count += 1
    return count
