"""Pydantic models for panel debate state."""
from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel, Field


class TransportSpec(BaseModel):
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


PersonaName = Literal["auditor", "engineer", "adversary", "security_model"]


class PersonaSpec(BaseModel):
    persona: PersonaName
    transports: list[TransportSpec]
    temperature: float = 0.5
    system_prompt: str = ""


class Critique(BaseModel):
    persona: PersonaName
    model: str
    transport: str
    verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    concerns: list[str] = Field(default_factory=list)
    recommended_additional_searches: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    fallback_used: bool = False


def _build_severity_order() -> dict[str, int]:
    insufficient_overrides_fail = os.getenv(
        "AEC_INSUFFICIENT_OVERRIDES_FAIL", "true"
    ).lower() != "false"
    return {
        "PASS": 0,
        "PARTIAL": 1,
        "FAIL": 2,
        "INSUFFICIENT": 3 if insufficient_overrides_fail else 1,
    }


VERDICT_SEVERITY: dict[str, int] = _build_severity_order()


class PanelResult(BaseModel):
    critiques: list[Critique]
    final_verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    consensus_method: Literal[
        "lowest_of_one",
        "lowest_of_two",
        "lowest_of_three",
        "lowest_of_four",
        "moderator_llm",
    ] = "lowest_of_three"
    transcript: str = ""
    degraded: bool = False
    mode: str = "multi-vendor"
    splunk_snapshot: dict[str, Any] | None = None
    adversary_followups: list[dict[str, Any]] = Field(default_factory=list)


class AdversarySearch(BaseModel):
    spl: str
    validation_status: Literal["accepted", "rejected"]
    rejection_reason: str | None = None
    executed: bool
    row_count: int = 0
    sample_events: list[dict[str, Any]] = Field(default_factory=list)
    execution_time_ms: int = 0
    error: str | None = None


class PanelResultWithRecurrence(BaseModel):
    round_1: PanelResult
    round_2: PanelResult | None = None
    counter_searches: list[AdversarySearch] = Field(default_factory=list)
    final_verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    final_consensus_round: Literal[1, 2]
    transcript: str
    iteration_count: int


class DriftMetric(BaseModel):
    name: str
    value_1: float | int
    value_2: float | int
    delta_abs: float
    delta_pct: float
    direction: Literal["improving", "stable", "worsening"]
    material: bool


class DriftAnalysis(BaseModel):
    window_1: dict[str, Any]
    window_2: dict[str, Any]
    metrics: list[DriftMetric]
    overall_direction: Literal["improving", "stable", "worsening"]
    summary: str


class TwoWindowSnapshot(BaseModel):
    control_id: str
    snapshot_1: dict[str, Any]
    snapshot_2: dict[str, Any]
    drift: DriftAnalysis
