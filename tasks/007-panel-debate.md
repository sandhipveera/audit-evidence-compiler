# Task 007 — Three-Agent Panel Debate

**Goal:** Replace the single-LLM "is this evidence sufficient?" call with a three-persona panel whose disagreement is the final report's strongest signal.

## Why this is the differentiator

Most hackathon submissions will produce confident single-LLM verdicts. This one produces verdicts that visibly survived an internal argument. The debate transcript itself becomes auditor-grade documentation ("the agent considered these objections and recorded them") — which is precisely what GRC artifacts need.

## Personas — three vendors, three roles, OAuth-first

Each persona runs on a *different model from a different vendor*. This is deliberate: disagreement between independently-trained models is meaningful signal; disagreement between same-model-different-prompts is performative. The audit story we're selling — *"three independent reasoners had to agree"* — only holds if they're actually independent.

**Default transport is OAuth-via-CLI** (zero per-call cost, subscription-billed). API-key SDK transport is the fallback.

| Persona | Default transport | Model | Auth | Per-call cost |
|---|---|---|---|---|
| **Auditor** | Claude Code CLI subprocess | `claude-sonnet-4-6` | Claude Max OAuth | $0 |
| **Engineer** | Codex CLI subprocess | `gpt-5` | ChatGPT OAuth (Codex) | $0 |
| **Adversary** | [`gemini-cli`](https://github.com/google-gemini/gemini-cli) subprocess | `gemini-2.5-pro` | Google account OAuth | $0 (free tier ~60 RPM) |

Fallback chain per persona is declared in frontmatter; the router auto-detects available auth at startup and records the chosen transport on every `Critique` (so the audit trail is honest about what actually ran).

Frontmatter declares a transport chain (first available wins):
```yaml
---
persona: adversary
transports:
  - gemini-cli                          # try first: OAuth, $0
  - gemini-api:                         # fallback: API key
      model: gemini-2.5-pro
  - openrouter-api:                     # last resort: any-vendor escape
      model: google/gemini-2.5-pro
temperature: 0.5
---
# (system prompt follows)
```

Same input to all three personas: `EvidenceSnapshot + control_text + spl_executed`. Different system prompt + different model + different vendor + different auth.

## LLM router — six transports, one interface

`src/aec/agent/llm_router.py` exposes:
```python
async def complete(persona_spec: PersonaSpec, prompt: str) -> CritiqueRaw
```

Six transports, each implementing `Transport.complete()`:
| Transport | How it works |
|---|---|
| `anthropic-cli` | `subprocess.Popen(["claude", "--print", "--output-format", "json"])`, stream prompt via stdin |
| `anthropic-api` | `anthropic.AsyncAnthropic().messages.create(...)` |
| `openai-cli` | `subprocess.Popen(["codex", "exec", "--json"])`, stream prompt via stdin |
| `openai-api` | `openai.AsyncOpenAI().chat.completions.create(...)` |
| `gemini-cli` | `subprocess.Popen(["gemini", "--prompt", "-", "--output", "json"])`, stream via stdin |
| `gemini-api` | `google.generativeai` SDK |
| `openrouter-api` | OpenAI-compatible SDK pointed at OpenRouter base URL |

Router auto-detects at startup which CLIs are on `PATH` and which API keys are in env. Chosen transport recorded on every `Critique`.

## Concurrency + cost + latency

- All three personas fire in parallel via `asyncio.gather` (subprocesses + tasks). Total panel latency ≈ slowest single transport.
- **Default OAuth-CLI mode:** ~$0 per run, ~10–30s per persona (CLI cold-start dominates), total ~15–30s.
- **API mode fallback:** ~$0.10–0.30 per run, ~2–5s per persona, total ~5s.
- For the *recorded demo*: use OAuth-CLI mode and screen-record the panel running in three terminal panes (see "Demo visual" below).
- For *contributors cloning the repo*: API mode is the friction-free path (just need 3 API keys, no CLI installs).

## Demo visual (tmux split)

The recorded demo uses three terminal panes side-by-side, each running one CLI:
```
┌────────────────┬────────────────┬────────────────┐
│ AUDITOR        │ ENGINEER       │ ADVERSARY      │
│ $ claude ...   │ $ codex ...    │ $ gemini ...   │
│ reading SOC2   │ parsing SPL... │ counter-search │
│ CC6.1 text...  │ for time bnd.. │ reveals 3 svc  │
│                │                │ accts bypass.. │
│ → PASS         │ → PARTIAL      │ → FAIL         │
└────────────────┴────────────────┴────────────────┘
                       ↓
            CONSENSUS: FAIL  (lowest-of-three)
```

This is the "three vendor AIs argue live on your screen" beat — judges remember it because it's the only entry where they can *see* the multi-vendor claim being true rather than asserted in a slide.

A small TUI (rich.Live + 3 Panel widgets) inside the `aec ask` CLI replicates this for the API-mode path too — same visual without the terminal-management overhead.

## Graceful degradation

Two layers of fallback:

**Transport fallback (per persona):** declared chain in frontmatter. If `gemini-cli` isn't installed → try `gemini-api` → try `openrouter-api`. First available wins. Logged.

**Panel fallback (when a whole vendor is down):**
1. If a persona's full transport chain exhausts → log the failure with vendor + reason
2. Fall back to a 2-model panel (consensus rule still works with 2)
3. If two fail → fall back to single-vendor mode (all 3 personas → Claude with prompt-only diversity)
4. `audit_trail.jsonl` records mode used so the multi-vendor claim isn't overstated when degraded

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
- `src/aec/agent/llm_router.py` — 6-transport dispatcher with auth auto-detect
- `src/aec/agent/transports/` — one file per transport (`anthropic_cli.py`, `anthropic_api.py`, `openai_cli.py`, `openai_api.py`, `gemini_cli.py`, `gemini_api.py`, `openrouter_api.py`)
- `src/aec/agent/personas/auditor.md` — chain: anthropic-cli → anthropic-api
- `src/aec/agent/personas/engineer.md` — chain: openai-cli → openai-api
- `src/aec/agent/personas/adversary.md` — chain: gemini-cli → gemini-api → openrouter-api
- `src/aec/agent/panel_view.py` — `rich.Live` 3-panel TUI used during `aec ask`
- Wire into `src/aec/agent/graph.py` after Evidence Normalizer
- `tests/test_panel.py` — 4 fixture cases + 1 degradation test + 1 transport-fallback test
- `.env.example` updated with all three API-key fallbacks
- `docs/auth-setup.md` — how to log into the 3 CLIs (1-line each)

## Out of scope (v2)

- Multi-turn debate where personas see each other's critiques (one-shot for MVP)
- Voting weighted by historical persona accuracy
- Per-control persona tuning (e.g., "for access controls weight the adversary higher")
- Speech synthesis of the debate for the demo video (stretch — would be fun)

## Demo cue

In the 3-min video, after the pipeline runs, show ~12 seconds of the panel transcript scrolling on screen. Voice-over: *"Three personas argue about every finding. Lowest verdict wins. The debate ships with the report."*
