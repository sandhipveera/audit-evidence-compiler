# Task 007 — Three-Agent Panel Debate

**Goal:** Replace the single-LLM "is this evidence sufficient?" call with a three-persona panel whose disagreement is the final report's strongest signal.

## Why this is the differentiator

Most hackathon submissions will produce confident single-LLM verdicts. This one produces verdicts that visibly survived an internal argument. The debate transcript itself becomes auditor-grade documentation ("the agent considered these objections and recorded them") — which is precisely what GRC artifacts need.

## Personas — three vendors, three roles

Each persona runs on a *different model from a different vendor*. This is deliberate: disagreement between independently-trained models is meaningful signal; disagreement between same-model-different-prompts is performative. The audit story we're selling — *"three independent reasoners had to agree"* — only holds if they're actually independent.

| Persona | Default model | Vendor | Why this model |
|---|---|---|---|
| **Auditor** | `claude-sonnet-4-6` | Anthropic | Best at nuanced literal reading of policy/control language; measured tone; strong on "does this satisfy the control as written" |
| **Engineer** | `gpt-5` (or `gpt-4.1` if 5 unavailable) | OpenAI | Strong structured technical reasoning; reads SPL well; good at edge cases (NULLs, service accounts, sourcetype gaps) |
| **Adversary** | `gemini-2.5-pro` (or `deepseek-r1` via OpenRouter) | Google / OpenRouter | Extended-thinking models probe harder and surface counter-examples more aggressively; different vendor lineage = genuine variance |

All three are configurable via frontmatter in `src/aec/agent/personas/*.md`:
```yaml
---
provider: anthropic   # anthropic | openai | openrouter
model: claude-sonnet-4-6
temperature: 0.3
---
# (system prompt follows)
```

Same input to all three: `EvidenceSnapshot + control_text + spl_executed`. Different system prompt + different model.

## Concurrency + cost

- All three calls fire in parallel via `asyncio.gather`. Total panel latency ≈ slowest single call (~3–5s), not sum.
- Cost per audit run: ~$0.10–0.30 (3 calls × ~10 findings × ~1K tokens each at mixed pricing). Trivial.

## Graceful degradation

If any vendor 5xxs or rate-limits at runtime:
1. Log the failure with vendor + reason
2. Fall back to a 2-model panel (consensus rule still works with 2)
3. If two fail → fall back to single-model mode (all 3 personas → Claude with prompt-only diversity)
4. The `audit_trail.jsonl` records which mode was used so the panel claim isn't overstated when degraded

This means the recorded demo can run on the 3-model panel, and judges who clone the repo without all 3 API keys still get a working agent (single-vendor mode, clearly labeled).

## Persona authoring

Each persona prompt lives in `src/aec/agent/personas/<name>.md` with YAML frontmatter (see above). The prompts are version-controlled, human-readable, and editable without code changes — so you can iterate on persona voice between demo takes.

## State models

```python
class Critique(BaseModel):
    persona: Literal["auditor", "engineer", "adversary"]
    model: str                # e.g., "anthropic/claude-sonnet-4-6"
    verdict: Literal["PASS", "PARTIAL", "FAIL", "INSUFFICIENT"]
    confidence: float         # 0.0–1.0
    rationale: str            # <= 400 chars
    concerns: list[str]
    recommended_additional_searches: list[str] = []  # adversary mainly
    latency_ms: int
    fallback_used: bool = False  # true if this critique ran on a fallback model

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

- `src/aec/agent/panel.py` — orchestrator: parallel persona calls + consensus + degradation logic
- `src/aec/agent/llm_router.py` — thin wrapper that takes `(provider, model)` and dispatches to anthropic / openai / openrouter SDKs with uniform return shape
- `src/aec/agent/personas/auditor.md` — frontmatter: anthropic / claude-sonnet-4-6
- `src/aec/agent/personas/engineer.md` — frontmatter: openai / gpt-5
- `src/aec/agent/personas/adversary.md` — frontmatter: openrouter / google/gemini-2.5-pro
- Wire into `src/aec/agent/graph.py` after Evidence Normalizer
- `tests/test_panel.py` with 4 fixture cases (unanimous PASS, mixed, all-FAIL, adversary surfaces counter-search) + 1 degradation test (one vendor returns 5xx)
- `.env.example` updated with `OPENAI_API_KEY` and `OPENROUTER_API_KEY`

## Out of scope (v2)

- Multi-turn debate where personas see each other's critiques (one-shot for MVP)
- Voting weighted by historical persona accuracy
- Per-control persona tuning (e.g., "for access controls weight the adversary higher")
- Speech synthesis of the debate for the demo video (stretch — would be fun)

## Demo cue

In the 3-min video, after the pipeline runs, show ~12 seconds of the panel transcript scrolling on screen. Voice-over: *"Three personas argue about every finding. Lowest verdict wins. The debate ships with the report."*
