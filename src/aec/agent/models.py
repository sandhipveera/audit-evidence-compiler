"""Pydantic models for panel debate state."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TransportSpec(BaseModel):
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class PersonaSpec(BaseModel):
    persona: Literal["auditor", "engineer", "adversary"]
    transports: list[TransportSpec]
    temperature: float = 0.5
    system_prompt: str = ""


class Critique(BaseModel):
    persona: Literal["auditor", "engineer", "adversary"]
    model: str
    transport: str
    verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    concerns: list[str] = Field(default_factory=list)
    recommended_additional_searches: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    fallback_used: bool = False


VERDICT_SEVERITY: dict[str, int] = {
    "PASS": 0,
    "PARTIAL": 1,
    "FAIL": 2,
    "INSUFFICIENT": 3,
}


class PanelResult(BaseModel):
    critiques: list[Critique]
    final_verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    consensus_method: Literal["lowest_of_three", "moderator_llm"] = "lowest_of_three"
    transcript: str = ""
    degraded: bool = False
    mode: str = "multi-vendor"
