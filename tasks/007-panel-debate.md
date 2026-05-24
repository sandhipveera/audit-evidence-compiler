# Task 007 — Three-Agent Panel Debate

**Goal:** Replace the single-LLM "is this evidence sufficient?" call with a three-persona panel whose disagreement is the final report's strongest signal.

## Why this is the differentiator

Most hackathon submissions will produce confident single-LLM verdicts. This one produces verdicts that visibly survived an internal argument. The debate transcript itself becomes auditor-grade documentation ("the agent considered these objections and recorded them") — which is precisely what GRC artifacts need.

## Personas

Each runs as a separate LLM call with the same input (EvidenceSnapshot + control_text + spl_executed) but a different system prompt.

| Persona | System prompt focus | What it asks |
|---|---|---|
| **Auditor** | Read the control language literally. Be conservative. | Does the evidence satisfy the control as written? Is the time window sufficient? Is the population coverage complete? |
| **Engineer** | Read the SPL technically. | Is the query sound? Sourcetype coverage? Time-bound correctness? Edge cases (NULLs, service accounts, sampling)? |
| **Adversary** | Try to disprove the PASS verdict. Be skeptical. | What's not searched? What counter-evidence would exist if the control failed? Propose 1–3 concrete counter-searches. |

## State models

```python
class Critique(BaseModel):
    persona: Literal["auditor", "engineer", "adversary"]
    verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    confidence: float  # 0.0–1.0
    rationale: str     # <= 400 chars
    concerns: list[str]
    recommended_additional_searches: list[str] = []  # adversary mainly

class PanelResult(BaseModel):
    critiques: list[Critique]
    final_verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    consensus_method: Literal["lowest_of_three", "moderator_llm"]
    transcript: str  # markdown rendering of the debate
```

## Consensus rule

**Default: `lowest_of_three`.** Severity order: `PASS > PARTIAL > FAIL > INSUFFICIENT`. Final = min(verdicts). Rationale: audit findings are conservative by tradition — one dissenting critic should anchor the outcome. This is also what real audit committees do.

**Optional `moderator_llm`:** a 4th LLM call summarizing the debate and picking. Useful when critiques are confident-but-contradictory. Disabled by default (extra latency + cost).

## Optional: counter-search recurrence (1 round only)

If the adversary proposes `recommended_additional_searches` and `policy.allow_counter_searches=true`:
1. Each recommended SPL goes through Validator → Executor → Normalizer
2. New snapshots feed back into the Adversary persona for *one* refinement turn (no full re-debate — too expensive)
3. Adversary updates verdict; consensus re-runs

This is the killer demo beat: the agent finds its own blind spot and re-searches.

## Acceptance

- `python -m aec.agent.panel --snapshot fixture.json --control CC6.1` returns a `PanelResult` with 3 critiques and a final verdict.
- Three personas run in parallel (LangGraph parallel branches or `asyncio.gather`), not sequentially — keeps total latency near 1 LLM round-trip, not 3.
- Transcript renders to clean markdown for inclusion in `audit_trail.jsonl` and the optional `audit_package.md`.
- Fixture tests cover: unanimous PASS, mixed verdicts, all-FAIL, adversary surfaces counter-search.

## Files to create

- `src/aec/agent/panel.py` — three persona LLM callers + consensus
- `src/aec/agent/personas/` — three `.md` files holding the system prompts (so they're version-controlled and editable without code changes)
- `src/aec/agent/personas/auditor.md`
- `src/aec/agent/personas/engineer.md`
- `src/aec/agent/personas/adversary.md`
- Wire into `src/aec/agent/graph.py` after Evidence Normalizer
- `tests/test_panel.py` with 4 fixture cases

## Out of scope (v2)

- Multi-turn debate where personas see each other's critiques (one-shot for MVP)
- Voting weighted by historical persona accuracy
- Per-control persona tuning (e.g., "for access controls weight the adversary higher")
- Speech synthesis of the debate for the demo video (stretch — would be fun)

## Demo cue

In the 3-min video, after the pipeline runs, show ~12 seconds of the panel transcript scrolling on screen. Voice-over: *"Three personas argue about every finding. Lowest verdict wins. The debate ships with the report."*
