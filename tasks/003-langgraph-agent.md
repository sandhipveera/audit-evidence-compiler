# Task 003 — LangGraph agent (4-node pipeline)

**Goal:** Wire the 4 pipeline stages into a single LangGraph that takes an operator prompt and produces a list of `EvidenceRow` dicts (passed-evidence or gap-finding shape).

## Acceptance

- `python -m aec.agent.graph --framework soc2 --control CC6.1` runs end-to-end against the local Splunk container.
- Each node logs its input + output to `audit_trail.jsonl`.
- Failure in node N short-circuits to a gap finding (don't crash the pipeline).

## Graph

```
START → control_mapper → spl_generator → spl_validator → splunk_executor
                                              │
                                              └─(reject)─→ evidence_formatter (as gap)
                                                                ▲
splunk_executor → evidence_normalizer → evidence_formatter → review_gate → END
                                                                │
                                                  (auto mode skips review_gate)
```

**SPL Validator** is task 006 — runs after generation, before execution, enforces the execution policy (allowed indexes, time bounds, forbidden commands). Rejection short-circuits to a gap finding.

**Review Gate** uses LangGraph's `interrupt()`. Two modes via `--review` flag: `auto` (skip), `interactive` (pause for terminal approve/edit/reject). Demo runs auto; the README + ARCHITECTURE call out interactive as the "enterprise mode."

**Evidence Normalizer** converts raw Splunk rows into an `EvidenceSnapshot` with full provenance (control_id, spl_executed, sourcetypes_hit, row_count, execution_ts, llm_metadata). Snapshot is what gets written to `audit_trail.jsonl`. This is the provenance backbone — without it the agent's output isn't audit-defensible.

State shape (`pydantic.BaseModel`):
```python
class AgentState(BaseModel):
    framework: str               # "SOC 2"
    control_query: str           # "CC6.1"
    matched_controls: list[ControlMatch] = []
    spl_queries: list[SplQuery] = []
    raw_results: list[SplResult] = []
    evidence_rows: list[EvidenceRow] = []
    audit_trail: list[dict] = []
```

## Nodes

1. **`control_mapper`** — loads `catalog.json`, uses Claude to resolve operator's control_query (e.g., "CC6.1") to N internal controls. Prompt template in `src/aec/agent/prompts.py`. Returns `matched_controls`.
2. **`spl_generator`** — for each matched control, prompts Claude with the control's `splunk_hint.spl_skeleton` + `evidence_question` and asks it to refine into a runnable SPL targeting BOTS v3 sourcetypes. Returns `spl_queries`.
3. **`spl_validator`** — task 006. Runs each query through `validate(spl, policy)`. Rejections route to formatter as gap findings (skipping execution).
4. **`splunk_executor`** — calls `splunk_client.execute_spl()` for each validated query. Returns `raw_results`.
5. **`evidence_normalizer`** — wraps each result with provenance metadata into an `EvidenceSnapshot`; writes to `audit_trail.jsonl`.
6. **`evidence_formatter`** — converts snapshots into `EvidenceRow` (passed) or `GapFinding` (no results / validation reject / execution error; severity-scored).
7. **`review_gate`** — LangGraph interrupt; auto-bypassed unless `--review=interactive`.

## Files to create

- `src/aec/agent/__init__.py`
- `src/aec/agent/graph.py`
- `src/aec/agent/nodes.py`
- `src/aec/agent/prompts.py`
- `src/aec/models.py` (pydantic state models)
- `tests/test_agent_graph.py` (mock the Splunk client + Anthropic client)

## Out of scope

- Human-in-the-loop approval gate (LangGraph supports it; add in v2)
- Multi-control batching (do one control at a time for MVP)
- SPL repair loop on error (one shot; if SPL fails, record as gap)
