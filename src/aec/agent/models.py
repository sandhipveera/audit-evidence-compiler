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
    # How much the panel agreed with the sealed verdict: fraction of vendors
    # whose own verdict equals final_verdict (0.0–1.0). With "lowest wins"
    # consensus a contested FAIL (1 of 4) scores low on purpose — that low
    # number IS the signal that the verdict is split, not unanimous.
    consensus_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Per-vendor record of agreement vs. the sealed verdict, for the dissent
    # ledger in the UI/report. Each entry: persona, model, verdict, agreed.
    dissent_ledger: list[dict[str, Any]] = Field(default_factory=list)


def build_dissent_ledger(
    critiques: list[Critique], final_verdict: str
) -> tuple[float, list[dict[str, Any]]]:
    """Return (consensus_confidence, dissent_ledger) for a set of critiques.

    consensus_confidence is the fraction of vendors agreeing with final_verdict.
    The ledger records each vendor's verdict and whether it agreed, so the UI
    can surface *who* dissented and the report can prove the debate happened.
    """
    if not critiques:
        return 0.0, []
    ledger: list[dict[str, Any]] = []
    agreed_count = 0
    for c in critiques:
        agreed = c.verdict == final_verdict
        agreed_count += int(agreed)
        ledger.append({
            "persona": c.persona,
            "model": c.model,
            "verdict": c.verdict,
            "agreed": agreed,
        })
    return round(agreed_count / len(critiques), 4), ledger


def confidence_label(consensus_confidence: float) -> str:
    """Human label for an agreement-derived confidence score."""
    if consensus_confidence >= 1.0:
        return "unanimous"
    if consensus_confidence >= 0.75:
        return "strong"
    if consensus_confidence >= 0.5:
        return "split"
    return "contested"


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
    # Per-vendor verdict changes between round 1 and round 2 (the visible effect
    # of the adversary's counter-searches). Each entry: persona, from, to,
    # changed. Empty when there was no second round.
    verdict_changes: list[dict[str, Any]] = Field(default_factory=list)


def build_verdict_changes(
    round_1: PanelResult, round_2: PanelResult | None
) -> list[dict[str, Any]]:
    """Diff per-vendor verdicts across the two rounds.

    Surfaces where the counter-evidence loop flipped a vendor's verdict — the
    "visible self-correction" that distinguishes a real debate from one shot.
    """
    if round_2 is None:
        return []
    r1_by_persona = {c.persona: c.verdict for c in round_1.critiques}
    changes: list[dict[str, Any]] = []
    for c in round_2.critiques:
        before = r1_by_persona.get(c.persona)
        if before is None:
            continue
        changes.append({
            "persona": c.persona,
            "from": before,
            "to": c.verdict,
            "changed": before != c.verdict,
        })
    return changes


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
