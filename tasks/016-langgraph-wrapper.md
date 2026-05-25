# Task 016 — LangGraph wrapper + HITL approval gate + state checkpointing

**Goal:** Wrap the existing aec_demo pipeline as a proper LangGraph. Adds explicit state, durable checkpointing, and a human-in-the-loop interrupt before destructive/expensive steps (Splunk execution, panel debate). Makes the "agentic" claim rigorous instead of "we call async functions."

**Budget:** ~400 LOC + tests. ~2 days of YC time.

## Why this matters

Right now the pipeline is `async def main()` calling functions in sequence. That's "scripted automation." LangGraph adds:
- **Explicit state** (`AgentState` pydantic model) — every node reads/writes typed state
- **Checkpointing** — persist state between nodes; resume after failures
- **Interrupts** (`interrupt()`) — pause for human approve/edit/reject without rewriting orchestration
- **Observability** — LangSmith-style traces (we won't ship LangSmith, but the graph structure is what enables it later)

For judges who skim the repo, "wraps pipeline as a LangGraph" is the right signal that this is an agent system, not a script.

## Graph topology

```
START
  │
  ▼
control_mapper          (resolves CC6.1 → internal controls)
  │
  ▼
spl_generator           (LLM generates SPL targeting BOTS v3)
  │
  ▼
spl_validator           (policy.json guard)
  │
  ├─ rejected ─────────────────────────────────────┐
  ▼                                                 │
[HITL interrupt: review SPL?]                       │
  │ approve                                         │
  ▼                                                 │
mcp_executor            (runs SPL via active MCP)   │
  │                                                 │
  ▼                                                 ▼
evidence_normalizer                          formatter_gap
  │
  ▼
panel_round_1
  │
  ├─ no counter-searches ───┐
  ▼                          │
adversary_search_validator   │
  │                          │
  ▼                          │
mcp_executor (counter)       │
  │                          │
  ▼                          │
panel_round_2                │
  │                          │
  ▼                          ▼
consensus
  │
  ▼
[HITL interrupt: review verdict?]
  │ approve
  ▼
evidence_formatter
  │
  ▼
merkle_chain_sealer
  │
  ▼
END
```

Two interrupt points by default. Both off in `--review auto` (default); both on in `--review interactive`.

## State model

```python
class AgentState(BaseModel):
    # Inputs (set once)
    control_id: str
    framework: str
    time_window: dict
    transport: Literal["official", "livehybrid", "rest"]

    # Pipeline state (mutated by nodes)
    matched_controls: list[ControlMatch] = []
    spl_query: str | None = None
    spl_validation: ValidationResult | None = None
    splunk_snapshot: dict | None = None
    panel_round_1: PanelResult | None = None
    counter_searches: list[AdversarySearch] = []
    panel_round_2: PanelResult | None = None
    final_verdict: str | None = None
    evidence_snapshots: list[EvidenceSnapshot] = []
    output_paths: dict = {}

    # Provenance
    run_id: str
    started_at: datetime
    node_durations_ms: dict[str, int] = {}
    interrupts_raised: list[str] = []
```

## CLI surface

```
aec_demo --control CC6.1                              # auto mode (no interrupts)
aec_demo --control CC6.1 --review interactive         # both gates active
aec_demo --control CC6.1 --review spl-only            # interrupt at SPL only
aec_demo --control CC6.1 --review verdict-only        # interrupt at verdict only
aec_demo --control CC6.1 --resume <run_id>            # resume from last checkpoint
aec_demo list-checkpoints                              # show resumable runs
```

## Checkpointing

State persists to `.aec_cache/checkpoints/<run_id>.json` after every node completes. Resume reads the file and skips already-completed nodes. Cleanup after successful END (or `aec_demo clean` for manual).

## Files to create / modify

- `src/aec/agent/graph.py` — LangGraph definition (~200 LOC)
- `src/aec/agent/nodes.py` — node functions, one per pipeline stage (~150 LOC)
- `src/aec/agent/state.py` — `AgentState` pydantic + checkpoint serialization (~50 LOC)
- `cli/aec_demo.py` — refactor to invoke `graph.invoke(initial_state)` instead of calling functions directly
- `tests/test_graph.py` — covers:
  - Happy-path end-to-end through the graph
  - Validator rejection routes to formatter_gap (skips execution)
  - HITL interrupt with auto-approve callback
  - Checkpoint write/read/resume
  - Resume picks up at the right node after a simulated crash mid-run
  - State mutations are isolated per node (no cross-node pollution)

## Constraints

- LangGraph is already in pyproject.toml (task 010 added it)
- Don't break the existing `aec_demo` invocation — same CLI, same outputs, just plumbed through a graph
- HITL interrupt uses LangGraph's built-in `interrupt()` primitive — no custom blocking logic
- Checkpoints are JSON, not pickle (auditability)

## Definition of done

- `aec_demo --control CC6.1` produces the same 4 artifacts as before
- `--review interactive` pauses with `Approve / Edit SPL / Reject` prompt at the SPL gate
- `--review interactive` pauses with `Approve / Reject` at the verdict gate
- `--resume <run_id>` works after a manual kill (e.g., `kill -9` during MCP exec)
- Checkpoints land in `.aec_cache/checkpoints/` and are JSON-readable
- All existing tests still pass
- New `test_graph.py` tests pass

## Demo cue (20 seconds for the interactive mode)

```
$ aec_demo --control CC6.1 --review interactive

[graph] node: control_mapper       (102ms)
[graph] node: spl_generator        (1840ms)
[graph] node: spl_validator        (12ms)  → policy: pass

⏸ HITL gate: review SPL before execution

SPL: index=botsv3 sourcetype=o365:management:activity action=Login
     | stats count by user, mfa_used | where mfa_used="false"

Estimated event count (via | tstats): ~47
Estimated runtime: ~2.3s

[a]pprove / [e]dit / [r]eject: a

[graph] node: mcp_executor         (2280ms via splunk-official)
[graph] node: evidence_normalizer  (8ms)
[graph] node: panel_round_1        (28000ms — 3 personas parallel)
...
```

This is the *agentic* visual — a graph executing, pausing for review, then continuing. Reads as "real agent" to anyone who's used LangGraph before.

## Out of scope

- LangSmith integration (the trace structure is there; we don't enable cloud reporting)
- Custom UI for HITL — terminal prompt only
- Distributed checkpointing (single-process is enough)
- Conditional edges beyond the 2 already shown
